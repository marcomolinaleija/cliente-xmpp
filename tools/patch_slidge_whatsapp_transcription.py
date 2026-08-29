from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SESSION_IMPORT = '''import time
from .deepgram_transcription import (
    build_audio_dedup_key,
    claim_audio,
    complete_audio,
    format_audio_duration,
    inspect_audio,
    is_audio_attachment,
    release_audio,
    transcription_enabled,
    transcribe_audio,
)
'''
SESSION_IMPORT_ANCHOR = "from .group import MUC, Bookmarks, Participant\n"

OLD_ATTACHMENT_METHOD = '''    async def on_wa_msg_attachment(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        attachments = await Attachment.convert_list(message.Attachments, muc)
        await actor.send_files(
            attachments=attachments,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            is_forwarded=message.IsForwarded,
        )
'''

NEW_ATTACHMENT_METHOD = '''    async def on_wa_msg_attachment(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        attachments = await Attachment.convert_list(message.Attachments, muc)
        transcription_jobs = []
        if not message.IsHistory and not message.Actor.IsMe:
            for index, attachment in enumerate(attachments):
                if is_audio_attachment(attachment):
                    data = bytes(attachment.data or b"")
                    transcription_jobs.append(
                        (
                            build_audio_dedup_key(message.ID, index, data),
                            data,
                            str(attachment.content_type or ""),
                            str(attachment.name or ""),
                        )
                    )

        await actor.send_files(
            attachments=attachments,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            is_forwarded=message.IsForwarded,
        )
        account = str(self.user_jid.bare)
        for dedupe_key, data, content_type, filename in transcription_jobs:
            if data and transcription_enabled(account):
                self.create_task(
                    self.__transcribe_and_send(
                        actor,
                        account,
                        dedupe_key,
                        data,
                        content_type,
                        filename,
                    ),
                    name="deepgram-transcription",
                )
'''

SESSION_METHOD_ANCHOR = '''    async def on_wa_msg_edit(
'''

TRANSCRIPTION_METHOD = '''    async def __transcribe_and_send(
        self,
        actor: Contact | Participant,
        account: str,
        dedupe_key: str,
        data: bytes,
        content_type: str,
        filename: str,
    ) -> None:
        if not claim_audio(account, dedupe_key):
            self.log.info("Skipping duplicate audio transcription: %s", dedupe_key)
            return
        try:
            inspection = await inspect_audio(data, content_type, filename)
            if not inspection.accepted:
                self.log.info(
                    "Skipping Deepgram transcription for %s: %s",
                    filename or "audio",
                    inspection.reason,
                )
                complete_audio(account, dedupe_key)
                return
            started_at = time.perf_counter()
            transcript = await transcribe_audio(
                self.http,
                data,
                content_type=content_type,
                filename=filename,
            )
            elapsed = time.perf_counter() - started_at
            complete_audio(account, dedupe_key)
        except Exception as exc:
            release_audio(account, dedupe_key)
            self.log.warning(
                "Deepgram transcription failed for %s (%d bytes): %s",
                filename or "audio",
                len(data),
                exc,
            )
            return
        if transcript:
            duration = (
                f" Audio: {format_audio_duration(inspection.duration_seconds)}."
                if inspection.duration_seconds is not None
                else ""
            )
            actor.send_text(
                f'Transcripción: "{transcript}".{duration} '
                f"Transcrito en {elapsed:.1f} s."
            )

'''

MIXINS_IMPORT_ANCHOR = "from .generated import go, whatsapp\n"
MIXINS_IMPORT = (
    "from .deepgram_transcription import handle_transcription_command\n"
)
MIXINS_TEXT_ANCHOR = '''        assert xmpp_msg.body
        message_id: str = self.wa.GenerateMessageID()  # type:ignore[no-untyped-call]
'''
MIXINS_TEXT_REPLACEMENT = '''        assert xmpp_msg.body
        command_response = await handle_transcription_command(
            self.session.http,
            str(self.session.user_jid.bare),
            xmpp_msg.body,
        )
        if command_response is not None:
            local_sender = getattr(self, "send_text", None)
            if local_sender is not None:
                local_sender(command_response)
            else:
                participant = await self.get_user_participant()
                participant.send_text(command_response)
            return self.wa.GenerateMessageID()  # type:ignore[no-untyped-call,no-any-return]
        message_id: str = self.wa.GenerateMessageID()  # type:ignore[no-untyped-call]
'''


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not patch {description}: expected one match, found {count}."
        )
    return text.replace(old, new, 1)


def write(path: Path, text: str, *, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-transcription")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    marker = "Skipping duplicate audio transcription"
    if marker in source:
        return False
    source = replace_once(
        source,
        SESSION_IMPORT_ANCHOR,
        SESSION_IMPORT_ANCHOR + SESSION_IMPORT,
        "slidge_whatsapp session import",
    )
    source = replace_once(
        source,
        OLD_ATTACHMENT_METHOD,
        NEW_ATTACHMENT_METHOD,
        "slidge_whatsapp attachment method",
    )
    source = replace_once(
        source,
        SESSION_METHOD_ANCHOR,
        TRANSCRIPTION_METHOD + SESSION_METHOD_ANCHOR,
        "slidge_whatsapp transcription method",
    )
    write(path, source, backup=backup)
    return True


def patch_mixins(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    marker = "handle_transcription_command("
    if marker in source:
        return False
    source = replace_once(
        source,
        MIXINS_IMPORT_ANCHOR,
        MIXINS_IMPORT_ANCHOR + MIXINS_IMPORT,
        "slidge_whatsapp command import",
    )
    source = replace_once(
        source,
        MIXINS_TEXT_ANCHOR,
        MIXINS_TEXT_REPLACEMENT,
        "slidge_whatsapp outgoing text command hook",
    )
    write(path, source, backup=backup)
    return True


def patch_package(site_packages: Path, *, backup: bool) -> bool:
    package_dir = site_packages.resolve() / "slidge_whatsapp"
    session_path = package_dir / "session.py"
    mixins_path = package_dir / "mixins.py"
    missing = [str(path) for path in (session_path, mixins_path) if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changed_session = patch_session(session_path, backup=backup)
    changed_mixins = patch_mixins(mixins_path, backup=backup)
    return changed_session or changed_mixins


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add optional Deepgram transcription to slidge-whatsapp."
    )
    parser.add_argument(
        "site_packages",
        type=Path,
        help="Path containing the installed slidge_whatsapp package.",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print(
        "Optional Deepgram transcription patch applied."
        if changed
        else "Optional Deepgram transcription patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
