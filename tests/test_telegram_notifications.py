from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from tools.ci import notify_telegram as telegram


class TelegramNotificationTests(unittest.TestCase):
    def environment(self):
        return {
            "TELEGRAM_BOT_TOKEN": "123:fixture-token",
            "TELEGRAM_CHAT_IDS": "101,202", "RELEASE_TAG": "v1.2.4",
            "BUILD_RESULT": "success", "PUBLISH_RESULT": "success",
            "RELEASE_URL": "https://example.test/releases/v1.2.4",
            "RUN_URL": "https://example.test/runs/fixture",
        }

    def test_two_recipients_receive_the_same_result_without_duplicate_ids(self):
        environment = self.environment()
        environment["TELEGRAM_CHAT_IDS"] = "101,202\n101"
        with patch.object(telegram, "send_message") as send, redirect_stdout(io.StringIO()):
            self.assertEqual(telegram.notify(environment), 0)
        self.assertEqual([call.args[1] for call in send.call_args_list], ["101", "202"])
        self.assertEqual(send.call_args_list[0].args[2], send.call_args_list[1].args[2])
        self.assertIn("actualización publicada", send.call_args_list[0].args[2])
        self.assertIn(environment["RELEASE_URL"], send.call_args_list[0].args[2])

    def test_manual_probe_is_labeled_as_a_test_and_requires_configured_secrets(self):
        environment = {**self.environment(), "TELEGRAM_NOTIFICATION_TEST": "1"}
        with patch.object(telegram, "send_message") as send, redirect_stdout(io.StringIO()):
            self.assertEqual(telegram.notify(environment), 0)
            with self.assertRaises(ValueError):
                telegram.notify({"TELEGRAM_NOTIFICATION_TEST": "1"})
        self.assertEqual(send.call_count, 2)
        text = send.call_args_list[0].args[2]
        self.assertIn("Prueba de notificaciones", text)
        self.assertNotIn("actualización publicada", text)
        self.assertNotIn(environment["RELEASE_URL"], text)

    def test_failure_and_cancellation_messages_link_to_logs_without_promising_a_release(self):
        for build, publish, word in (("failure", "skipped", "compilación"),
                                     ("success", "failure", "publicación"),
                                     ("cancelled", "skipped", "cancelada")):
            with self.subTest(build=build, publish=publish):
                environment = {**self.environment(), "BUILD_RESULT": build,
                               "PUBLISH_RESULT": publish}
                text = telegram.notification_text(environment)
                self.assertIn(word, text)
                self.assertIn(environment["RUN_URL"], text)
                self.assertNotIn(environment["RELEASE_URL"], text)

    def test_bad_configuration_fails_without_any_request_and_missing_secrets_skip(self):
        with patch.object(telegram, "send_message") as send, redirect_stdout(io.StringIO()):
            self.assertEqual(telegram.notify({}), 0)
            variants = [
                {"TELEGRAM_BOT_TOKEN": "123:fixture-token"},
                {"TELEGRAM_CHAT_IDS": "101,202"},
                {**self.environment(), "TELEGRAM_CHAT_IDS": "101,invalid"},
                {**self.environment(), "TELEGRAM_CHAT_IDS": "0"},
            ]
            for environment in variants:
                with self.subTest(environment=environment), self.assertRaises(ValueError):
                    telegram.notify(environment)
            send.assert_not_called()
        self.assertEqual(telegram.chat_ids("-101, 202\n-101"), ["-101", "202"])

    def test_one_failed_chat_does_not_block_the_other_or_expose_secrets(self):
        environment = self.environment()
        error = HTTPError("https://api.telegram.org/bot123:fixture-token/sendMessage", 403,
                          "fixture-secret-body", {}, None)
        output = io.StringIO()
        with patch.object(telegram, "send_message", side_effect=[error, None]) as send, \
                patch.dict(telegram.os.environ, environment, clear=True), redirect_stdout(output):
            self.assertEqual(telegram.main(), 1)
        self.assertEqual(send.call_count, 2)
        self.assertIn("1 de 2", output.getvalue())
        for secret in ("fixture-token", "101", "202", "fixture-secret-body"):
            self.assertNotIn(secret, output.getvalue())

    def test_api_payload_uses_json_and_requires_confirmation(self):
        response = MagicMock()
        response.read.return_value = b'{"ok":true}'
        with patch.object(telegram, "urlopen") as open_request:
            open_request.return_value.__enter__.return_value = response
            telegram.send_message("123:fixture-token", "101", "Fixture message")
            request = open_request.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(json.loads(request.data)["chat_id"], "101")
            self.assertEqual(json.loads(request.data)["text"], "Fixture message")
            self.assertEqual(open_request.call_args.kwargs["timeout"], 15)
            response.read.return_value = b'{"ok":false}'
            with self.assertRaises(RuntimeError):
                telegram.send_message("123:fixture-token", "101", "Fixture message")


if __name__ == "__main__":
    unittest.main()
