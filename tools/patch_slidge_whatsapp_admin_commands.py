from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OLD_COMMAND_HOOK = '''        command_response = await handle_transcription_command(
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
'''

NEW_COMMAND_HOOK = '''        command_response = await handle_transcription_command(
            self.session.http,
            str(self.session.user_jid.bare),
            xmpp_msg.body,
            chat=str(self.jid.bare),
        )
        if command_response is not None:
            self.session.send_gateway_message(command_response)
            return self.wa.GenerateMessageID()  # type:ignore[no-untyped-call,no-any-return]
'''

OLD_TRANSCRIPTION_CHECK = '''        account = str(self.user_jid.bare)
        for dedupe_key, data, content_type, filename in transcription_jobs:
            if data and transcription_enabled(account):
'''

NEW_TRANSCRIPTION_CHECK = '''        account = str(self.user_jid.bare)
        chat = str(muc.jid.bare if muc is not None else actor.jid.bare)
        for dedupe_key, data, content_type, filename in transcription_jobs:
            if data and transcription_enabled(account, chat):
'''


def patch_mixins(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW_COMMAND_HOOK in source:
        return False

    count = source.count(OLD_COMMAND_HOOK)
    if count != 1:
        raise SystemExit(
            "Could not patch administrative command responses: "
            f"expected one v23 command hook, found {count}."
        )

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-admin-commands")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    path.write_text(
        source.replace(OLD_COMMAND_HOOK, NEW_COMMAND_HOOK, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def patch_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if NEW_TRANSCRIPTION_CHECK in source:
        return False

    count = source.count(OLD_TRANSCRIPTION_CHECK)
    if count != 1:
        raise SystemExit(
            "Could not patch per-chat transcription state: "
            f"expected one v23 transcription check, found {count}."
        )

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-chat-transcription")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    path.write_text(
        source.replace(OLD_TRANSCRIPTION_CHECK, NEW_TRANSCRIPTION_CHECK, 1),
        encoding="utf-8",
        newline="\n",
    )
    return True


def patch_package(site_packages: Path, *, backup: bool) -> bool:
    package = site_packages.resolve() / "slidge_whatsapp"
    mixins_path = package / "mixins.py"
    session_path = package / "session.py"
    missing = [str(path) for path in (mixins_path, session_path) if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changed_mixins = patch_mixins(mixins_path, backup=backup)
    changed_session = patch_session(session_path, backup=backup)
    return changed_mixins or changed_session


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add administrative and per-chat transcription controls to slidge-whatsapp."
        )
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
        "Administrative per-chat transcription controls patch applied."
        if changed
        else "Administrative per-chat transcription controls patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
