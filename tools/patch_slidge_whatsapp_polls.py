from __future__ import annotations

# ruff: noqa: E501, I001

import argparse
import shutil
from pathlib import Path


POLL_NAMESPACE = "urn:marco-ml:whatsapp:poll:0"


def _replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Could not patch {description}: expected one match, found {count}.")
    return text.replace(old, new, 1)


def _write(path: Path, text: str, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-polls")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_message_text(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "extra_xml: ET.Element | None = None" in source:
        return False
    if "from xml.etree import ElementTree as ET\n" not in source:
        source = _replace_once(
            source,
            "from datetime import datetime\n",
            "from datetime import datetime\nfrom xml.etree import ElementTree as ET\n",
            "message_text imports",
        )
    source = _replace_once(
        source,
        "        is_forwarded: bool = False,\n        **send_kwargs: object,\n",
        "        is_forwarded: bool = False,\n        extra_xml: ET.Element | None = None,\n        **send_kwargs: object,\n",
        "send_text signature",
    )
    source = _replace_once(
        source,
        "        add_whatsapp_forwarded_flag(msg, is_forwarded)\n        if correction:\n",
        "        add_whatsapp_forwarded_flag(msg, is_forwarded)\n        if extra_xml is not None:\n            msg.xml.append(extra_xml)\n        if correction:\n",
        "send_text XML insertion",
    )
    _write(path, source, backup)
    return True


def patch_session_py(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "POLL_NAMESPACE = \"urn:marco-ml:whatsapp:poll:0\"" in source:
        return False
    source = _replace_once(
        source,
        "from typing import Any, Concatenate, ParamSpec, TypeVar, cast\n",
        "from typing import Any, Concatenate, ParamSpec, TypeVar, cast\nfrom xml.etree import ElementTree as ET\n",
        "session.py imports",
    )
    source = _replace_once(
        source,
        "Recipient = Contact | MUC\n\n\n",
        "Recipient = Contact | MUC\n\nPOLL_NAMESPACE = \"urn:marco-ml:whatsapp:poll:0\"\n\n\n",
        "session.py poll namespace",
    )
    old = '''    async def on_wa_msg_poll(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        body = f"🗳 {message.Poll.Title}"
        for option in message.Poll.Options:
            body = body + f"\\n☐ {option.Title}"
        actor.send_text(
            body=body,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
        )
'''
    new = '''    async def on_wa_msg_poll(
        self, message: whatsapp.Message, actor: Contact | Participant, muc: MUC | None
    ) -> None:
        body = f"🗳 {message.Poll.Title}"
        option_titles = [option.Title.strip() for option in message.Poll.Options]
        for option in option_titles:
            body = body + f"\\n☐ {option}"

        poll_xml: ET.Element | None = None
        if message.ID and message.Poll.Title.strip() and all(option_titles):
            max_selections = int(message.Body or "0")
            if max_selections < 1 or max_selections > len(option_titles):
                max_selections = len(option_titles)
            attrs = {
                "id": message.ID,
                "title": message.Poll.Title,
                "creator": message.Actor.JID,
                "creator-is-me": str(message.Actor.IsMe).lower(),
                "max-selections": str(max_selections),
            }
            if message.Actor.LID:
                attrs["creator-lid"] = message.Actor.LID
            if attrs["creator"] and (muc is None or attrs.get("creator-lid")):
                poll_xml = ET.Element(f"{{{POLL_NAMESPACE}}}poll", attrs)
                for option in option_titles:
                    ET.SubElement(poll_xml, f"{{{POLL_NAMESPACE}}}option").text = option

        actor.send_text(
            body=body,
            legacy_msg_id=message.ID,
            reply_to=await self.__get_reply_to(message, muc),
            when=self.__get_timestamp(message),
            carbon=message.Actor.IsMe,
            extra_xml=poll_xml,
        )
'''
    source = _replace_once(source, old, new, "on_wa_msg_poll")
    _write(path, source, backup)
    return True


def patch_dispatcher(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "POLL_NAMESPACE = \"urn:marco-ml:whatsapp:poll:0\"" in source:
        return False
    source = _replace_once(
        source,
        "if TYPE_CHECKING:\n    from slidge.util.types import AnyGateway\n\n\n",
        "if TYPE_CHECKING:\n    from slidge.util.types import AnyGateway\n\n\nPOLL_NAMESPACE = \"urn:marco-ml:whatsapp:poll:0\"\nPOLL_LOG = logging.getLogger(__name__)\nMAX_POLL_OPTIONS = 12\nMAX_POLL_VALUE_LENGTH = 1024\n\n\n",
        "dispatcher poll constants",
    )
    source = _replace_once(
        source,
        "        recipient, thread = await self._get_recipient_and_thread(msg)\n        replace = await self.__get_replace(msg, recipient)\n",
        "        recipient, thread = await self._get_recipient_and_thread(msg)\n        if msg.xml.find(f\"{{{POLL_NAMESPACE}}}vote\") is not None:\n            await self.__dispatch_poll_vote(msg, recipient)\n            return\n        replace = await self.__get_replace(msg, recipient)\n",
        "dispatcher poll branch",
    )
    marker = "    def __get_xhtml_sticker_cid(self, msg: Message) -> str | None:\n"
    helper = '''    async def __dispatch_poll_vote(
        self, msg: Message, recipient: AnyRecipient
    ) -> None:
        vote = msg.xml.find(f"{{{POLL_NAMESPACE}}}vote")
        if vote is None:
            raise XMPPError("bad-request", "Missing poll vote payload")

        poll_id = (vote.get("id") or "").strip()
        creator = (vote.get("creator") or "").strip()
        creator_lid = (vote.get("creator-lid") or "").strip()
        creator_is_me = (vote.get("creator-is-me") or "").strip().lower()
        if not poll_id or not creator:
            raise XMPPError("bad-request", "A poll vote requires its ID and creator")
        if recipient.is_group and not creator_lid:
            raise XMPPError("bad-request", "A group poll vote requires creator-lid")
        if creator_is_me not in {"true", "false"}:
            raise XMPPError("bad-request", "Invalid creator-is-me value")
        if any(len(value) > MAX_POLL_VALUE_LENGTH for value in (poll_id, creator, creator_lid)):
            raise XMPPError("bad-request", "Poll vote metadata is too long")

        options = [
            (option.text or "").strip()
            for option in vote.findall(f"{{{POLL_NAMESPACE}}}option")
        ]
        if (
            not options
            or len(options) > MAX_POLL_OPTIONS
            or any(not option or len(option) > MAX_POLL_VALUE_LENGTH for option in options)
            or len(set(options)) != len(options)
        ):
            raise XMPPError("bad-request", "Invalid poll vote options")

        POLL_LOG.info("Received poll vote id=%s option_count=%d", poll_id, len(options))
        await recipient.on_poll_vote(
            poll_id=poll_id,
            creator=creator,
            creator_lid=creator_lid,
            creator_is_me=creator_is_me == "true",
            options=options,
        )
        POLL_LOG.info("Forwarded poll vote id=%s to WhatsApp", poll_id)
        self.__ack(msg)

'''
    source = _replace_once(source, marker, helper + marker, "dispatcher poll helper")
    _write(path, source, backup)
    return True


def patch_mixins(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "async def on_poll_vote(" in source:
        return False
    marker = "    async def _on_text(self, xmpp_msg: XMPPMessage) -> str:\n"
    helper = '''    async def on_poll_vote(
        self,
        *,
        poll_id: str,
        creator: str,
        creator_lid: str,
        creator_is_me: bool,
        options: list[str],
    ) -> str:
        chat = self.get_wa_chat()
        if chat.IsGroup and not creator_lid:
            raise XMPPError("bad-request", "A group poll vote requires creator-lid")
        if not chat.IsGroup and not creator:
            raise XMPPError("bad-request", "A direct poll vote requires creator")
        poll_options = whatsapp.Slice_whatsapp_PollOption(
            [whatsapp.PollOption(Title=option) for option in options]
        )
        message = whatsapp.Message(
            Kind=whatsapp.MessagePoll,
            ID=poll_id,
            Chat=chat,
            Actor=whatsapp.Actor(
                JID=creator,
                LID=creator_lid,
                IsMe=creator_is_me,
            ),
            Poll=whatsapp.Poll(Options=poll_options),
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
        return poll_id

'''
    source = _replace_once(source, marker, helper + marker, "RecipientMixin poll vote")
    _write(path, source, backup)
    return True


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "strconv.Itoa(int(p.GetSelectableOptionsCount()))" in source:
        return False
    source = _replace_once(
        source,
        '\t"slices"\n\t"strings"\n',
        '\t"slices"\n\t"strconv"\n\t"strings"\n',
        "event.go strconv import",
    )
    source = _replace_once(
        source,
        "\t\tmessage.Kind = MessagePoll\n\t\tmessage.Poll = Poll{Title: p.GetName()}\n",
        "\t\tmessage.Kind = MessagePoll\n\t\tmessage.Body = strconv.Itoa(int(p.GetSelectableOptionsCount()))\n\t\tmessage.Poll = Poll{Title: p.GetName()}\n",
        "event.go poll selection count",
    )
    _write(path, source, backup)
    return True


def patch_session_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "func pollVoteInfo(message Message, chat types.JID)" in source:
        return False
    marker = "// SendMessage processes the given Message and sends a WhatsApp message for the kind and contact JID\n"
    helper = '''func pollVoteInfo(message Message, chat types.JID) (*types.MessageInfo, []string, error) {
	if message.ID == "" {
		return nil, nil, fmt.Errorf("cannot vote without a poll message ID")
	}
	if len(message.Poll.Options) == 0 || len(message.Poll.Options) > 12 {
		return nil, nil, fmt.Errorf("invalid number of poll vote options")
	}

	creator := message.Actor.JID
	if message.Chat.IsGroup {
		creator = message.Actor.LID
	} else if message.Actor.IsMe && message.Actor.LID != "" {
		// A poll created from another one of our devices stores its creator as our
		// LID, even in a direct chat.
		creator = message.Actor.LID
	}
	if creator == "" {
		return nil, nil, fmt.Errorf("missing poll creator identity")
	}
	sender, err := types.ParseJID(creator)
	if err != nil {
		return nil, nil, fmt.Errorf("could not parse poll creator JID: %w", err)
	}

	options := make([]string, 0, len(message.Poll.Options))
	seen := make(map[string]struct{}, len(message.Poll.Options))
	for _, option := range message.Poll.Options {
		if option.Title == "" || len(option.Title) > 1024 {
			return nil, nil, fmt.Errorf("invalid poll vote option")
		}
		if _, duplicate := seen[option.Title]; duplicate {
			return nil, nil, fmt.Errorf("duplicate poll vote option")
		}
		seen[option.Title] = struct{}{}
		options = append(options, option.Title)
	}

	return &types.MessageInfo{
		MessageSource: types.MessageSource{
			Chat:     chat,
			Sender:   sender,
			IsFromMe: message.Actor.IsMe,
			IsGroup:  message.Chat.IsGroup,
		},
		ID: types.MessageID(message.ID),
	}, options, nil
}

'''
    source = _replace_once(source, marker, helper + marker, "session.go poll vote helper")
    source = _replace_once(
        source,
        "\tcase MessageReaction:\n\t\t// Send message as emoji reaction to a given message.\n",
        "\tcase MessagePoll:\n\t\tpollInfo, optionNames, pollErr := pollVoteInfo(message, jid)\n\t\tif pollErr != nil {\n\t\t\treturn pollErr\n\t\t}\n\t\tif message.Actor.IsMe && !message.Chat.IsGroup && message.Actor.LID != \"\" {\n\t\t\t// Poll secrets for a direct chat authored on another own device are\n\t\t\t// indexed by the recipient LID, while the wire send still targets jid.\n\t\t\trecipientLID, lidErr := s.client.Store.LIDs.GetLIDForPN(s.ctx, pollInfo.Chat)\n\t\t\tif lidErr != nil {\n\t\t\t\treturn fmt.Errorf(\"failed to resolve direct poll recipient LID: %w\", lidErr)\n\t\t\t}\n\t\t\tif recipientLID.IsEmpty() {\n\t\t\t\treturn fmt.Errorf(\"missing direct poll recipient LID\")\n\t\t\t}\n\t\t\tpollInfo.Chat = recipientLID\n\t\t}\n\t\tpayload, err = s.client.BuildPollVote(s.ctx, pollInfo, optionNames)\n\t\tif err != nil {\n\t\t\treturn fmt.Errorf(\"failed building poll vote: %w\", err)\n\t\t}\n\tcase MessageReaction:\n\t\t// Send message as emoji reaction to a given message.\n",
        "session.go poll vote SendMessage case",
    )
    _write(path, source, backup)
    return True


def patch_package(package_root: Path, *, backup: bool) -> bool:
    package_root = package_root.resolve()
    targets = {
        "message_text": package_root / "slidge/core/mixins/message_text.py",
        "dispatcher": package_root / "slidge/core/dispatcher/message/message.py",
        "session_py": package_root / "slidge_whatsapp/session.py",
        "mixins": package_root / "slidge_whatsapp/mixins.py",
        "event_go": package_root / "slidge_whatsapp/event.go",
        "session_go": package_root / "slidge_whatsapp/session.go",
    }
    missing = [str(path) for path in targets.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changes = (
        patch_message_text(targets["message_text"], backup=backup),
        patch_dispatcher(targets["dispatcher"], backup=backup),
        patch_session_py(targets["session_py"], backup=backup),
        patch_mixins(targets["mixins"], backup=backup),
        patch_event_go(targets["event_go"], backup=backup),
        patch_session_go(targets["session_go"], backup=backup),
    )
    return any(changes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add WhatsApp poll metadata and encrypted vote forwarding to Slidge."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.package_root, backup=not args.no_backup)
    print("WhatsApp poll patch applied." if changed else "WhatsApp poll patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
