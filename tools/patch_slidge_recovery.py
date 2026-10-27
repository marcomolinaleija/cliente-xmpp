"""Guarded fixes for the deployed payload, read recovery and SQL reader paths."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

# ruff: noqa: E501 -- exact upstream anchors and native source templates


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(f"Recovery patch expected {count} anchors, found {source.count(old)}")
    return source.replace(old, new)


def session_source(source):
    if "// CAN payload composition v1" not in source:
        start = source.index("func (s *Session) getMessagePayload(")
        end = source.index("// GenerateMessageID", start)
        method = source[start:end]
        method = replace(method, "\tvar payload *waE2E.Message\n",
                         "\t// CAN payload composition v1\n\tvar payload *waE2E.Message\n")
        old = ("\t\tif payload == nil {\n"
               "\t\t\tpayload = &waE2E.Message{ExtendedTextMessage: "
               "&waE2E.ExtendedTextMessage{Text: &message.Body}}\n\t\t}\n")
        new = ("\t\tif payload == nil { payload = &waE2E.Message{} }\n"
               "\t\tif payload.ExtendedTextMessage == nil {\n"
               "\t\t\tpayload.Conversation = nil\n"
               "\t\t\tpayload.ExtendedTextMessage = "
               "&waE2E.ExtendedTextMessage{Text: &message.Body}\n\t\t}\n")
        method = replace(method, old, new, count=2)
        old = ("\tif message.Location.Latitude > 0 || message.Location.Longitude > 0 {\n"
               "\t\tif payload == nil {\n"
               "\t\t\tpayload = &waE2E.Message{LocationMessage: &waE2E.LocationMessage{}}\n"
               "\t\t}\n"
               "\t\tpayload.LocationMessage.DegreesLatitude = &message.Location.Latitude\n"
               "\t\tpayload.LocationMessage.DegreesLongitude = &message.Location.Longitude\n"
               "\t\tpayload.LocationMessage.AccuracyInMeters = ptrTo(uint32(message.Location.Accuracy))\n"
               "\t}\n")
        new = ("\tif message.Location.Latitude != 0 || message.Location.Longitude != 0 {\n"
               "\t\t// A location is the content; keep reply/mention context, not a second body.\n"
               "\t\tcontextInfo := payload.GetExtendedTextMessage().GetContextInfo()\n"
               "\t\tpayload = &waE2E.Message{LocationMessage: &waE2E.LocationMessage{\n"
               "\t\t\tDegreesLatitude: &message.Location.Latitude,\n"
               "\t\t\tDegreesLongitude: &message.Location.Longitude,\n"
               "\t\t\tAccuracyInMeters: ptrTo(uint32(message.Location.Accuracy)),\n"
               "\t\t\tContextInfo: contextInfo,\n\t\t}}\n\t}\n")
        method = replace(method, old, new)
        source = source[:start] + method + source[end:]
    if "// CAN primary snapshot recovery v1" not in source:
        start = source.index("func (s *Session) MarkChatRead(")
        end = source.index("\n}\n", start) + 3
        method = source[start:end]
        old = "\t\treturn err\n\t})"
        new = ("\t\treturn err\n\t}, func(ctx context.Context, name appstate.WAPatchName) error {\n"
               "\t\t// CAN primary snapshot recovery v1: never reset collections or devices.\n"
               "\t\t_, err := client.SendPeerMessage(ctx, whatsmeow.BuildAppStateRecoveryRequest(name))\n"
               "\t\tif err == nil {\n"
               '\t\t\ts.gateway.logger.Infof("CAN_READ_STATE stage=peer_recovery_requested collection=%s", name)\n'
               "\t\t}\n\t\treturn err\n\t})")
        method = replace(method, old, new)
        source = source[:start] + method + source[end:]
    return source


def recovery_source(source):
    if "// CAN request primary snapshot v1" in source:
        return source
    source = replace(source,
                     "    fetch func(context.Context, appstate.WAPatchName, bool, bool) error,\n",
                     "    fetch func(context.Context, appstate.WAPatchName, bool, bool) error,\n"
                     "    primary ...func(context.Context, appstate.WAPatchName) error,\n")
    return replace(source,
                   "    if recoveryErr := fetch(ctx, patch.Type, true, false); recoveryErr != nil {\n",
                   "    if recoveryErr := fetch(ctx, patch.Type, true, false); recoveryErr != nil {\n"
                   "        // CAN request primary snapshot v1. A queued request is not an ack.\n"
                   "        if errors.Is(recoveryErr, appstate.ErrMismatchingLTHash) && ctx.Err() == nil && len(primary) == 1 {\n"
                   "            if peerErr := primary[0](ctx, patch.Type); peerErr != nil {\n"
                   "                return fmt.Errorf(\"group read primary recovery failed: %w\", errors.Join(err, recoveryErr, peerErr))\n"
                   "            }\n"
                   "        }\n")


def roster_native_source(source):
    if "// CAN bounded roster snapshot v2" in source:
        return source
    if "// CAN bounded roster snapshot v1" in source:
        source = replace(source,
                         "context.WithTimeout(context.Background(), 30*time.Second)",
                         "context.WithTimeout(context.Background(), 120*time.Second)", count=3)
        return source.replace("// CAN bounded roster snapshot v1", "// CAN bounded roster snapshot v2")
    for name in ("GetContacts", "GetGroups"):
        start = source.index(f"func (s *Session) {name}(")
        # GetContacts is followed by presence helpers, which are not part of it.
        end = source.index("\n}\n", start) + 3
        method = source[start:end]
        method = replace(method, "\tif s.client == nil || s.client.Store.ID == nil {\n",
                         "\t// CAN bounded roster snapshot v2\n"
                         "\tclient := s.currentClient()\n"
                         "\tif client == nil || client.Store == nil || client.Store.ID == nil {\n")
        # A bounded worker call must not read a mutable session client/context.
        offset = method.index("\n\t}\n") + len("\n\t}\n")
        method = method[:offset] + ("\tctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)\n"
                                   "\tdefer cancel()\n") + method[offset:]
        method = method.replace("s.client", "client").replace("s.ctx", "ctx")
        result = "contacts" if name == "GetContacts" else "groups"
        method = replace(method, f"\treturn {result}, nil\n",
                         "\tif s.currentClient() != client { return nil, fmt.Errorf(\"roster session changed\") }\n"
                         f"\treturn {result}, nil\n")
        source = source[:start] + method + source[end:]
    old = "\treturn s.subscribeToPresences(s.ctx, s.currentClient())"
    return replace(source, old,
                   "\tctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)\n"
                   "\tdefer cancel()\n"
                   "\treturn s.subscribeToPresences(ctx, s.currentClient())")


def roster_session_source(source):
    if "# CAN nonblocking roster v1" in source:
        return source
    source = replace(source,
                     "            wa_contacts = list(\n"
                     "                self.whatsapp.GetContacts(refresh=True)  # type:ignore\n"
                     "            )\n",
                     "            # CAN nonblocking roster v1\n"
                     "            wa_contacts = await asyncio.to_thread(\n"
                     "                lambda: list(self.whatsapp.GetContacts(refresh=True))\n"
                     "            )\n")
    return replace(source,
                   "            for wa_contact in self.whatsapp.GetContacts(refresh=True):  # type:ignore[no-untyped-call]\n",
                   "            wa_contacts = await asyncio.to_thread(\n"
                   "                lambda: list(self.whatsapp.GetContacts(refresh=True))\n"
                   "            )\n"
                   "            for wa_contact in wa_contacts:\n")


def roster_contact_source(source):
    if "# CAN nonblocking contact fill v1" in source:
        return source
    source = "import asyncio\n" + source
    source = replace(source,
                     "        wa_contacts = list(\n"
                     "            self.session.whatsapp.GetContacts(  # type:ignore[no-untyped-call]\n"
                     "                refresh=config.ALWAYS_SYNC_ROSTER\n"
                     "            )\n"
                     "        )\n",
                     "        # CAN nonblocking contact fill v1\n"
                     "        wa_contacts = await asyncio.to_thread(\n"
                     "            lambda: list(self.session.whatsapp.GetContacts(refresh=config.ALWAYS_SYNC_ROSTER))\n"
                     "        )\n")
    return replace(source,
                   "        self.session.whatsapp.SubscribeToPresences()  # type:ignore[no-untyped-call]\n",
                   "        await asyncio.to_thread(self.session.whatsapp.SubscribeToPresences)\n")


def roster_group_source(source):
    if "# CAN nonblocking group fill v1" in source:
        return source
    source = "import asyncio\n" + source
    return replace(source,
                   "        groups = self.session.whatsapp.GetGroups()  # type:ignore[no-untyped-call]\n",
                   "        # CAN nonblocking group fill v1\n"
                   "        groups = await asyncio.to_thread(lambda: list(self.session.whatsapp.GetGroups()))\n")


def room_source(source):
    if "# CAN release archived reader v1" in source:
        return source
    start = source.index("    def get_archived_messages(")
    end = source.index("\n\ndef set_origin_id", start)
    method = source[start:end]
    method = replace(method, "            for stored in self.xmpp.store.mam.get_messages(\n",
                     "            # CAN release archived reader v1: one message ID, not all history.\n"
                     "            messages = list(self.xmpp.store.mam.get_messages(\n")
    method = replace(method, "            ):\n                yield HistoryMessage(stored.stanza)\n",
                     "            ))\n"
                     "            messages = [HistoryMessage(stored.stanza) for stored in messages]\n"
                     "        yield from messages\n")
    return source[:start] + method + source[end:]


def attachment_source(source):
    if "# CAN release attachment reader v1" in source:
        return source
    old = ("                if stored is not None:\n"
           "                    if not await self.__valid_url(stored.url):\n"
           "                        stored.url = None  # type:ignore\n"
           "                    return stored\n")
    new = ("            # CAN release attachment reader v1: never hold SQL across HTTP.\n"
           "            if stored is not None:\n"
           "                if not await self.__valid_url(stored.url):\n"
           "                    stored.url = None  # type:ignore\n"
           "                return stored\n")
    return replace(source, old, new)


def muc_error_source(source):
    if "# CAN ignore reflected MUC errors v1" in source:
        return source
    source = replace(source,
                     "from ..util import DispatcherMixin, exceptions_to_xmpp_errors\n",
                     "from ..util import DispatcherMixin, Ignore, exceptions_to_xmpp_errors\n")
    start = source.index("    async def __on_group_chat_error(")
    end = source.index("    @exceptions_to_xmpp_errors", start)
    method = replace(source[start:end], "        except XMPPError as e:\n",
                     "        # CAN ignore reflected MUC errors v1: Ignore is BaseException, not Exception.\n"
                     "        except (XMPPError, Ignore) as e:\n")
    return source[:start] + method + source[end:]


def archive_xml_source(source):
    if "# CAN archive XML 1.0 characters v1" in source:
        return source
    source = replace(source, "import uuid\n", "import re\nimport uuid\n")
    source = replace(source, "class HistoryMessage:\n", r'''# CAN archive XML 1.0 characters v1: keep valid text and preserve stored rows.
_CAN_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class HistoryMessage:
''')
    source = replace(source, "            stanza = Message(xml=ET.fromstring(stanza))\n",
                     '            stanza = Message(xml=ET.fromstring(_CAN_INVALID_XML.sub("\\ufffd", stanza)))\n')
    return replace(source, "            from_db = False\n", '''            from_db = False
            for node in stanza.xml.iter():
                if node.text:
                    node.text = _CAN_INVALID_XML.sub("\\ufffd", node.text)
                if node.tail:
                    node.tail = _CAN_INVALID_XML.sub("\\ufffd", node.tail)
                for key, value in node.attrib.items():
                    node.set(key, _CAN_INVALID_XML.sub("\\ufffd", value))
''')


def patch_package(site_packages):
    transforms = {
        "slidge_whatsapp/session.go": lambda s: roster_native_source(session_source(s)),
        "slidge_whatsapp/can_read_state_recovery.go": recovery_source,
        "slidge_whatsapp/session.py": roster_session_source,
        "slidge_whatsapp/contact.py": roster_contact_source,
        "slidge_whatsapp/group.py": roster_group_source,
        "slidge/group/room.py": room_source,
        "slidge/core/mixins/attachment.py": attachment_source,
        "slidge/core/dispatcher/muc/misc.py": muc_error_source,
        "slidge/util/archive_msg.py": archive_xml_source,
    }
    outputs = {}
    for name, transform in transforms.items():
        path = site_packages / name
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if path.suffix == ".py":
            ast.parse(new)
        if new != old:
            outputs[path] = new
    # All source anchors must validate before any file is changed.
    for path, new in outputs.items():
        path.write_text(new, encoding="utf-8", newline="\n")
    return bool(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Recovery patch applied" if patch_package(args.site_packages) else "Already patched")
