from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

# ruff: noqa: E501


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not apply private-reply patch: expected one {description}, found {count}."
        )
    return text.replace(old, new, 1)


def replace_function(text: str, pattern: str, replacement: str, description: str) -> str:
    updated, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(
            f"Could not apply private-reply patch: expected one {description}, found {count}."
        )
    return updated


def patch_dispatcher(source: str) -> str:
    if "private group reply must reference its MUC" in source:
        return source
    replacement = '''    async def __get_reply(
        self, msg: Message, recipient: AnyRecipient
    ) -> tuple[str, Reply | None]:
        if "reply" not in msg:
            return msg["body"], None

        session = recipient.session
        reply_to_jid = JID(msg["reply"]["to"])
        reply_to = None
        reply_recipient = recipient

        # A private group reply must reference its MUC even though its outer stanza is a chat.
        is_private_group_reply = (
            msg["type"] == "chat"
            and reply_to_jid.local.startswith("#")
            and reply_to_jid.server == self.xmpp.boundjid.bare
        )
        if is_private_group_reply:
            try:
                muc = await session.bookmarks.by_jid(reply_to_jid)
                reply_recipient = muc
                nick = reply_to_jid.resource
                if nick == muc.user_nick:
                    reply_to = await muc.get_user_participant()
                elif not nick:
                    reply_to = muc.get_system_participant()
                else:
                    reply_to = await muc.get_participant(nick, store=False)
            except XMPPError:
                session.log.exception("Could not instantiate replied-to group participant")
        elif msg["type"] == "chat":
            if reply_to_jid.bare != session.user_jid.bare:
                try:
                    reply_to = await session.contacts.by_jid(reply_to_jid)
                except XMPPError:
                    session.log.exception("Could not instantiate replied-to contact")
        elif msg["type"] == "groupchat":
            nick = reply_to_jid.resource
            try:
                muc = await session.bookmarks.by_jid(reply_to_jid)
            except XMPPError:
                session.log.exception("Could not instantiate replied-to participant")
            else:
                if nick == muc.user_nick:
                    reply_to = await muc.get_user_participant()
                elif not nick:
                    reply_to = muc.get_system_participant()
                else:
                    reply_to = await muc.get_participant(nick, store=False)

        try:
            reply_to_msg_id = self._xmpp_msg_id_to_legacy(msg["reply"]["id"], reply_recipient)
        except XMPPError:
            session.log.debug("Could not determine reply-to legacy msg ID, sending quote instead.")
            return redact_url(msg["body"]), None

        if "fallback" in msg and (isinstance(reply_recipient, LegacyMUC) or recipient.REPLIES):
            text = msg["fallback"].get_stripped_body(self.xmpp.plugin["xep_0461"].namespace)
            try:
                reply_fallback = redact_url(msg["reply"].get_fallback_body())
            except AttributeError:
                reply_fallback = None
        else:
            text = msg["body"]
            reply_fallback = None

        return text, Reply(reply_to_msg_id, reply_to, reply_fallback)

'''
    return replace_function(
        source,
        r"    async def __get_reply\(.*?\n    async def __dispatch_nonbob_sticker\(",
        replacement + "    async def __dispatch_nonbob_sticker(",
        "XEP-0461 reply parser",
    )


def patch_contact(source: str) -> str:
    if "private group reply retains the original WhatsApp group" in source:
        return source
    replacement = '''    def _set_reply_to(self, xmpp_msg: XMPPMessage, wa_msg: whatsapp.Message) -> None:
        if not xmpp_msg.reply:
            return

        wa_msg.ReplyID = xmpp_msg.reply.msg_id
        if xmpp_msg.reply.fallback:
            wa_msg.ReplyBody = strip_quote_prefix(xmpp_msg.reply.fallback)
            wa_msg.Body = wa_msg.Body.lstrip()

        reply_target = xmpp_msg.reply.to
        # A private group reply retains the original WhatsApp group and participant.
        if reply_target is not None and hasattr(reply_target, "muc"):
            participant = reply_target
            wa_msg.OriginActor.IsMe = participant.is_user
            if participant.contact:
                wa_msg.OriginActor.JID = participant.contact.legacy_id
            participant_lid = (
                participant.occupant_id
                if participant.occupant_id and participant.occupant_id.endswith("@lid")
                else ""
            )
            wa_msg.OriginActor.LID = participant_lid + "\x1f" + participant.muc.legacy_id
            return

        if reply_target is None:
            wa_msg.OriginActor.IsMe = True
            wa_msg.OriginActor.JID = self.session.contacts.user_legacy_id
            return

        xmpp_msg.reply.to = cast(Contact, reply_target)
        wa_msg.OriginActor.JID = self._wa_legacy_id()

'''
    return replace_function(
        source,
        r"    def _set_reply_to\(self, xmpp_msg: XMPPMessage, wa_msg: whatsapp\.Message\) -> None:.*?\n\nclass Roster",
        replacement + "class Roster",
        "contact reply builder",
    )


def patch_session_py(source: str) -> str:
    if "_private_group_reply_context(message.OriginActor)" in source:
        return source
    helper = '''\n\ndef _private_group_reply_context(actor: whatsapp.Actor) -> tuple[whatsapp.Actor, str]:
    lid, separator, group_jid = actor.LID.partition("\\x1f")
    if not separator or not group_jid:
        return actor, ""
    return whatsapp.Actor(JID=actor.JID, LID=lid, IsMe=actor.IsMe), group_jid
'''
    old = '''        if self.contacts.user_legacy_id == message.OriginActor.JID:
            reply_to.author = "user"
        else:
            reply_to.author, _muc = await self.__get_contact_or_participant(
                message.Chat, message.OriginActor
            )
'''
    new = '''        reply_actor, quoted_group_jid = _private_group_reply_context(
            message.OriginActor
        )
        if quoted_group_jid:
            quoted_chat = whatsapp.Chat(JID=quoted_group_jid, IsGroup=True)
            reply_to.author, _muc = await self.__get_contact_or_participant(
                quoted_chat, reply_actor
            )
        elif self.contacts.user_legacy_id == reply_actor.JID:
            reply_to.author = "user"
        else:
            reply_to.author, _muc = await self.__get_contact_or_participant(
                message.Chat, reply_actor
            )
'''
    source = replace_once(source, old, new, "inbound reply author resolver")
    return source.replace("\n\ndef add_quote_prefix", helper + "\n\ndef add_quote_prefix", 1)


def patch_event_go(source: str) -> str:
    if "privateReplyGroupSeparator" in source:
        return source
    source = replace_once(
        source,
        "type Actor struct {",
        "const privateReplyGroupSeparator = \"\\x1f\"\n\ntype Actor struct {",
        "private reply context separator",
    )
    return replace_once(
        source,
        "\tmessage.OriginActor = newActor(ctx, client, originJID, remoteJID)\n",
        "\tmessage.OriginActor = newActor(ctx, client, originJID, remoteJID)\n"
        "\tif remoteJID.Server == types.GroupServer && !message.Chat.IsGroup {\n"
        "\t\tmessage.OriginActor.LID += privateReplyGroupSeparator + remoteJID.ToNonAD().String()\n"
        "\t}\n",
        "inbound private group reply context",
    )


def patch_session_go(source: str) -> str:
    if "private group replies retain ContextInfo.RemoteJID" in source:
        return source
    marker = "func (s *Session) getMessagePayload"
    head, separator, payload_builder = source.partition(marker)
    if not separator:
        raise SystemExit("Could not apply private-reply patch: getMessagePayload is missing.")
    payload_builder = replace_once(payload_builder, "\t\tvar participant string\n\t\tif message.Chat.IsGroup {\n\t\t\tparticipant = message.OriginActor.LID\n\t\t} else {\n\t\t\tparticipant = message.OriginActor.JID\n\t\t}\n", "\t\tparticipant := message.OriginActor.JID\n\t\t_, quotedGroupJID, privateGroupReply := strings.Cut(message.OriginActor.LID, privateReplyGroupSeparator)\n\t\tif message.Chat.IsGroup {\n\t\t\tparticipant = message.OriginActor.LID\n\t\t} else if privateGroupReply {\n\t\t\tparticipant, _, _ = strings.Cut(message.OriginActor.LID, privateReplyGroupSeparator)\n\t\t\tif participant == \"\" {\n\t\t\t\tparticipant = message.OriginActor.JID\n\t\t\t}\n\t\t}\n", "quoted participant selection")
    payload_builder = replace_once(payload_builder, "\t\tif participant != \"\" {\n\t\t\tpayload = &waE2E.Message{\n\t\t\t\tExtendedTextMessage: &waE2E.ExtendedTextMessage{\n\t\t\t\t\tText: &message.Body,\n\t\t\t\t\tContextInfo: &waE2E.ContextInfo{\n\t\t\t\t\t\tStanzaID:      &message.ReplyID,\n\t\t\t\t\t\tQuotedMessage: &waE2E.Message{Conversation: ptrTo(message.ReplyBody)},\n\t\t\t\t\t\tParticipant:   &participant,\n\t\t\t\t\t},\n\t\t\t\t},\n\t\t\t}\n", "\t\tif participant != \"\" {\n\t\t\tcontextInfo := &waE2E.ContextInfo{\n\t\t\t\tStanzaID:      &message.ReplyID,\n\t\t\t\tQuotedMessage: &waE2E.Message{Conversation: ptrTo(message.ReplyBody)},\n\t\t\t\tParticipant:   &participant,\n\t\t\t}\n\t\t\t// A direct response to a group message needs the original group.\n\t\t\tif privateGroupReply && quotedGroupJID != \"\" {\n\t\t\t\tcontextInfo.RemoteJID = &quotedGroupJID\n\t\t\t}\n\t\t\tpayload = &waE2E.Message{\n\t\t\t\tExtendedTextMessage: &waE2E.ExtendedTextMessage{\n\t\t\t\t\tText:        &message.Body,\n\t\t\t\t\tContextInfo: contextInfo,\n\t\t\t\t},\n\t\t\t}\n", "quoted WhatsApp context builder")
    return head + separator + payload_builder


def patch_package(site_packages: Path, backup: bool = True) -> bool:
    files = {
        site_packages / "slidge/core/dispatcher/message/message.py": patch_dispatcher,
        site_packages / "slidge_whatsapp/contact.py": patch_contact,
        site_packages / "slidge_whatsapp/session.py": patch_session_py,
        site_packages / "slidge_whatsapp/event.go": patch_event_go,
        site_packages / "slidge_whatsapp/session.go": patch_session_go,
    }
    changed = False
    for path, patcher in files.items():
        source = path.read_text(encoding="utf-8")
        updated = patcher(source)
        if updated != source:
            if backup:
                shutil.copy2(path, path.with_suffix(path.suffix + ".private-replies.bak"))
            path.write_text(updated, encoding="utf-8")
            changed = True
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch Slidge WhatsApp private group replies.")
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    print("Private group reply patch applied." if patch_package(args.site_packages, not args.no_backup) else "Private group reply patch already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
