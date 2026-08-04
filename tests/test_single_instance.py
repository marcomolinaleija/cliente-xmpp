from __future__ import annotations

import sys
import unittest
import uuid

from cliente_xmpp.app.single_instance import SingleInstanceGuard


@unittest.skipUnless(sys.platform == "win32", "La coordinación usa primitivas de Windows.")
class SingleInstanceGuardTests(unittest.TestCase):
    def test_second_guard_activates_the_first_guard(self) -> None:
        token = uuid.uuid4().hex
        mutex_name = rf"Local\WhatsAppCAN.Test.{token}"
        event_name = rf"Local\WhatsAppCAN.TestActivate.{token}"
        first = SingleInstanceGuard(mutex_name, event_name)
        second = SingleInstanceGuard(mutex_name, event_name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertTrue(second.request_activation())
            self.assertTrue(first.consume_activation_request())
            self.assertFalse(first.consume_activation_request())
        finally:
            second.close()
            first.close()
