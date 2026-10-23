"""Move group echo archive commits off the gateway loop, without sharing ORM objects."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path


def archive_source(source: str) -> str:
    if "# CAN nonblocking group echo archive v1" in source:
        return source
    start = source.index("    def add(\n")
    end = source.index("    def __iter__(", start)
    original = source[start:end]
    split = "        with self.__store.session(expire_on_commit=False) as orm:\n"
    if original.count(split) != 1 or source.count("    def add(\n") != 1:
        raise ValueError("Expected exactly one archive-add transaction")
    prepare, transaction = original.split(split)
    anchor = "        if not archivable(msg):\n            return\n"
    if prepare.count(anchor) != 1 or "HistoryMessage(new_msg)," not in transaction:
        raise ValueError("Unsupported archive preparation")
    signature = prepare[:prepare.index('        """')]
    body = prepare[prepare.index("        if not archivable(msg):"):]
    helper = signature.replace("def add(", "def _prepare_add(").replace(
        ") -> None:", ") -> tuple[HistoryMessage, str | None] | None:"
    ) + body + "        return HistoryMessage(new_msg), legacy_msg_id\n\n"
    sync = signature + '''        prepared = self._prepare_add(
            msg, participant, archive_only, legacy_msg_id,
        )
        if prepared is None:
            return
        history, legacy_msg_id = prepared
''' + split + transaction.replace("HistoryMessage(new_msg),", "history,")
    asynchronous = '''    # CAN nonblocking group echo archive v1
    async def add_async(
        self, msg: Message, participant: "AnyParticipant | None" = None,
        archive_only: bool = False, legacy_msg_id: str | None = None,
    ) -> None:
        # Prepare mutable stanza/participant data only on the owner loop.
        prepared = self._prepare_add(msg, participant, archive_only, legacy_msg_id)
        if prepared is None:
            return
        history, legacy_msg_id = prepared
        xml, when, stanza_id = str(history.stanza), history.when, history.id
        room_pk = self.room.id
        account_pk, room_legacy_id = self.room.user_account_id, self.room.legacy_id
        store, rooms = self.__store, self.__rooms_store

        def commit():
            # Only immutable values and stateless stores enter the worker.
            # Each transaction creates/closes its own session, including on failure.
            with store.session(expire_on_commit=False) as orm:
                pk = room_pk
                if pk is None:
                    pk = rooms.get(orm, account_pk, room_legacy_id).id
                message = HistoryMessage(xml, when=when)
                message.id = stanza_id
                store.add_message(orm, pk, message, archive_only,
                                  None if legacy_msg_id is None else str(legacy_msg_id))
                orm.commit()

        task = asyncio.create_task(asyncio.to_thread(commit))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release the caller's room lock while SQL is still running.
            await asyncio.gather(task, return_exceptions=True)
            raise

'''
    result = "import asyncio\n" + source[:start] + helper + sync + asynchronous + source[end:]
    ast.parse(result)
    return result


def room_source(source: str) -> str:
    if "# CAN ordered nonblocking group echoes v1" in source:
        return source
    if source.count("    async def echo(") != 1:
        raise ValueError("Expected exactly one group echo method")
    start = source.index("    async def echo(")
    end = source.index("    def _post_avatar_update(", start)
    original = source[start:end]
    anchor = "        self.archive.add(msg, user_part)\n"
    if original.count(anchor) != 1:
        raise ValueError("Expected exactly one group echo archive call")
    head, tail = original.split(anchor)
    tail = tail.replace("\n\n", "\n", 1).rstrip() + "\n\n"
    replacement = head + '''        # CAN ordered nonblocking group echoes v1
        # Keep archive-before-send and per-room order across the new await.
        async with self.session.lock(("can-group-echo", str(self.jid))):
            await self.archive.add_async(msg, user_part)
''' + "\n".join("    " + line if line else "" for line in tail.split("\n"))
    result = source[:start] + replacement + source[end:]
    ast.parse(result)
    return result


def patch_package(site_packages: Path) -> bool:
    outputs = {}
    for name, transform in (("slidge/group/archive.py", archive_source),
                            ("slidge/group/room.py", room_source)):
        path = site_packages / name
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if new != old:
            outputs[path] = new
    # Validate every source before modifying either installed file.
    for path, source in outputs.items():
        path.write_text(source, encoding="utf-8", newline="\n")
    return bool(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    print("Group echo patch applied" if patch_package(parser.parse_args().site_packages)
          else "Already patched")
