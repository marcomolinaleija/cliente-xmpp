from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch Slidge core so XEP-0372 mention references become WhatsApp MentionedJID values "
            "in slidge-whatsapp."
        )
    )
    parser.add_argument(
        "slidge_source_tree",
        type=Path,
        help="Path to the root of a Slidge source checkout used to build the bridge.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files before modifying sources.",
    )
    args = parser.parse_args()

    source_tree = args.slidge_source_tree.resolve()
    room_py = source_tree / "slidge" / "group" / "room.py"
    dispatcher_py = source_tree / "slidge" / "core" / "dispatcher" / "message" / "message.py"
    for path in (room_py, dispatcher_py):
        if not path.is_file():
            raise SystemExit(f"Expected Slidge source file not found: {path}")

    changed = False
    changed |= patch_dispatcher(dispatcher_py, backup=not args.no_backup)
    changed |= patch_room(room_py, backup=not args.no_backup)
    if changed:
        print("Patch applied. Rebuild the custom slidge-whatsapp image before deploying it.")
    else:
        print("Patch already present; no files changed.")
    return 0


def patch_dispatcher(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    if "recipient.parse_mentions(body, msg.xml)" in text:
        return False

    updated = replace_once(
        text,
        "mentions = tuple(await recipient.parse_mentions(body))",
        "mentions = tuple(await recipient.parse_mentions(body, msg.xml))",
        path,
    )
    write_text(path, updated, backup=backup)
    return True


def patch_room(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    if "message_xml: ET.Element | None" in text:
        return False

    old = '''    async def parse_mentions(self, text: str | None) -> tuple[Mention, ...]:
        if not text:
            return ()
        with self.xmpp.store.session() as orm:
            await self.__fill_participants()
            orm.add(self.stored)
            participants = {
                p.nickname: p for p in self.stored.participants if len(p.nickname) > 1
            }

            if len(participants) == 0:
                return ()

            result = []
            for match in re.finditer(
                "|".join(
                    sorted(
                        [re.escape(nick) for nick in participants],
                        key=lambda nick: len(nick),
                        reverse=True,
                    )
                ),
                text,
            ):
                span = match.span()
                nick = match.group()
                if span[0] != 0 and text[span[0] - 1] not in _WHITESPACE_OR_PUNCTUATION:
                    continue
                if span[1] == len(text) or text[span[1]] in _WHITESPACE_OR_PUNCTUATION:
                    participant = self.participant_from_store(
                        stored=participants[nick],
                    )
                    if contact := participant.contact:
                        result.append(
                            Mention(contact=contact, start=span[0], end=span[1])
                        )
        return tuple(result)
'''
    new = '''    async def parse_mentions(
        self,
        text: str | None,
        message_xml: ET.Element | None = None,
    ) -> tuple[Mention, ...]:
        if not text:
            return ()
        with self.xmpp.store.session() as orm:
            await self.__fill_participants()
            orm.add(self.stored)
            participants = {{
                p.nickname: p for p in self.stored.participants if len(p.nickname) > 1
            }}

            if message_xml is not None:
                explicit_mentions = []
                for reference in message_xml.findall(".//{urn:xmpp:reference:0}reference"):
                    if reference.attrib.get("type") != "mention":
                        continue
                    try:
                        start = int(reference.attrib["begin"])
                        end = int(reference.attrib["end"])
                    except (KeyError, ValueError):
                        continue
                    if start < 0 or end <= start or end > len(text):
                        continue
                    mentioned_jid = reference.attrib.get("uri", "").removeprefix("xmpp:")
                    mentioned_jid = mentioned_jid.split("/", 1)[0]
                    for stored in self.stored.participants:
                        participant = self.participant_from_store(stored=stored)
                        if (
                            participant.contact
                            and str(participant.contact.jid.bare) == mentioned_jid
                        ):
                            explicit_mentions.append(
                                Mention(contact=participant.contact, start=start, end=end)
                            )
                            break
                if explicit_mentions:
                    return tuple(explicit_mentions)

            if len(participants) == 0:
                return ()

            result = []
            for match in re.finditer(
                "|".join(
                    sorted(
                        [re.escape(nick) for nick in participants],
                        key=lambda nick: len(nick),
                        reverse=True,
                    )
                ),
                text,
            ):
                span = match.span()
                nick = match.group()
                if span[0] != 0 and text[span[0] - 1] not in _WHITESPACE_OR_PUNCTUATION:
                    continue
                if span[1] == len(text) or text[span[1]] in _WHITESPACE_OR_PUNCTUATION:
                    participant = self.participant_from_store(
                        stored=participants[nick],
                    )
                    if contact := participant.contact:
                        result.append(
                            Mention(contact=contact, start=span[0], end=span[1])
                        )
        return tuple(result)
'''
    updated = replace_once(text, old, new, path)
    write_text(path, updated, backup=backup)
    return True


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Could not apply patch to {path}: expected one match, found {count}.")
    return text.replace(old, new, 1)


def write_text(path: Path, text: str, *, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {path}")


if __name__ == "__main__":
    raise SystemExit(main())
