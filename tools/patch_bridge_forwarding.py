from __future__ import annotations

import argparse
from pathlib import Path

FORWARDED_NAMESPACE = "urn:marco-ml:whatsapp:forwarded:0"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add native WhatsApp forwarding metadata to Slidge and slidge-whatsapp."
    )
    parser.add_argument("slidge_tree", type=Path, nargs="?")
    parser.add_argument("slidge_whatsapp_tree", type=Path, nargs="?")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--slidge-only", action="store_true")
    parser.add_argument("--whatsapp-only", action="store_true")
    args = parser.parse_args()

    backup = not args.no_backup
    if args.slidge_only and args.whatsapp_only:
        parser.error("--slidge-only and --whatsapp-only are mutually exclusive")
    if not args.whatsapp_only and args.slidge_tree is None:
        parser.error("slidge_tree is required unless --whatsapp-only is used")
    if not args.slidge_only and args.slidge_whatsapp_tree is None:
        parser.error("slidge_whatsapp_tree is required unless --slidge-only is used")

    changes: list[bool] = []
    if not args.whatsapp_only:
        assert args.slidge_tree is not None
        slidge_tree = args.slidge_tree.resolve()
        changes.extend(
            [
                patch_slidge_types(slidge_tree, backup=backup),
                patch_slidge_dispatcher(slidge_tree, backup=backup),
                patch_slidge_text(slidge_tree, backup=backup),
                patch_slidge_attachment(slidge_tree, backup=backup),
            ]
        )
    if not args.slidge_only:
        assert args.slidge_whatsapp_tree is not None
        whatsapp_tree = args.slidge_whatsapp_tree.resolve()
        changes.extend(
            [
                patch_whatsapp_event(whatsapp_tree, backup=backup),
                patch_whatsapp_session_py(whatsapp_tree, backup=backup),
                patch_whatsapp_mixins(whatsapp_tree, backup=backup),
                patch_whatsapp_session_go(whatsapp_tree, backup=backup),
            ]
        )
    if any(changes):
        print("Forwarding patch applied. Rebuild the bridge image before deployment.")
    else:
        print("Forwarding patch already present; no files changed.")
    return 0


def patch_slidge_types(root: Path, *, backup: bool) -> bool:
    path = root / "slidge" / "util" / "types.py"
    return patch_once(
        path,
        "    thread: str | None = None\n\n\nclass XMPPAttachmentMessage",
        "    thread: str | None = None\n"
        "    is_forwarded: bool = False\n\n\n"
        "class XMPPAttachmentMessage",
        marker="    is_forwarded: bool = False",
        backup=backup,
    )


def patch_slidge_dispatcher(root: Path, *, backup: bool) -> bool:
    path = root / "slidge" / "core" / "dispatcher" / "message" / "message.py"
    text = read_expected(path)
    marker = f'find("{{{FORWARDED_NAMESPACE}}}forwarded")'
    if marker in text:
        return False
    old = """        if attachments:
            xmpp_msg: XMPPMessage = XMPPAttachmentMessage(
                body=body,
"""
    new = f"""        is_forwarded = (
            msg.xml.find("{{{FORWARDED_NAMESPACE}}}forwarded") is not None
        )

        if attachments:
            xmpp_msg: XMPPMessage = XMPPAttachmentMessage(
                body=body,
                is_forwarded=is_forwarded,
"""
    text = replace_exact(text, old, new, path)
    text = replace_exact(
        text,
        """        else:
            xmpp_msg = XMPPTextMessage(
                body=body,
""",
        """        else:
            xmpp_msg = XMPPTextMessage(
                body=body,
                is_forwarded=is_forwarded,
""",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_slidge_text(root: Path, *, backup: bool) -> bool:
    path = root / "slidge" / "core" / "mixins" / "message_text.py"
    text = read_expected(path)
    if "def add_whatsapp_forwarded_flag" in text:
        return False
    text = replace_exact(
        text,
        "from datetime import datetime\n",
        "from datetime import datetime\nfrom xml.etree import ElementTree as ET\n",
        path,
    )
    text = replace_exact(
        text,
        "\n\nclass TextMessageMixin(MessageMaker):\n",
        "\n\n"
        f'WHATSAPP_FORWARDED_TAG = "{{{FORWARDED_NAMESPACE}}}forwarded"\n\n\n'
        "def add_whatsapp_forwarded_flag(\n"
        "    msg: Message, is_forwarded: bool\n"
        ") -> None:\n"
        "    if is_forwarded:\n"
        "        msg.xml.append(ET.Element(WHATSAPP_FORWARDED_TAG))\n\n\n"
        "class TextMessageMixin(MessageMaker):\n",
        path,
    )
    text = replace_exact(
        text,
        """    def send_text(
        self,
        body: str,
        legacy_msg_id: str | None = None,
        *,
        when: datetime | None = None,
        reply_to: MessageReference | None = None,
        thread: str | None = None,
        hints: Iterable[ProcessingHint] | None = None,
        carbon: bool = False,
        archive_only: bool = False,
        correction: bool = False,
        correction_event_id: str | None = None,
        link_previews: list[LinkPreview] | None = None,
        **send_kwargs: object,
""",
        """    def send_text(
        self,
        body: str,
        legacy_msg_id: str | None = None,
        *,
        when: datetime | None = None,
        reply_to: MessageReference | None = None,
        thread: str | None = None,
        hints: Iterable[ProcessingHint] | None = None,
        carbon: bool = False,
        archive_only: bool = False,
        correction: bool = False,
        correction_event_id: str | None = None,
        link_previews: list[LinkPreview] | None = None,
        is_forwarded: bool = False,
        **send_kwargs: object,
""",
        path,
    )
    text = replace_exact(
        text,
        """            link_previews=link_previews,
        )
        if correction:
""",
        """            link_previews=link_previews,
        )
        add_whatsapp_forwarded_flag(msg, is_forwarded)
        if correction:
""",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_slidge_attachment(root: Path, *, backup: bool) -> bool:
    path = root / "slidge" / "core" / "mixins" / "attachment.py"
    text = read_expected(path)
    if "add_whatsapp_forwarded_flag(msg, is_forwarded)" in text:
        return False
    text = replace_exact(
        text,
        "from .message_text import TextMessageMixin\n",
        "from .message_text import TextMessageMixin, add_whatsapp_forwarded_flag\n",
        path,
    )
    text = replace_exact(
        text,
        "        correction: bool = False,\n        **send_kwargs: Any,  # noqa:ANN401\n",
        "        correction: bool = False,\n"
        "        is_forwarded: bool = False,\n"
        "        **send_kwargs: Any,  # noqa:ANN401\n",
        path,
    )
    text = replace_exact(
        text,
        """            mto=mto,
        )
        if attachment.is_sticker:
""",
        """            mto=mto,
        )
        add_whatsapp_forwarded_flag(msg, is_forwarded)
        if attachment.is_sticker:
""",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_whatsapp_event(root: Path, *, backup: bool) -> bool:
    path = root / "slidge_whatsapp" / "event.go"
    return patch_once(
        path,
        """\tremoteJID, _ := types.ParseJID(info.GetRemoteJID())
\toriginJID, err := types.ParseJID(info.GetParticipant())
""",
        """\tmessage.IsForwarded = info.GetIsForwarded()

\tremoteJID, _ := types.ParseJID(info.GetRemoteJID())
\toriginJID, err := types.ParseJID(info.GetParticipant())
""",
        remove="\tmessage.IsForwarded = info.GetIsForwarded()\n",
        marker="\tmessage.IsForwarded = info.GetIsForwarded()\n\n\tremoteJID",
        backup=backup,
    )


def patch_whatsapp_session_py(root: Path, *, backup: bool) -> bool:
    path = root / "slidge_whatsapp" / "session.py"
    text = read_expected(path)
    if "is_forwarded=message.IsForwarded" in text:
        return False
    text = replace_exact(
        text,
        "            link_previews=_get_link_previews(message.Preview),\n",
        "            link_previews=_get_link_previews(message.Preview),\n"
        "            is_forwarded=message.IsForwarded,\n",
        path,
    )
    text = replace_exact(
        text,
        "            carbon=message.Actor.IsMe,\n"
        "        )\n"
        "        for attachment in attachments:\n",
        "            carbon=message.Actor.IsMe,\n"
        "            is_forwarded=message.IsForwarded,\n"
        "        )\n"
        "        for attachment in attachments:\n",
        path,
    )
    text = replace_exact(
        text,
        """        if message.IsForwarded:
            body = "↱ Forwarded message:\\n " + add_quote_prefix(body)
""",
        "",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_whatsapp_mixins(root: Path, *, backup: bool) -> bool:
    path = root / "slidge_whatsapp" / "mixins.py"
    text = read_expected(path)
    if text.count("IsForwarded=xmpp_msg.is_forwarded") >= 2:
        return False
    text = replace_exact(
        text,
        "            Location=message_location,\n",
        "            Location=message_location,\n            IsForwarded=xmpp_msg.is_forwarded,\n",
        path,
    )
    text = replace_exact(
        text,
        '            ReplyID=xmpp_msg.reply.msg_id if xmpp_msg.reply else "",\n',
        '            ReplyID=xmpp_msg.reply.msg_id if xmpp_msg.reply else "",\n'
        "            IsForwarded=xmpp_msg.is_forwarded,\n",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_whatsapp_session_go(root: Path, *, backup: bool) -> bool:
    path = root / "slidge_whatsapp" / "session.go"
    text = read_expected(path)
    if "func setForwardedContext" in text:
        return False
    text = replace_exact(
        text,
        """\tdefault:
\t\tpayload = s.getMessagePayload(s.ctx, message)
\t\textra.ID = message.ID
\t}

\ts.gateway.logger.Debugf("Sending message to JID '%s': %+v", jid, payload)
""",
        """\tdefault:
\t\tpayload = s.getMessagePayload(s.ctx, message)
\t\textra.ID = message.ID
\t}

\tif message.IsForwarded && (message.Kind == MessagePlain || message.Kind == MessageAttachment) {
\t\tsetForwardedContext(payload)
\t}

\ts.gateway.logger.Debugf("Sending message to JID '%s': %+v", jid, payload)
""",
        path,
    )
    helper = """
func setForwardedContext(payload *waE2E.Message) {
\tif payload == nil {
\t\treturn
\t}

\tisForwarded := true
\tsetFlag := func(info **waE2E.ContextInfo) {
\t\tif *info == nil {
\t\t\t*info = &waE2E.ContextInfo{}
\t\t}
\t\t(*info).IsForwarded = &isForwarded
\t}

\tswitch {
\tcase payload.ExtendedTextMessage != nil:
\t\tsetFlag(&payload.ExtendedTextMessage.ContextInfo)
\tcase payload.Conversation != nil:
\t\ttext := payload.GetConversation()
\t\tpayload.Conversation = nil
\t\tpayload.ExtendedTextMessage = &waE2E.ExtendedTextMessage{Text: &text}
\t\tsetFlag(&payload.ExtendedTextMessage.ContextInfo)
\tcase payload.ImageMessage != nil:
\t\tsetFlag(&payload.ImageMessage.ContextInfo)
\tcase payload.AudioMessage != nil:
\t\tsetFlag(&payload.AudioMessage.ContextInfo)
\tcase payload.VideoMessage != nil:
\t\tsetFlag(&payload.VideoMessage.ContextInfo)
\tcase payload.DocumentMessage != nil:
\t\tsetFlag(&payload.DocumentMessage.ContextInfo)
\tcase payload.LocationMessage != nil:
\t\tsetFlag(&payload.LocationMessage.ContextInfo)
\t}
}

"""
    constants = "\nconst (\n\t// The maximum size thumbnail image we'll send"
    text = replace_exact(
        text,
        constants,
        "\n" + helper + "const (\n\t// The maximum size thumbnail image we'll send",
        path,
    )
    write_text(path, text, backup=backup)
    return True


def patch_once(
    path: Path,
    old: str,
    new: str,
    *,
    marker: str,
    backup: bool,
    remove: str | None = None,
) -> bool:
    text = read_expected(path)
    if marker in text:
        return False
    if remove is not None:
        text = replace_exact(text, remove, "", path)
    text = replace_exact(text, old, new, path)
    write_text(path, text, backup=backup)
    return True


def replace_exact(text: str, old: str, new: str, path: Path) -> str:
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
