from __future__ import annotations

import ast
import unittest

from tools.patch_slidge_number_change import patch_group, patch_room, patch_session, replace_once


class NumberChangePatchTests(unittest.TestCase):
    def test_group_uses_authoritative_self_id_not_nickname(self) -> None:
        source = '''class Group:
    async def get(self, actor):
        if actor.IsMe:
            return await self.get_user_participant(occupant_id=actor.LID)
'''
        updated = patch_group(source)
        ast.parse(updated)
        self.assertIn("self.session._can_own_occupant_id = actor.LID", updated)
        self.assertEqual(patch_group(updated), updated)

    def test_room_does_not_pick_first_historical_alias(self) -> None:
        source = '''class Room:
    async def get_user_participant(self, occupant_id=None):
        p = await self.get_participant(
            self.user_nick,
            is_user=True,
            occupant_id=occupant_id,
        )
        self.__store_participant(p)
        return p

    def __store_participant(self, p):
        pass
'''
        updated = patch_room(source)
        ast.parse(updated)
        self.assertIn('occupant_id = occupant_id or "slidge-user"', updated)
        self.assertIn("if len(own_ids) == 1:", updated)
        self.assertNotIn(".first()", updated)
        self.assertNotIn(".delete(", updated)
        self.assertEqual(patch_room(updated), updated)

    def test_session_refreshes_identity_before_completed_future_branch(self) -> None:
        source = '''class Session:
    async def on_wa_connect(self, connect):
        # On re-pair, Session.login() is not called by slidge core, so the status message is
        # not updated.
        if self.__connected.done():
            pass
'''
        updated = patch_session(source)
        ast.parse(updated)
        self.assertLess(updated.index("self.contacts.user_legacy_id ="),
                        updated.index("if self.__connected.done()"))
        self.assertEqual(patch_session(updated), updated)

    def test_unknown_or_ambiguous_anchors_fail_closed(self) -> None:
        for source in ("unknown", "anchor anchor"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                replace_once(source, "anchor", "replacement")


if __name__ == "__main__":
    unittest.main()
