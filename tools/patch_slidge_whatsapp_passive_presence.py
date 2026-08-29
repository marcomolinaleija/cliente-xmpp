from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ruff: noqa: E501


MARKER = "func passiveWhatsAppPresence(_ PresenceKind) types.Presence"
ACTIVE_SEND = "client.SendPresence(s.ctx, types.PresenceAvailable)"
PASSIVE_SEND = "client.SendPresence(s.ctx, passiveWhatsAppPresence(PresenceAvailable))"

OLD_SEND_PRESENCE = '''func (s *Session) SendPresence(presence PresenceKind, statusMessage string) error {
	client := s.currentClient()
	if client == nil || client.Store == nil || client.Store.ID == nil {
		return fmt.Errorf("cannot send presence for unauthenticated session")
	}

	var err error
	s.queuePresenceRefresh(presence)

	switch presence {
	case PresenceAvailable:
		err = client.SendPresence(s.ctx, types.PresenceAvailable)
	case PresenceUnavailable:
		err = client.SendPresence(s.ctx, types.PresenceUnavailable)
	}

	if err == nil && statusMessage != "" {
		err = client.SetStatusMessage(s.ctx, statusMessage)
	}

	return err
}
'''

NEW_SEND_PRESENCE = '''// passiveWhatsAppPresence keeps the linked device connected without advertising it
// as the foreground WhatsApp session. This preserves notifications on the phone.
func passiveWhatsAppPresence(_ PresenceKind) types.Presence {
	return types.PresenceUnavailable
}

func (s *Session) SendPresence(presence PresenceKind, statusMessage string) error {
	client := s.currentClient()
	if client == nil || client.Store == nil || client.Store.ID == nil {
		return fmt.Errorf("cannot send presence for unauthenticated session")
	}

	s.queuePresenceRefresh(presence)
	err := client.SendPresence(s.ctx, passiveWhatsAppPresence(presence))

	if err == nil && statusMessage != "" {
		err = client.SetStatusMessage(s.ctx, statusMessage)
	}

	return err
}
'''


def patch_session_go(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        if ACTIVE_SEND in text:
            raise SystemExit(f"Passive-presence marker exists but active sends remain in {path}")
        return False

    if text.count(OLD_SEND_PRESENCE) != 1:
        raise SystemExit(f"Could not find the v21 SendPresence implementation in {path}")
    if text.count(ACTIVE_SEND) != 3:
        raise SystemExit(
            f"Expected three active presence sends in {path}, found {text.count(ACTIVE_SEND)}"
        )

    updated = text.replace(OLD_SEND_PRESENCE, NEW_SEND_PRESENCE, 1)
    updated = updated.replace(ACTIVE_SEND, PASSIVE_SEND)
    if ACTIVE_SEND in updated:
        raise SystemExit(f"An active presence send remains in {path}")

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-passive-presence")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"patched {path}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep the WhatsApp linked device in passive presence mode."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    path = args.package_root.resolve() / "slidge_whatsapp" / "session.go"
    if not path.is_file():
        raise SystemExit(f"Expected file not found: {path}")
    changed = patch_session_go(path, backup=not args.no_backup)
    if changed:
        print("Passive-presence patch applied; rebuild the Go binding.")
    else:
        print("Passive-presence patch already present; no files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
