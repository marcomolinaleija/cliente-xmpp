from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .patch_slidge_message_info_v33 import replace
else:
    from patch_slidge_message_info_v33 import replace

# ruff: noqa: E501 -- guarded native source anchors

HISTORY_HELPER = '''
// canOwnCallAlias recognizes a stored PN/LID alias without guessing from phone digits.
func canOwnCallAlias(client *whatsmeow.Client, creator types.JID) bool {
    if client == nil || client.Store == nil || client.Store.LIDs == nil { return false }
    var mapped types.JID
    if creator.Server == types.DefaultUserServer {
        mapped, _ = getLIDForPNBounded(context.Background(), client, creator)
    } else if creator.Server == types.HiddenUserServer {
        mapped, _ = getPNForLIDBounded(context.Background(), client, creator)
    }
    return !mapped.IsEmpty() && isOwnCallJID(client, mapped)
}

// canHistoricalCallEvent preserves typed call logs before the group-only message filter.
func canHistoricalCallEvent(ctx context.Context, client *whatsmeow.Client, info *waWeb.WebMessageInfo) (EventKind, *EventPayload, bool) {
    if info == nil || info.GetMessage() == nil {
        return EventUnknown, nil, false
    }
    raw := (&events.Message{RawMessage: info.GetMessage()}).UnwrapRaw()
    if raw.Message.GetCallLogMesssage() == nil {
        return EventUnknown, nil, false
    }
    chat, err := types.ParseJID(info.GetKey().GetRemoteJID())
    if client == nil || chat.User == "" || info.GetMessageTimestamp() == 0 || err != nil ||
        (chat.Server != types.DefaultUserServer && chat.Server != types.HiddenUserServer && chat.Server != types.GroupServer) {
        return EventUnknown, nil, true
    }
    evt, err := client.ParseWebMessage(chat, info)
    if err != nil {
        client.Log.Warnf("Unable to parse historical call log (invalid metadata)")
        return EventUnknown, nil, true
    }
    kind, payload := newCallLogMessageEvent(ctx, client, evt)
    return kind, payload, true
}

// canCallPeerMeta does not route an incoming acceptance from our phone into a self-chat.
func canCallPeerMeta(client *whatsmeow.Client, meta types.BasicCallMeta, direction string) types.BasicCallMeta {
    if direction != callDirectionIncoming || (!meta.From.IsEmpty() && !isOwnCallJID(client, meta.From)) {
        return meta
    }
    for _, creator := range []types.JID{meta.CallCreatorAlt, meta.CallCreator} {
        if creator.User != "" && !isOwnCallJID(client, creator) &&
            (creator.Server == types.DefaultUserServer || creator.Server == types.HiddenUserServer) {
            meta.From = creator.ToNonAD()
            break
        }
    }
    return meta
}
'''


def patch_event(source: str) -> str:
    anchor = "func newEventFromHistory(ctx context.Context, client *whatsmeow.Client, info *waWeb.WebMessageInfo) (EventKind, *EventPayload) {\n"
    source = replace(source, anchor, anchor +
        "\tif info == nil { return EventUnknown, nil }\n"
        "\tif kind, payload, handled := canHistoricalCallEvent(ctx, client, info); handled {\n"
        "\t\treturn kind, payload\n\t}\n")
    old = '''\tif !client.Store.LID.IsEmpty() && creator == client.Store.LID.ToNonAD() {
\t\treturn callDirectionOutgoing
\t}
\treturn callDirectionUnknown
}'''
    new = '''\tif !client.Store.LID.IsEmpty() && creator == client.Store.LID.ToNonAD() {
\t\treturn callDirectionOutgoing
\t}
\tif isOwnCallJID(client, meta.CallCreatorAlt) {
\t\treturn callDirectionOutgoing
\t}
\tif canOwnCallAlias(client, creator) {
\t\treturn callDirectionOutgoing
\t}
\tif client.Store.ID != nil && creator.User != "" &&
\t\t(creator.Server == types.DefaultUserServer || creator.Server == types.HiddenUserServer) &&
\t\t(isOwnCallJID(client, meta.From) || meta.From.ToNonAD() == creator ||
\t\t (!meta.CallCreatorAlt.IsEmpty() && meta.From.ToNonAD() == meta.CallCreatorAlt.ToNonAD())) {
\t\treturn callDirectionIncoming
\t}
\treturn callDirectionUnknown
}'''
    source = replace(source, old, new)
    source = replace(source,
        "\tactor := newActor(ctx, client, meta.From, meta.CallCreator, meta.CallCreatorAlt)\n",
        "\tmeta = canCallPeerMeta(client, meta, direction)\n"
        "\tactor := newActor(ctx, client, meta.From, meta.CallCreator, meta.CallCreatorAlt)\n")
    return source + HISTORY_HELPER


def patch_session(source: str) -> str:
    source = replace(source, "\t\tgo s.syncCallLogAppState(client)\n\t\treturn nil\n",
        "\t\treturn nil\n")
    anchor = "\tcase *events.Connected, *events.PushNameSetting:\n"
    return replace(source, anchor, anchor +
        "\t\t// Backfill only after authentication, also on a real reconnect.\n"
        "\t\tif _, connected := evt.(*events.Connected); connected {\n"
        "\t\t\tgo s.syncCallLogAppState(client)\n\t\t}\n")


def patch_package(site_packages: Path) -> bool:
    package = site_packages / "slidge_whatsapp"
    outputs = {}
    for name, transform, marker in (
        ("event.go", patch_event, "func canHistoricalCallEvent("),
        ("session.go", patch_session, "Backfill only after authentication"),
    ):
        path = package / name
        source = path.read_text(encoding="utf-8")
        if marker not in source:
            outputs[path] = transform(source)
    # Validate both files completely before changing either.
    for path, source in outputs.items():
        path.write_text(source, encoding="utf-8", newline="\n")
    return bool(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preserve incoming call history and caller identity")
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Call-duration patch applied" if patch_package(args.site_packages) else "Call-duration patch already present")
