from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_whatsapp_mentions import patch_dispatcher, patch_room

OLD_PARSE_MENTIONS = '''    async def parse_mentions(self, text: str | None) -> tuple[Mention, ...]:
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


class PatchSlidgeMentionsTests(unittest.TestCase):
    def test_patch_uses_a_dict_comprehension_for_participants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            room_path = Path(temp_dir) / "room.py"
            room_path.write_text("class Room:\n" + OLD_PARSE_MENTIONS, encoding="utf-8")

            self.assertTrue(patch_room(room_path, backup=False))

            module = ast.parse(room_path.read_text(encoding="utf-8"))
            parse_mentions = next(
                node
                for node in ast.walk(module)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "parse_mentions"
            )
            participants_assignment = next(
                node
                for node in ast.walk(parse_mentions)
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "participants"
                    for target in node.targets
                )
            )

            self.assertIsInstance(participants_assignment.value, ast.DictComp)
            self.assertIn("explicit_mentions", room_path.read_text(encoding="utf-8"))
            self.assertFalse(patch_room(room_path, backup=False))

    def test_dispatcher_passes_the_message_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dispatcher_path = Path(temp_dir) / "message.py"
            dispatcher_path.write_text(
                "mentions = tuple(await recipient.parse_mentions(body))\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_dispatcher(dispatcher_path, backup=False))

            updated = dispatcher_path.read_text(encoding="utf-8")
            self.assertIn("recipient.parse_mentions(body, msg.xml)", updated)
            self.assertFalse(patch_dispatcher(dispatcher_path, backup=False))


if __name__ == "__main__":
    unittest.main()
