from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SESSION_MARKER = "reply context is applied to media payloads"
INCOMING_MARKER = "media attachment context is propagated"
PYTHON_REPLY_MARKER = "reply fallback without author"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise SystemExit(f"Expected exactly one {label} block, found {source.count(old)}")
    return source.replace(old, new, 1)


def patch_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if SESSION_MARKER in source:
        return False

    attachment_send = (
        "\t\tif payload, err = uploadAttachment(s.ctx, s.client, "
        "&message.Attachments[0]); err != nil {\n"
        "\t\t\treturn fmt.Errorf(\"failed uploading attachment: %s\", err)\n"
        "\t\t}\n"
        "\t\textra.ID = message.ID\n"
    )
    attachment_send_patched = (
        "\t\tif payload, err = uploadAttachment(s.ctx, s.client, "
        "&message.Attachments[0]); err != nil {\n"
        "\t\t\treturn fmt.Errorf(\"failed uploading attachment: %s\", err)\n"
        "\t\t}\n"
        "\t\t// The upload helper builds the media payload directly. Apply XEP-0461\n"
        "\t\t// context to that payload before sending it to WhatsApp.\n"
        "\t\tsetReplyContext(payload, message)\n"
        "\t\textra.ID = message.ID\n"
    )
    source = replace_once(
        source,
        attachment_send,
        attachment_send_patched,
        "attachment send branch",
    )

    helper = '''// reply context is applied to media payloads
func setReplyContext(payload *waE2E.Message, message Message) {
	if payload == nil || message.ReplyID == "" {
		return
	}

	participant := message.OriginActor.JID
	_, quotedGroupJID, privateGroupReply := strings.Cut(
		message.OriginActor.LID,
		privateReplyGroupSeparator,
	)
	if message.Chat.IsGroup {
		participant = message.OriginActor.LID
	} else if privateGroupReply {
		participant, _, _ = strings.Cut(message.OriginActor.LID, privateReplyGroupSeparator)
		if participant == "" {
			participant = message.OriginActor.JID
		}
	}
	if participant == "" {
		return
	}

	contextInfo := &waE2E.ContextInfo{
		StanzaID:      &message.ReplyID,
		QuotedMessage: &waE2E.Message{Conversation: ptrTo(message.ReplyBody)},
		Participant:   &participant,
	}
	if privateGroupReply && quotedGroupJID != "" {
		contextInfo.RemoteJID = &quotedGroupJID
	}

	mergeContext := func(existing **waE2E.ContextInfo) {
		if *existing == nil {
			*existing = contextInfo
			return
		}
		if (*existing).StanzaID == nil {
			(*existing).StanzaID = contextInfo.StanzaID
		}
		if (*existing).QuotedMessage == nil {
			(*existing).QuotedMessage = contextInfo.QuotedMessage
		}
		if (*existing).Participant == nil {
			(*existing).Participant = contextInfo.Participant
		}
		if (*existing).RemoteJID == nil {
			(*existing).RemoteJID = contextInfo.RemoteJID
		}
	}

	switch {
	case payload.ImageMessage != nil:
		mergeContext(&payload.ImageMessage.ContextInfo)
	case payload.AudioMessage != nil:
		mergeContext(&payload.AudioMessage.ContextInfo)
	case payload.VideoMessage != nil:
		mergeContext(&payload.VideoMessage.ContextInfo)
	case payload.DocumentMessage != nil:
		mergeContext(&payload.DocumentMessage.ContextInfo)
	case payload.StickerMessage != nil:
		mergeContext(&payload.StickerMessage.ContextInfo)
	}
}

'''
    marker = "func (s *Session) getMessagePayload"
    if source.count(marker) != 1:
        raise SystemExit("Expected exactly one getMessagePayload function")
    source = source.replace(marker, helper + marker, 1)

    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".reply-attachments.bak"))
    path.write_text(source, encoding="utf-8")
    return True


def patch_python_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if PYTHON_REPLY_MARKER in source:
        return False

    old = (
        "        reply_actor, quoted_group_jid = _private_group_reply_context(\n"
        "            message.OriginActor\n"
        "        )\n"
        "        if quoted_group_jid:\n"
    )
    new = (
        "        # reply fallback without author\n"
        "        reply_actor, quoted_group_jid = _private_group_reply_context(\n"
        "            message.OriginActor\n"
        "        )\n"
        "        # WhatsApp may omit the quoted participant on a self-sent message. Keep\n"
        "        # the native reply ID usable in XMPP even when the author is unknown.\n"
        "        if not reply_actor.JID and not reply_actor.LID:\n"
        "            return reply_to\n"
        "        if quoted_group_jid:\n"
    )
    source = replace_once(source, old, new, "reply conversion fallback")
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".reply-attachments.bak"))
    path.write_text(source, encoding="utf-8")
    return True


def patch_event(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if INCOMING_MARKER in source:
        return False

    old = """\t\tcase *waE2E.ImageMessage:
\t\t\ta.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
\t\tcase *waE2E.AudioMessage:
\t\t\t// Preserve incoming WhatsApp voice notes as their original Ogg/Opus payload.
\t\t\t// The XMPP client supports Opus directly, so transcoding to AAC only adds loss.
\t\t\ta.MIME = msg.GetMimetype()
\t\tcase *waE2E.VideoMessage:
\t\t\ta.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
\t\tcase *waE2E.DocumentMessage:
\t\t\ta.MIME, a.Caption, a.Filename = msg.GetMimetype(), msg.GetCaption(), msg.GetFileName()
\t\tcase *waE2E.StickerMessage:
\t\t\ta.MIME = msg.GetMimetype()
\t\t}
"""
    new = """\t\tcase *waE2E.ImageMessage:
\t\t\ta.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
\t\t\tinfo = msg.GetContextInfo()
\t\tcase *waE2E.AudioMessage:
\t\t\t// Preserve incoming WhatsApp voice notes as their original Ogg/Opus payload.
\t\t\t// The XMPP client supports Opus directly, so transcoding to AAC only adds loss.
\t\t\ta.MIME = msg.GetMimetype()
\t\t\tinfo = msg.GetContextInfo()
\t\tcase *waE2E.VideoMessage:
\t\t\ta.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
\t\t\tinfo = msg.GetContextInfo()
\t\tcase *waE2E.DocumentMessage:
\t\t\ta.MIME, a.Caption, a.Filename = msg.GetMimetype(), msg.GetCaption(), msg.GetFileName()
\t\t\tinfo = msg.GetContextInfo()
\t\tcase *waE2E.StickerMessage:
\t\t\ta.MIME = msg.GetMimetype()
\t\t\tinfo = msg.GetContextInfo()
\t\t}
"""
    source = replace_once(source, old, new, "incoming attachment media switch")
    function_signature = (
        "func getMessageAttachments(ctx context.Context, client *whatsmeow.Client, "
        "message *waE2E.Message) ([]Attachment, *waE2E.ContextInfo, error) {"
    )
    source = source.replace(
        function_signature,
        "// media attachment context is propagated\n" + function_signature,
        1,
    )

    context_function = (
        "func getMessageWithContext(ctx context.Context, client *whatsmeow.Client, "
        "message Message, info *waE2E.ContextInfo) Message {\n"
        "\tif info == nil {\n"
        "\t\treturn message\n"
        "\t}\n"
        "\n"
        "\tmessage.IsForwarded = info.GetIsForwarded()\n"
        "\n"
        "\tremoteJID, _ := types.ParseJID(info.GetRemoteJID())\n"
        "\toriginJID, err := types.ParseJID(info.GetParticipant())\n"
        "\tif err != nil {\n"
        "\t\treturn message\n"
        "\t}\n"
        "\n"
        "\tmessage.ReplyID = info.GetStanzaID()\n"
        "\tmessage.OriginActor = newActor(ctx, client, originJID, remoteJID)\n"
        "\tif remoteJID.Server == types.GroupServer && !message.Chat.IsGroup {\n"
        "\t\tmessage.OriginActor.LID += privateReplyGroupSeparator + remoteJID.ToNonAD().String()\n"
        "\t}\n"
        "\n"
        "\t// Handle reply messages.\n"
        "\tif q := info.GetQuotedMessage(); q != nil {\n"
        "\t\tif qe := q.GetExtendedTextMessage(); qe != nil {\n"
        "\t\t\tmessage.ReplyBody = qe.GetText()\n"
        "\t\t} else {\n"
        "\t\t\tmessage.ReplyBody = q.GetConversation()\n"
        "\t\t}\n"
        "\t}\n"
        "\n"
        "\treturn message\n"
        "}\n"
    )
    context_function_patched = (
        "func getMessageWithContext(ctx context.Context, client *whatsmeow.Client, "
        "message Message, info *waE2E.ContextInfo) Message {\n"
        "\tif info == nil {\n"
        "\t\treturn message\n"
        "\t}\n"
        "\n"
        "\tmessage.IsForwarded = info.GetIsForwarded()\n"
        "\t// Preserve the native reference before resolving the quoted participant.\n"
        "\t// Self-sent WhatsApp messages can carry a stanza ID without a participant.\n"
        "\tmessage.ReplyID = info.GetStanzaID()\n"
        "\tif q := info.GetQuotedMessage(); q != nil {\n"
        "\t\tif qe := q.GetExtendedTextMessage(); qe != nil {\n"
        "\t\t\tmessage.ReplyBody = qe.GetText()\n"
        "\t\t} else {\n"
        "\t\t\tmessage.ReplyBody = q.GetConversation()\n"
        "\t\t}\n"
        "\t}\n"
        "\n"
        "\tremoteJID, _ := types.ParseJID(info.GetRemoteJID())\n"
        "\toriginJID, err := types.ParseJID(info.GetParticipant())\n"
        "\tif err != nil {\n"
        "\t\treturn message\n"
        "\t}\n"
        "\n"
        "\tmessage.OriginActor = newActor(ctx, client, originJID, remoteJID)\n"
        "\tif remoteJID.Server == types.GroupServer && !message.Chat.IsGroup {\n"
        "\t\tmessage.OriginActor.LID += privateReplyGroupSeparator + remoteJID.ToNonAD().String()\n"
        "\t}\n"
        "\n"
        "\treturn message\n"
        "}\n"
    )
    source = replace_once(
        source,
        context_function,
        context_function_patched,
        "message context resolver",
    )

    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".reply-attachments.bak"))
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve native WhatsApp reply context for incoming and outgoing media."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    session_path = args.site_packages / "slidge_whatsapp" / "session.go"
    python_session_path = args.site_packages / "slidge_whatsapp" / "session.py"
    event_path = args.site_packages / "slidge_whatsapp" / "event.go"
    changed = patch_session(session_path, backup=not args.no_backup)
    changed = patch_python_session(python_session_path, backup=not args.no_backup) or changed
    changed = patch_event(event_path, backup=not args.no_backup) or changed
    print(
        "Reply attachment patch applied."
        if changed
        else "Reply attachment patch already applied."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
