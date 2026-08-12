from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

# Long lines below intentionally mirror exact upstream Go/Python source anchors.
# ruff: noqa: E501


POLL_NAMESPACE = "urn:marco-ml:whatsapp:poll:0"


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not apply poll patch: expected one {description}, found {count}."
        )
    return text.replace(old, new, 1)


def replace_function(
    text: str, pattern: str, replacement: str, description: str
) -> str:
    updated, count = re.subn(
        pattern, lambda _match: replacement, text, count=1, flags=re.DOTALL
    )
    if count != 1:
        raise SystemExit(
            f"Could not apply poll patch: expected one {description}, found {count}."
        )
    return updated


def patch_event_go(text: str) -> str:
    if "func setPollUpdateMessage(" in text:
        return text
    text = replace_once(
        text,
        '\t"context"\n\t"fmt"',
        '\t"context"\n\t"encoding/hex"\n\t"fmt"',
        "event.go hex import anchor",
    )
    text = replace_once(
        text,
        '''\tfor _, p := range []*waE2E.PollCreationMessage{
\t\tevt.Message.GetPollCreationMessageV3(),
\t\tevt.Message.GetPollCreationMessageV2(),
\t\tevt.Message.GetPollCreationMessage(),
\t} {
\t\tif p == nil {
\t\t\tcontinue
\t\t}
\t\tmessage.Kind = MessagePoll
\t\tmessage.Poll = Poll{Title: p.GetName()}''',
        '''\tpollVersions := []struct {
\t\tpoll              *waE2E.PollCreationMessage
\t\tzeroValueFallback int
\t}{
\t\t{evt.Message.GetPollCreationMessageV3(), 1},
\t\t{evt.Message.GetPollCreationMessageV2(), 1},
\t\t{evt.Message.GetPollCreationMessage(), 0},
\t}
\tfor _, version := range pollVersions {
\t\tp := version.poll
\t\tif p == nil {
\t\t\tcontinue
\t\t}
\t\tmessage.Kind = MessagePoll
\t\tselectableCount := pollSelectableCount(p, version.zeroValueFallback)
\t\tselectionMode := pollSelectionMode(p, version.zeroValueFallback)
\t\tmessage.Body = fmt.Sprintf("%s:%d", selectionMode, selectableCount)
\t\tmessage.Poll = Poll{Title: p.GetName()}''',
        "incoming poll conversion",
    )
    helper = r'''func pollSelectableCount(poll *waE2E.PollCreationMessage, zeroValueFallback int) int {
	optionCount := len(poll.GetOptions())
	selectableCount := int(poll.GetSelectableOptionsCount())
	if selectableCount <= 0 || selectableCount > optionCount {
		selectableCount = zeroValueFallback
	}
	if selectableCount <= 0 || selectableCount > optionCount {
		selectableCount = optionCount
	}
	return selectableCount
}

func pollSelectionMode(poll *waE2E.PollCreationMessage, zeroValueFallback int) string {
	selectableCount := int(poll.GetSelectableOptionsCount())
	if selectableCount > 1 || (selectableCount == 0 && zeroValueFallback == 0) {
		return "multiple"
	}
	return "single"
}

func setPollUpdateMessage(message *Message, update *waE2E.PollUpdateMessage, vote *waE2E.PollVoteMessage) bool {
	if update == nil || vote == nil {
		return false
	}
	referenceID := update.GetPollCreationMessageKey().GetID()
	if referenceID == "" {
		return false
	}

	message.Kind = MessagePoll
	message.ReferenceID = referenceID
	message.Poll = Poll{}
	for _, optionHash := range vote.GetSelectedOptions() {
		message.Poll.Options = append(message.Poll.Options, PollOption{
			Title: hex.EncodeToString(optionHash),
		})
	}
	return true
}

'''
    text = replace_once(
        text,
        "// NewMessageEvent returns event data meant for [Session.propagateEvent] for the primive message\n",
        helper
        + "// NewMessageEvent returns event data meant for [Session.propagateEvent] for the primive message\n",
        "poll update helper anchor",
    )
    update_handler = '''\tif update := evt.Message.GetPollUpdateMessage(); update != nil {
\t\tclient.Log.Infof(
\t\t\t"Received WhatsApp poll update event=%s reference=%s chat=%s sender=%s from_me=%t",
\t\t\tmessage.ID,
\t\t\tupdate.GetPollCreationMessageKey().GetID(),
\t\t\tmessage.Chat.JID,
\t\t\tmessage.Actor.JID,
\t\t\tmessage.Actor.IsMe,
\t\t)
\t\tvote, err := client.DecryptPollVote(ctx, evt)
\t\tif err != nil {
\t\t\tclient.Log.Warnf("Failed decrypting poll vote '%s': %s", message.ID, err)
\t\t\treturn EventUnknown, nil
\t\t}
\t\tif !setPollUpdateMessage(&message, update, vote) {
\t\t\treturn EventUnknown, nil
\t\t}
\t\treturn EventMessage, &EventPayload{Message: message}
\t}

'''
    text = replace_once(
        text,
        "\t// Handle poll messages.\n",
        "\t// Handle poll vote updates before poll creation messages.\n"
        + update_handler
        + "\t// Handle poll messages.\n",
        "live poll update handler anchor",
    )
    history_handler = '''\t// Reuse the live parser for poll messages so archived group polls and vote updates keep
\t// the same metadata and decryption behavior after a bridge restart.
\trawMessage := info.GetMessage()
\tif rawMessage.GetPollUpdateMessage() != nil ||
\t\trawMessage.GetPollCreationMessageV3() != nil ||
\t\trawMessage.GetPollCreationMessageV2() != nil ||
\t\trawMessage.GetPollCreationMessage() != nil {
\t\tchatJID, err := types.ParseJID(jid)
\t\tif err != nil {
\t\t\treturn EventUnknown, nil
\t\t}
\t\tevt, err := client.ParseWebMessage(chatJID, info)
\t\tif err != nil {
\t\t\tclient.Log.Warnf("Failed parsing historical poll message '%s': %s", info.GetKey().GetID(), err)
\t\t\treturn EventUnknown, nil
\t\t}
\t\tkind, payload := newMessageEvent(ctx, client, evt)
\t\tif payload != nil {
\t\t\tpayload.Message.IsHistory = true
\t\t}
\t\treturn kind, payload
\t}

'''
    return replace_once(
        text,
        "\tif j, _ := types.ParseJID(jid); j.Server != types.GroupServer {\n"
        "\t\treturn EventUnknown, nil\n"
        "\t}\n\n"
        "\t// Set basic data for message, to be potentially amended depending on the concrete version of\n",
        "\tif j, _ := types.ParseJID(jid); j.Server != types.GroupServer {\n"
        "\t\treturn EventUnknown, nil\n"
        "\t}\n\n"
        + history_handler
        + "\t// Set basic data for message, to be potentially amended depending on the concrete version of\n",
        "historical poll handler anchor",
    )


def patch_message_text(text: str) -> str:
    if "extra_xml: ET.Element | None = None" in text:
        return text
    text = replace_once(
        text,
        "        is_forwarded: bool = False,\n        **send_kwargs: object,",
        "        is_forwarded: bool = False,\n"
        "        extra_xml: ET.Element | None = None,\n"
        "        **send_kwargs: object,",
        "send_text extra_xml parameter",
    )
    text = replace_once(
        text,
        "        add_whatsapp_forwarded_flag(msg, is_forwarded)\n        if correction:",
        "        add_whatsapp_forwarded_flag(msg, is_forwarded)\n"
        "        if extra_xml is not None:\n"
        "            msg.xml.append(extra_xml)\n"
        "        if correction:",
        "send_text XML append",
    )
    return text


def patch_session_py(text: str) -> str:
    if 'f"{{{POLL_NAMESPACE}}}poll-update"' in text:
        return text
    text = replace_once(
        text,
        "from urllib.parse import quote as url_quote\n",
        "from urllib.parse import quote as url_quote\n"
        "from xml.etree import ElementTree as ET\n",
        "session.py ElementTree import",
    )
    text = replace_once(
        text,
        "Recipient = Contact | MUC\n",
        f'Recipient = Contact | MUC\n\nPOLL_NAMESPACE = "{POLL_NAMESPACE}"\n',
        "session.py namespace anchor",
    )
    replacement = r'''    async def on_wa_msg_poll(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        if message.ReferenceID:
            self.log.info(
                "Forwarding WhatsApp poll update event=%s reference=%s actor=%s own=%s muc=%s",
                message.ID,
                message.ReferenceID,
                message.Actor.JID,
                message.Actor.IsMe,
                muc is not None,
            )
            update_xml = ET.Element(
                f"{{{POLL_NAMESPACE}}}poll-update",
                {
                    "id": message.ReferenceID,
                    "voter": message.Actor.JID,
                    "voter-is-me": str(message.Actor.IsMe).lower(),
                },
            )
            if message.Actor.LID:
                update_xml.set("voter-lid", message.Actor.LID)
            for option in message.Poll.Options:
                ET.SubElement(
                    update_xml,
                    f"{{{POLL_NAMESPACE}}}option",
                    {"hash": option.Title},
                )
            actor.send_text(
                # Keep a whitespace body because some XMPP routing layers discard
                # extension-only messages. The client strips it before rendering.
                body=" ",
                legacy_msg_id=message.ID,
                when=self.__get_timestamp(message),
                carbon=message.Actor.IsMe,
                extra_xml=update_xml,
            )
            self.log.info(
                "Forwarded WhatsApp poll update event=%s reference=%s to XMPP",
                message.ID,
                message.ReferenceID,
            )
            return

        fallback_body = f"\U0001f5f3 {message.Poll.Title}"
        options = [option.Title for option in message.Poll.Options if option.Title]
        for option in options:
            fallback_body = fallback_body + f"\n\u2610 {option}"

        selection_mode, separator, selectable_value = (message.Body or "").partition(":")
        if not separator or selection_mode not in {"single", "multiple"}:
            selection_mode = "single"
            selectable_value = "1"
        try:
            selectable_count = int(selectable_value)
        except (TypeError, ValueError):
            selectable_count = 1
        if selectable_count <= 0 or selectable_count > len(options):
            selectable_count = len(options) if selection_mode == "multiple" else 1
        if selection_mode == "single":
            selectable_count = 1

        poll_xml = ET.Element(
            f"{{{POLL_NAMESPACE}}}poll",
            {
                "id": message.ID,
                "title": message.Poll.Title,
                "creator": message.Actor.JID,
                "creator-is-me": str(message.Actor.IsMe).lower(),
                "max-selections": str(selectable_count),
                "selection-mode": selection_mode,
            },
        )
        if message.Actor.LID:
            poll_xml.set("creator-lid", message.Actor.LID)
        for option in options:
            ET.SubElement(poll_xml, f"{{{POLL_NAMESPACE}}}option").text = option

        actor.send_text(
            body=fallback_body,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            extra_xml=poll_xml,
        )
'''
    return replace_function(
        text,
        r"    async def on_wa_msg_poll\(.*?(?=\n    async def on_wa_avatar)",
        replacement.rstrip(),
        "session.py on_wa_msg_poll",
    )


def patch_dispatcher(text: str) -> str:
    if "await recipient.on_poll_vote(" in text:
        return text
    text = replace_once(
        text,
        "\n\n@dataclass\nclass _IncomingAttachment:",
        f'\n\nWHATSAPP_POLL_NAMESPACE = "{POLL_NAMESPACE}"\n'
        "WHATSAPP_POLL_LOG = logging.getLogger(__name__)\n\n\n"
        "@dataclass\nclass _IncomingAttachment:",
        "dispatcher namespace anchor",
    )
    vote_dispatch = '''        vote = msg.xml.find(f"{{{WHATSAPP_POLL_NAMESPACE}}}vote")
        if vote is not None:
            poll_id = vote.attrib.get("id", "").strip()
            creator = vote.attrib.get("creator", "").strip()
            creator_lid = vote.attrib.get("creator-lid", "").strip()
            creator_is_me_value = vote.attrib.get("creator-is-me", "false").lower()
            if not poll_id or not creator:
                raise XMPPError(
                    "bad-request", "Poll votes require a poll ID and creator."
                )
            if creator_is_me_value not in {"true", "false"}:
                raise XMPPError("bad-request", "Invalid poll creator-is-me value.")
            if recipient.is_group and not creator_lid:
                raise XMPPError(
                    "bad-request", "Group poll votes require the creator LID."
                )

            options = [
                (option.text or "").strip()
                for option in vote.findall(f"{{{WHATSAPP_POLL_NAMESPACE}}}option")
            ]
            if not 1 <= len(options) <= 12 or any(not option for option in options):
                raise XMPPError("bad-request", "Invalid poll vote options.")
            if len(set(options)) != len(options):
                raise XMPPError("bad-request", "Poll vote options must be unique.")

            WHATSAPP_POLL_LOG.info(
                "Received poll vote id=%s option_count=%d", poll_id, len(options)
            )
            await recipient.on_poll_vote(
                poll_id=poll_id,
                creator=creator,
                creator_lid=creator_lid,
                creator_is_me=creator_is_me_value == "true",
                options=options,
            )
            WHATSAPP_POLL_LOG.info("Forwarded poll vote id=%s to WhatsApp", poll_id)

            confirmation = msg.reply(clear=True)
            # Keep a whitespace body because some XMPP routing layers discard
            # extension-only messages. The client strips it before rendering.
            confirmation["body"] = " "
            update_xml = ElementTree.Element(
                f"{{{WHATSAPP_POLL_NAMESPACE}}}poll-update",
                {
                    "id": poll_id,
                    "voter": "me",
                    "voter-is-me": "true",
                },
            )
            for option in options:
                ElementTree.SubElement(
                    update_xml,
                    f"{{{WHATSAPP_POLL_NAMESPACE}}}option",
                    {"hash": hashlib.sha256(option.encode("utf-8")).hexdigest()},
                )
            confirmation.append(update_xml)
            confirmation.append(ElementTree.Element("{urn:xmpp:hints}store"))
            confirmation.send()
            WHATSAPP_POLL_LOG.info(
                "Confirmed poll vote id=%s stanza_id=%s type=%s to=%s from=%s",
                poll_id,
                msg["id"],
                confirmation["type"],
                confirmation["to"],
                confirmation["from"],
            )
            self.__ack(msg)
            return
'''
    return replace_once(
        text,
        "        recipient, thread = await self._get_recipient_and_thread(msg)\n"
        "        replace = await self.__get_replace(msg, recipient)",
        "        recipient, thread = await self._get_recipient_and_thread(msg)\n"
        + vote_dispatch
        + "        replace = await self.__get_replace(msg, recipient)",
        "outgoing vote dispatch",
    )


def patch_mixins(text: str) -> str:
    if "async def on_poll_vote(" in text:
        return text
    method = '''    async def on_poll_vote(
        self,
        *,
        poll_id: str,
        creator: str,
        creator_lid: str,
        creator_is_me: bool,
        options: list[str],
    ) -> None:
        chat = self.get_wa_chat()
        if chat.IsGroup and not creator_lid:
            raise XMPPError("bad-request", "Group poll votes require the creator LID.")
        if not chat.IsGroup and not creator:
            raise XMPPError("bad-request", "Direct poll votes require the creator JID.")

        poll_options = whatsapp.Slice_whatsapp_PollOption(  # type:ignore[no-untyped-call]
            [whatsapp.PollOption(Title=option) for option in options]  # type:ignore[no-untyped-call]
        )
        message = whatsapp.Message(  # type:ignore[no-untyped-call]
            Kind=whatsapp.MessagePoll,
            ID=poll_id,
            Actor=whatsapp.Actor(  # type:ignore[no-untyped-call]
                JID=creator,
                LID=creator_lid,
                IsMe=creator_is_me,
            ),
            Chat=chat,
            Poll=whatsapp.Poll(Options=poll_options),  # type:ignore[no-untyped-call]
        )
        self.log.info(
            "Building WhatsApp poll vote id=%s own_creator=%s option_count=%d",
            poll_id,
            creator_is_me,
            len(options),
        )
        try:
            self.wa.SendMessage(message)  # type:ignore[no-untyped-call]
        except Exception:
            self.log.exception("WhatsApp poll vote failed id=%s", poll_id)
            raise
        self.log.info("WhatsApp poll vote accepted id=%s", poll_id)

'''
    return replace_once(
        text,
        "    async def on_message(self, message: XMPPMessage) -> str | None:\n",
        method + "    async def on_message(self, message: XMPPMessage) -> str | None:\n",
        "RecipientMixin poll method anchor",
    )


def patch_session_go(text: str) -> str:
    if "func pollVoteMessageInfo(" in text:
        return text
    text = replace_once(
        text,
        '\t"slices"\n\t"sync"',
        '\t"slices"\n\t"strings"\n\t"sync"',
        "session.go strings import",
    )
    helper = r'''func pollVoteMessageInfo(message Message, chatJID types.JID) (*types.MessageInfo, []string, error) {
	if message.ID == "" {
		return nil, nil, fmt.Errorf("poll vote requires a poll ID")
	}
	if message.Chat.IsGroup != (chatJID.Server == types.GroupServer) {
		return nil, nil, fmt.Errorf("poll vote chat kind does not match JID '%s'", chatJID)
	}

	creator := message.Actor.JID
	pollChat := chatJID
	if message.Chat.IsGroup {
		if message.Actor.LID == "" {
			return nil, nil, fmt.Errorf("group poll vote requires the creator LID")
		}
		creator = message.Actor.LID
	} else if message.Actor.LID != "" {
		creator = message.Actor.LID
	}
	if creator == "" {
		return nil, nil, fmt.Errorf("poll vote requires the creator identity")
	}
	sender, err := types.ParseJID(creator)
	if err != nil {
		return nil, nil, fmt.Errorf("could not parse poll creator identity '%s': %v", creator, err)
	}
	if !message.Chat.IsGroup && !message.Actor.IsMe && message.Actor.LID != "" {
		// Incoming direct polls are commonly stored under the contact LID.
		// Preserve that exact chat key for both secret lookup and encryption.
		pollChat = sender
	}

	if len(message.Poll.Options) < 1 || len(message.Poll.Options) > 12 {
		return nil, nil, fmt.Errorf("poll vote requires between 1 and 12 options")
	}
	optionNames := make([]string, 0, len(message.Poll.Options))
	seen := make(map[string]struct{}, len(message.Poll.Options))
	for _, option := range message.Poll.Options {
		if strings.TrimSpace(option.Title) == "" {
			return nil, nil, fmt.Errorf("poll vote options cannot be empty")
		}
		if _, ok := seen[option.Title]; ok {
			return nil, nil, fmt.Errorf("poll vote options cannot be duplicated")
		}
		seen[option.Title] = struct{}{}
		optionNames = append(optionNames, option.Title)
	}

	return &types.MessageInfo{
		MessageSource: types.MessageSource{
			Chat:     pollChat,
			Sender:   sender,
			IsFromMe: message.Actor.IsMe,
			IsGroup:  message.Chat.IsGroup,
		},
		ID: types.MessageID(message.ID),
	}, optionNames, nil
}

'''
    text = replace_once(
        text,
        "// SendMessage processes the given Message and sends a WhatsApp message for the kind and contact JID\n",
        helper
        + "// SendMessage processes the given Message and sends a WhatsApp message for the kind and contact JID\n",
        "session.go SendMessage anchor",
    )
    poll_case = '''\tcase MessagePoll:
\t\tpollInfo, optionNames, pollErr := pollVoteMessageInfo(message, jid)
\t\tif pollErr != nil {
\t\t\treturn pollErr
\t\t}
\t\tif message.Actor.IsMe && !message.Chat.IsGroup && message.Actor.LID != "" {
\t\t\trecipientLID, lidErr := s.client.Store.LIDs.GetLIDForPN(s.ctx, pollInfo.Chat)
\t\t\tif lidErr != nil {
\t\t\t\treturn fmt.Errorf("failed to resolve direct poll recipient LID: %s", lidErr)
\t\t\t}
\t\t\tif recipientLID.IsEmpty() {
\t\t\t\treturn fmt.Errorf("missing direct poll recipient LID")
\t\t\t}
\t\t\tpollInfo.Chat = recipientLID
\t\t}
\t\tpayload, err = s.client.BuildPollVote(s.ctx, pollInfo, optionNames)
\t\tif err != nil {
\t\t\treturn fmt.Errorf("failed building poll vote: %s", err)
\t\t}
\t\t// Whatsmeow requires the encrypted vote to be sent to the same chat key
\t\t// used by the original poll secret. For LID-addressed direct polls this
\t\t// intentionally differs from the XMPP contact's phone-number JID.
\t\tjid = pollInfo.Chat
\t\ts.gateway.logger.Infof(
\t\t\t"Sending poll vote id=%s target=%s creator=%s",
\t\t\tmessage.ID, jid, pollInfo.Sender,
\t\t)
'''
    return replace_once(
        text,
        "\tcase MessageReaction:\n",
        poll_case + "\tcase MessageReaction:\n",
        "session.go MessagePoll switch case",
    )


PATCHERS = {
    Path("slidge_whatsapp/event.go"): patch_event_go,
    Path("slidge_whatsapp/session.go"): patch_session_go,
    Path("slidge_whatsapp/session.py"): patch_session_py,
    Path("slidge_whatsapp/mixins.py"): patch_mixins,
    Path("slidge/core/mixins/message_text.py"): patch_message_text,
    Path("slidge/core/dispatcher/message/message.py"): patch_dispatcher,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the private XMPP contract for native WhatsApp poll voting."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    package_root = args.package_root.resolve()

    changed: list[str] = []
    for relative, patcher in PATCHERS.items():
        target = package_root / relative
        if not target.is_file():
            raise SystemExit(f"File not found: {target}")
        original = target.read_text(encoding="utf-8")
        updated = patcher(original)
        if updated == original:
            continue
        if not args.no_backup:
            backup = target.with_suffix(target.suffix + ".before-polls-v15")
            if not backup.exists():
                shutil.copy2(target, backup)
        target.write_text(updated, encoding="utf-8", newline="\n")
        changed.append(str(relative))

    if changed:
        print("WhatsApp poll patch applied: " + ", ".join(changed))
    else:
        print("WhatsApp poll patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
