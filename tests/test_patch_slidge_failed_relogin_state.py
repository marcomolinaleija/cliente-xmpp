from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from tools.patch_slidge_failed_relogin_state import patch_login, patch_package


class FailedReloginStatePatchTests(unittest.TestCase):
    @staticmethod
    def _source() -> str:
        return '''class Login:
    async def run(self, session, _ifrom):
        msg = await self.xmpp.login_wrap(session)
        session.logged = True
        session.send_gateway_status(msg or "Re-connected", show="chat")
        session.send_gateway_message(msg or "Re-connected")
        return msg
'''

    def test_failed_relogin_does_not_claim_connected(self) -> None:
        namespace: dict[str, object] = {}
        exec(patch_login(self._source()), namespace)
        login_type = namespace["Login"]

        class Session:
            logged = False
            statuses: list[str] = []
            messages: list[str] = []

            def send_gateway_status(self, text: str, *, show: str) -> None:
                self.statuses.append(f"{show}:{text}")

            def send_gateway_message(self, text: str) -> None:
                self.messages.append(text)

        class Gateway:
            async def login_wrap(self, session: Session) -> str:
                session.logged = False
                return "You are not connected to this gateway"

        login = login_type()
        login.xmpp = Gateway()
        session = Session()
        result = asyncio.run(login.run(session, None))
        self.assertIn("not connected", result)
        self.assertFalse(session.logged)
        self.assertEqual(session.statuses, [])
        self.assertEqual(session.messages, [])

    def test_successful_relogin_keeps_status_notification(self) -> None:
        namespace: dict[str, object] = {}
        exec(patch_login(self._source()), namespace)
        login_type = namespace["Login"]

        class Session:
            logged = False

            def __init__(self) -> None:
                self.statuses: list[str] = []
                self.messages: list[str] = []

            def send_gateway_status(self, text: str, *, show: str) -> None:
                self.statuses.append(f"{show}:{text}")

            def send_gateway_message(self, text: str) -> None:
                self.messages.append(text)

        class Gateway:
            async def login_wrap(self, session: Session) -> str:
                session.logged = True
                return "Connected"

        login = login_type()
        login.xmpp = Gateway()
        session = Session()
        self.assertEqual(asyncio.run(login.run(session, None)), "Connected")
        self.assertTrue(session.logged)
        self.assertEqual(session.statuses, ["chat:Connected"])
        self.assertEqual(session.messages, ["Connected"])

    def test_patch_is_idempotent_and_refuses_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "slidge" / "command" / "user.py"
            path.parent.mkdir(parents=True)
            path.write_text(self._source(), encoding="utf-8")
            self.assertTrue(patch_package(Path(temp_dir), backup=False))
            self.assertFalse(patch_package(Path(temp_dir), backup=False))
            self.assertEqual(path.read_text(encoding="utf-8"), patch_login(self._source()))
        with self.assertRaises(SystemExit):
            patch_login("class Login: pass\n")


if __name__ == "__main__":
    unittest.main()
