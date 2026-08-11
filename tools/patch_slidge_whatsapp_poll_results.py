from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ruff: noqa: E501


def _replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Could not patch {description}: expected one match, found {count}.")
    return text.replace(old, new, 1)


def _write(path: Path, text: str, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-poll-results")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "MessagePollUpdate" in source:
        return False
    source = _replace_once(
        source,
        'import (\n\t// Standard library.\n\t"context"\n',
        'import (\n\t// Standard library.\n\t"context"\n\t"encoding/hex"\n',
        "event.go poll-update import",
    )
    source = _replace_once(
        source,
        "\tMessagePoll\n)",
        "\tMessagePoll\n\tMessagePollUpdate\n)",
        "event.go poll-update kind",
    )
    source = _replace_once(
        source,
        "\tPoll        Poll         // The multiple-choice poll contained in the message, if any.\n",
        "\tPoll        Poll         // The multiple-choice poll contained in the message, if any.\n\tPollUpdate  PollUpdate   // A decrypted vote update for a previous poll, if any.\n",
        "event.go poll-update message field",
    )
    source = _replace_once(
        source,
        "type PollOption struct {\n\tTitle string // The human-readable name for the poll option.\n}\n",
        '''type PollOption struct {
	Title string // The human-readable name for the poll option.
}

// PollUpdate identifies one voter's current selected option hashes. The option
// names remain private to WhatsApp; the XMPP client matches these SHA-256 hashes
// against the poll options it already received.
type PollUpdate struct {
	PollID       string
	Voter        string
	VoterLID     string
	OptionHashes []string
}
''',
        "event.go poll-update type",
    )
    update = '''	// Handle encrypted poll vote updates. WhatsMeow decrypts these using the
	// message secret retained in its own store; no poll secret crosses to XMPP.
	if update := evt.Message.GetPollUpdateMessage(); update != nil {
		vote, err := client.DecryptPollVote(ctx, evt)
		if err != nil {
			client.Log.Warnf("Ignoring undecipherable poll update %s: %v", evt.Info.ID, err)
			return EventUnknown, nil
		}
		key := update.GetPollCreationMessageKey()
		if key == nil || key.GetID() == "" {
			return EventUnknown, nil
		}
		message.Kind = MessagePollUpdate
		message.PollUpdate = PollUpdate{
			PollID:   key.GetID(),
			Voter:    message.Actor.JID,
			VoterLID: message.Actor.LID,
		}
		for _, optionHash := range vote.GetSelectedOptions() {
			message.PollUpdate.OptionHashes = append(
				message.PollUpdate.OptionHashes, hex.EncodeToString(optionHash),
			)
		}
		return EventMessage, &EventPayload{Message: message}
	}

'''
    source = _replace_once(
        source,
        "\t// Handle poll messages.\n",
        update + "\t// Handle poll messages.\n",
        "event.go poll-update event handling",
    )
    _write(path, source, backup)
    return True


def patch_session_py(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "async def on_wa_msg_poll_update(" in source:
        return False
    source = _replace_once(
        source,
        "            case whatsapp.MessagePoll:\n                await self.on_wa_msg_poll(message, actor, muc)\n",
        "            case whatsapp.MessagePoll:\n                await self.on_wa_msg_poll(message, actor, muc)\n            case whatsapp.MessagePollUpdate:\n                await self.on_wa_msg_poll_update(message, actor, muc)\n",
        "session.py poll-update dispatch",
    )
    handler = '''    async def on_wa_msg_poll_update(
        self, message: whatsapp.Message, actor: Contact | Participant, _muc: MUC | None
    ) -> None:
        update = message.PollUpdate
        if not update.PollID or not update.Voter:
            return
        attrs = {"id": update.PollID, "voter": update.Voter}
        if update.VoterLID:
            attrs["voter-lid"] = update.VoterLID
        poll_xml = ET.Element(f"{{{POLL_NAMESPACE}}}poll-update", attrs)
        for option_hash in update.OptionHashes:
            if option_hash:
                ET.SubElement(poll_xml, f"{{{POLL_NAMESPACE}}}option", {"hash": option_hash})
        actor.send_text(
            body=" ",
            legacy_msg_id=message.ID,
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            extra_xml=poll_xml,
        )

'''
    source = _replace_once(
        source,
            "    async def on_wa_avatar(self, avatar: whatsapp.Avatar) -> None:\n",
            handler + "    async def on_wa_avatar(self, avatar: whatsapp.Avatar) -> None:\n",
        "session.py poll-update XMPP forwarding",
    )
    _write(path, source, backup)
    return True


def patch_package(package_root: Path, *, backup: bool) -> bool:
    package_root = package_root.resolve()
    event_go = package_root / "slidge_whatsapp/event.go"
    session_py = package_root / "slidge_whatsapp/session.py"
    missing = [str(path) for path in (event_go, session_py) if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changed_event = patch_event_go(event_go, backup=backup)
    changed_session = patch_session_py(session_py, backup=backup)
    return changed_event or changed_session


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Expose decrypted WhatsApp poll vote updates to the XMPP client."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.package_root, backup=not args.no_backup)
    print(
        "WhatsApp poll-result patch applied."
        if changed
        else "WhatsApp poll-result patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
