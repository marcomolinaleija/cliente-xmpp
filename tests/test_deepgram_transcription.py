from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tools.deepgram_transcription import (
    build_audio_dedup_key,
    claim_audio,
    complete_audio,
    format_audio_duration,
    format_credit_balances,
    get_credit_balances,
    handle_transcription_command,
    inspect_audio,
    release_audio,
    transcribe_audio,
    transcription_enabled,
)


class FakeResponse:
    def __init__(
        self,
        status: int,
        payload: dict[str, object] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.payload = payload or {}
        self._text = text
        self.headers = headers or {}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self.payload

    async def text(self) -> str:
        return self._text


class FakeSession:
    def __init__(
        self,
        *,
        posts: list[FakeResponse] | None = None,
        gets: list[FakeResponse] | None = None,
    ) -> None:
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = 0
        self.get_calls: list[str] = []

    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        self.post_calls += 1
        return self.posts.pop(0)

    def get(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
        self.get_calls.append(url)
        return self.gets.pop(0)


class DeepgramTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.environment = patch.dict(
            os.environ,
            {
                "DEEPGRAM_STATE_PATH": str(self.state_path),
                "DEEPGRAM_API_KEY": "transcription-test-key",
                "DEEPGRAM_OWNER_API_KEY": "owner-test-key",
                "DEEPGRAM_RETRY_ATTEMPTS": "2",
                "DEEPGRAM_TRANSCRIPTION_ENABLED": "true",
                "DEEPGRAM_SKIP_MIME_TYPES": "",
                "DEEPGRAM_ALLOWED_MIME_TYPES": "",
            },
        )
        self.environment.start()
        os.environ.pop("DEEPGRAM_PROJECT_ID", None)
        os.environ.pop("DEEPGRAM_COMMAND_JIDS", None)
        os.environ.pop("DEEPGRAM_ALLOWED_JIDS", None)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp_dir.cleanup()

    def test_toggle_and_deduplication_are_persistent(self) -> None:
        account = "Marco@Example.test"
        self.assertTrue(transcription_enabled(account))
        asyncio.run(
            handle_transcription_command(
                FakeSession(), account, "/transcribe off"  # type: ignore[arg-type]
            )
        )
        self.assertFalse(transcription_enabled(account))
        self.assertEqual(build_audio_dedup_key("abc", 1, b"data"), "abc:1")
        self.assertTrue(claim_audio(account, "abc:1"))
        self.assertFalse(claim_audio(account, "abc:1"))
        release_audio(account, "abc:1")
        self.assertTrue(claim_audio(account, "abc:1"))
        complete_audio(account, "abc:1")
        self.assertFalse(claim_audio(account, "abc:1"))

    async def test_commands_never_need_whatsapp_and_stats_formats_balance(self) -> None:
        session = FakeSession(
            gets=[
                FakeResponse(
                    200,
                    {"projects": [{"project_id": "project-test", "name": "Test"}]},
                ),
                FakeResponse(
                    200,
                    {
                        "balances": [
                            {"amount": 199.76368895, "units": "usd"},
                        ]
                    },
                ),
            ]
        )
        response = await handle_transcription_command(  # type: ignore[arg-type]
            session, "marco@example.test", "/stats"
        )
        self.assertEqual(response, "Crédito disponible: $199.76 USD.")
        self.assertEqual(session.post_calls, 0)
        self.assertEqual(len(session.get_calls), 2)

        self.assertEqual(
            await handle_transcription_command(  # type: ignore[arg-type]
                FakeSession(), "marco@example.test", "/transcribe of"
            ),
            "Transcripción desactivada.",
        )

    async def test_status_validates_api_without_transcribing(self) -> None:
        session = FakeSession(
            gets=[FakeResponse(200, {"projects": [{"project_id": "project-test"}]})]
        )
        response = await handle_transcription_command(  # type: ignore[arg-type]
            session, "marco@example.test", "/status"
        )
        self.assertEqual(
            response,
            "Servicio de transcripción configurado correctamente. Estado: activado.",
        )
        self.assertEqual(session.post_calls, 0)

    async def test_transcription_retries_retryable_http_error(self) -> None:
        session = FakeSession(
            posts=[
                FakeResponse(503, text="temporary"),
                FakeResponse(
                    200,
                    {
                        "results": {
                            "channels": [
                                {"alternatives": [{"transcript": "hola mundo"}]}
                            ]
                        }
                    },
                ),
            ]
        )
        with patch("tools.deepgram_transcription.asyncio.sleep", new=AsyncMock()):
            transcript = await transcribe_audio(  # type: ignore[arg-type]
                session,
                b"OggS-test",
                content_type="audio/ogg; codecs=opus",
            )
        self.assertEqual(transcript, "hola mundo")
        self.assertEqual(session.post_calls, 2)

    async def test_audio_policy_skips_configured_music_type_and_limits_duration(self) -> None:
        os.environ["DEEPGRAM_SKIP_MIME_TYPES"] = "audio/mpeg"
        skipped = await inspect_audio(b"mp3", "audio/mpeg", "song.mp3")
        self.assertFalse(skipped.accepted)
        self.assertIn("audio/mpeg", skipped.reason)

        os.environ["DEEPGRAM_SKIP_MIME_TYPES"] = ""
        os.environ["DEEPGRAM_MAX_DURATION_SECONDS"] = "60"
        with patch(
            "tools.deepgram_transcription.probe_audio_duration",
            new=AsyncMock(return_value=61.0),
        ):
            too_long = await inspect_audio(b"OggS", "audio/ogg", "voice.ogg")
        self.assertFalse(too_long.accepted)
        self.assertEqual(too_long.duration_seconds, 61.0)

    def test_human_formatting(self) -> None:
        self.assertEqual(format_audio_duration(16.4), "16 s")
        self.assertEqual(format_audio_duration(62.1), "1 min 2 s")
        self.assertEqual(format_audio_duration(3661), "1 h 1 min 1 s")
        self.assertEqual(
            format_credit_balances({"usd": Decimal("199.76368895")}),
            "$199.76 USD",
        )

    def test_transcription_allowlist_is_applied_per_account(self) -> None:
        os.environ["DEEPGRAM_ALLOWED_JIDS"] = "marco@example.test"
        self.assertTrue(transcription_enabled("marco@example.test"))
        self.assertFalse(transcription_enabled("someone@example.test"))

    async def test_balance_can_use_explicit_project_id(self) -> None:
        os.environ["DEEPGRAM_PROJECT_ID"] = "explicit-project"
        session = FakeSession(
            gets=[
                FakeResponse(
                    200,
                    {"balances": [{"amount": "10.25", "units": "usd"}]},
                )
            ]
        )
        balances = await get_credit_balances(session)  # type: ignore[arg-type]
        self.assertEqual(balances, {"usd": Decimal("10.25")})
        self.assertEqual(
            session.get_calls,
            ["https://api.deepgram.com/v1/projects/explicit-project/balances"],
        )


if __name__ == "__main__":
    unittest.main()
