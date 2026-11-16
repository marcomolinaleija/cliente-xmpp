"""Close archive readers before yielding and defer participant SQL without changing its API."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path


def archive_source(source: str) -> str:
    if "# CAN detached archive reads v2" in source:
        return source
    if source.count("    def get_all(\n") != 1:
        raise ValueError("Expected exactly one archive reader")
    start = source.index("    def get_all(\n")
    end = source.index("    async def send_metadata(", start)
    method = source[start:end]
    anchor = "            yield from self.__store.get_messages(\n"
    if method.count(anchor) != 1 or not method.endswith("            )\n\n"):
        raise ValueError("Unsupported archive reader")
    method = method.replace(anchor, "            messages = list(self.__store.get_messages(\n")
    method = method[:-len("            )\n\n")] + (
        "            ))\n"
        "        # CAN detached archive reads v2\n"
        "        # HistoryMessage holds XML/scalars, never lazy ORM objects.\n"
        "        yield from messages\n\n"
    )
    result = source[:start] + method + source[end:]
    ast.parse(result)
    return result


def participant_source(source: str) -> str:
    if "# CAN ordered participant archive delivery v2" in source:
        return source
    anchor = "                self.muc.archive.add(stanza, self, archive_only, legacy_msg_id)\n"
    if source.count(anchor) != 1 or source.count("    def mucadmin_item(") != 1:
        raise ValueError("Unsupported participant archive delivery")
    replacement = '''                # CAN ordered participant archive delivery v2
                # Snapshot the stanza now; the synchronous public API still returns it.
                snapshot = copy(stanza)
                archive = self.muc.archive
                pending = getattr(archive, "_can_pending_deliveries", None)
                if pending is None:
                    pending = archive._can_pending_deliveries = set()
                task = self.xmpp.loop.create_task(
                    self._can_archive_and_send(snapshot, archive_only, legacy_msg_id)
                )
                pending.add(task)
                from slidge.can_archive_delivery import track
                track(task)
                task.add_done_callback(pending.discard)
                return stanza
'''
    helper = '''    async def _can_archive_and_send(self, stanza, archive_only, legacy_msg_id):
        # Share the R6 echo lock so receipts, incoming messages and own echoes
        # keep archive-before-send ordering, even when SQL waits for a reader.
        try:
            async with self.session.lock(("can-group-echo", str(self.muc.jid))):
                await self.muc.archive.add_async(stanza, self, archive_only, legacy_msg_id)
                if not archive_only:
                    for full_jid in self.muc.user_full_jids():
                        outgoing = copy(stanza)
                        outgoing["to"] = full_jid
                        self.__send_presence_if_needed(outgoing, full_jid, False)
                        outgoing.send()
        except Exception as exc:
            # No resending on uncertain outcomes. Surface a durable local failure
            # to the bounded functional watchdog, without logging message content.
            self.xmpp._can_archive_failed = True
            self.xmpp.log.error("CAN archive delivery failed: %s", type(exc).__name__)
            raise

'''
    result = source.replace(anchor, replacement).replace("    def mucadmin_item(",
                                                          helper + "    def mucadmin_item(")
    ast.parse(result)
    return result


def gateway_source(source: str) -> str:
    if "# CAN functional healthcheck v1" in source:
        return source
    anchor = "        self.loop.set_exception_handler(self.__exception_handler)\n"
    if source.count(anchor) != 1:
        raise ValueError("Unsupported gateway initialization")
    result = source.replace(anchor, anchor + '''        # CAN functional healthcheck v1
        from slidge.can_health import install
        install(self)
''')
    ast.parse(result)
    return result


def room_source(source: str) -> str:
    if "# CAN allow queued participant deliveries v2" in source:
        return source
    anchor = '        async with self.session.lock(("can-group-echo", str(self.jid))):\n'
    if source.count(anchor) != 1:
        raise ValueError("Unsupported R6 group echo lock")
    result = source.replace(anchor, '''        # CAN allow queued participant deliveries v2
        # Let earlier synchronous _send calls enter the same FIFO room lock.
        await asyncio.sleep(0)
''' + anchor)
    if "import asyncio\n" not in source:
        result = "import asyncio\n" + result
    ast.parse(result)
    return result


def session_source(source: str) -> str:
    if "# CAN native callback archive completion v2" in source:
        return source
    methods = ("on_wa_msg_plain", "on_wa_msg_attachment", "on_wa_msg_edit",
               "on_wa_msg_revoke", "on_wa_msg_reaction", "on_wa_msg_poll",
               "on_wa_receipt", "__transcribe_and_send")
    for name in methods:
        anchor = f"    async def {name}("
        if source.count(anchor) != 1:
            raise ValueError(f"Unsupported native handler: {name}")
        source = source.replace(anchor, "    @delivery_scope\n" + anchor)
    anchor = "import asyncio\n"
    if source.count(anchor) != 1:
        raise ValueError("Unsupported native session imports")
    source = source.replace(anchor, anchor + '''# CAN native callback archive completion v2
from slidge.can_archive_delivery import delivery_scope
''')
    ast.parse(source)
    return source


def patch_package(site: Path) -> bool:
    outputs = {}
    for name, transform in (("slidge/group/archive.py", archive_source),
                            ("slidge/group/participant.py", participant_source),
                            ("slidge/group/room.py", room_source),
                            ("slidge_whatsapp/session.py", session_source),
                            ("slidge/core/gateway.py", gateway_source)):
        path = site / name
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if new != old:
            outputs[path] = new
    for path, source in outputs.items():
        path.write_text(source, encoding="utf-8", newline="\n")
    return bool(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    print("Archive delivery patch applied" if patch_package(parser.parse_args().site_packages)
          else "Already patched")
