from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so WhatsApp MarkChatAsRead events are "
            "propagated as read receipts to XMPP."
        )
    )
    parser.add_argument(
        "slidge_whatsapp_tree",
        type=Path,
        help="Path to the slidge-whatsapp source tree used to build the bridge.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files before modifying sources.",
    )
    args = parser.parse_args()

    source_tree = args.slidge_whatsapp_tree.resolve()
    changed = False
    changed |= patch_event_go(
        source_tree / "slidge_whatsapp" / "event.go",
        backup=not args.no_backup,
    )
    changed |= patch_session_go(
        source_tree / "slidge_whatsapp" / "session.go",
        backup=not args.no_backup,
    )
    changed |= patch_session_py(
        source_tree / "slidge_whatsapp" / "session.py",
        backup=not args.no_backup,
    )
    if changed:
        print("Read-sync patch applied. Rebuild the bridge image before deployment.")
    else:
        print("Read-sync patch already present; no files changed.")
    return 0


def patch_event_go(path: Path, *, backup: bool) -> bool:
    text = read_expected(path)
    if "func newMarkChatAsReadEvent(" in text:
        return False

    anchor = (
        "// GroupAffiliation represents the set of privilidges given to a specific "
        "participant in a group.\n"
    )
    converter = r'''// NewMarkChatAsReadEvent converts a read action made on another
// WhatsApp device into a normal read receipt. Unread actions are ignored because
// XMPP displayed markers are monotonic.
func newMarkChatAsReadEvent(
	ctx context.Context,
	client *whatsmeow.Client,
	evt *events.MarkChatAsRead,
) (EventKind, *EventPayload) {
	action := evt.Action
	if action == nil || !action.GetRead() {
		return EventUnknown, nil
	}

	var messageID string
	var latestTimestamp int64
	if messageRange := action.GetMessageRange(); messageRange != nil {
		for _, message := range messageRange.GetMessages() {
			key := message.GetKey()
			if key == nil || key.GetID() == "" {
				continue
			}
			if messageID == "" || message.GetTimestamp() >= latestTimestamp {
				messageID = key.GetID()
				latestTimestamp = message.GetTimestamp()
			}
		}
	}
	if messageID == "" {
		client.Log.Warnf("Ignoring MarkChatAsRead without a message ID for %s", evt.JID)
		return EventUnknown, nil
	}

	chat := newChat(ctx, client, evt.JID, evt.JID.Server == types.GroupServer)
	if chat.JID == "" {
		client.Log.Warnf("Ignoring MarkChatAsRead for unknown chat %s", evt.JID)
		return EventUnknown, nil
	}

	actor := newActor(ctx, client, client.Store.GetJID(), client.Store.GetLID())
	actor.IsMe = true
	receipt := Receipt{
		Kind:       ReceiptRead,
		MessageIDs: []string{messageID},
		Actor:      actor,
		Chat:       chat,
		Timestamp:  evt.Timestamp.Unix(),
	}
	return EventReceipt, &EventPayload{Receipt: receipt}
}

'''
    updated = replace_once(text, anchor, converter + anchor, path)
    write_text(path, updated, backup=backup)
    return True


def patch_session_go(path: Path, *, backup: bool) -> bool:
    text = read_expected(path)
    marker = "case *events.MarkChatAsRead:"
    if marker in text:
        return False

    old = """\tcase *events.Receipt:
\t\ts.propagateEvent(newReceiptEvent(s.ctx, s.client, evt))
\tcase *events.Presence:
"""
    new = """\tcase *events.Receipt:
\t\ts.propagateEvent(newReceiptEvent(s.ctx, s.client, evt))
\tcase *events.MarkChatAsRead:
\t\ts.propagateEvent(newMarkChatAsReadEvent(s.ctx, s.client, evt))
\tcase *events.Presence:
"""
    updated = replace_once(text, old, new, path)
    write_text(path, updated, backup=backup)
    return True


def patch_session_py(path: Path, *, backup: bool) -> bool:
    text = read_expected(path)
    marker = 'getattr(global_config, "NO_UPLOAD_METHOD", None)'
    if marker in text:
        return False

    updated = replace_once(
        text,
        '            if global_config.NO_UPLOAD_METHOD != "symlink":\n',
        f"            if {marker} != \"symlink\":\n",
        path,
    )
    write_text(path, updated, backup=backup)
    return True


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Could not patch {path}: expected one match, found {count}.")
    return text.replace(old, new, 1)


def read_expected(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Expected source file not found: {path}")
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str, *, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {path}")


if __name__ == "__main__":
    raise SystemExit(main())
