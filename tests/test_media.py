from __future__ import annotations

import unittest
from types import SimpleNamespace

from cliente_xmpp.media.downloads import media_description
from cliente_xmpp.models.chat import Message
from cliente_xmpp.ui.conversation_panel import ConversationPanel


class MediaDescriptionTests(unittest.TestCase):
    def test_hides_bridge_hash_for_video_note(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            media_url="https://example.test/a72c8a794c297abf149839cc92675eac15645f78082cb9906aa0b52918bcd745.mp4",
            media_kind="video",
            media_filename="a72c8a794c297abf149839cc92675eac15645f78082cb9906aa0b52918bcd745.mp4",
            media_size=464754,
        )

        self.assertEqual(media_description(message), "Nota de video, 453.9 KB")

    def test_keeps_human_video_filename(self) -> None:
        message = Message(
            chat_jid="contact@example.test",
            sender_jid="contact@example.test",
            body="",
            media_url="https://example.test/cumpleanos.mp4",
            media_kind="video",
            media_filename="cumpleanos.mp4",
            media_size=1024,
        )

        self.assertEqual(media_description(message), "video, cumpleanos.mp4, 1.0 KB")


class RecordingControlsTests(unittest.TestCase):
    def test_view_once_checkbox_only_shows_during_recording_and_resets(self) -> None:
        class Control:
            def __init__(self) -> None:
                self.enabled: bool | None = None
                self.visible: bool | None = None
                self.value = False

            def Enable(self, value: bool) -> None:
                self.enabled = value

            def Show(self, value: bool) -> None:
                self.visible = value

            def SetValue(self, value: bool) -> None:
                self.value = value

        compose = Control()
        attach = Control()
        pause = Control()
        cancel = Control()
        view_once = Control()
        panel = SimpleNamespace(
            compose=compose,
            attach_button=attach,
            pause_recording_button=pause,
            cancel_recording_button=cancel,
            view_once_audio=view_once,
            update_send_button_state=lambda *_args: None,
            Layout=lambda: None,
        )

        ConversationPanel.set_recording_state(panel, True)
        self.assertTrue(view_once.visible)
        self.assertFalse(view_once.value)

        view_once.value = True
        ConversationPanel.set_recording_state(panel, False)
        self.assertFalse(view_once.visible)
        self.assertFalse(view_once.value)


if __name__ == "__main__":
    unittest.main()
