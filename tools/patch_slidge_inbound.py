"""Isolate synchronous native callbacks per session and preserve failed media messages."""
# ruff: noqa: E501 -- exact source guards
from __future__ import annotations

import argparse
import ast
from pathlib import Path

MARKER = "// CAN inbound isolation v1"


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Inbound patch expected one anchor: {old[:90]!r}")
    return source.replace(old, new, 1)


def session_source(source):
    if MARKER in source:
        return source
    source = once(source, "type Session struct {", "type Session struct {\n"
                  "\t// CAN inbound isolation v1\n\tcanInboundMutex sync.Mutex\n\tcanInbound *canInboundDispatcher")
    source = once(source, "\ts.ctx, s.ctxCancel = context.WithCancelCause(context.Background())",
                  "\ts.ctx, s.ctxCancel = context.WithCancelCause(context.Background())\n\ts.canStartInbound()")
    start = source.index("func (s *Session) propagateEvent(")
    end = source.index("\n}\n", start) + 2
    source = source[:start] + """func (s *Session) propagateEvent(kind EventKind, payload *EventPayload) {
    s.canQueuePayload(kind, payload)
}""" + source[end:]
    start = source.index("\tcase *events.HistorySync:", source.index("func (s *Session) handleEvent("))
    end = source.index("\tcase *events.Receipt:", start)
    history_end = source.index("\tcase *events.Message:", start)
    history = source[start + len("\tcase *events.HistorySync:\n"):history_end]
    history = history.replace("s.ctx", "ctx").replace("s.propagateEvent(", "emit(")
    history = once(history, "\t\t\t\tfor _, msg := range c.GetMessages() {",
                   "\t\t\t\tfor _, msg := range c.GetMessages() {\n"
                   "\t\t\t\t\tif ctx.Err()!=nil || s.currentClient()!=client { return }")
    source = source[:start] + """\tcase *events.HistorySync:
        ctx := s.ctx
        d := s.canGetInbound()
        d.enqueue(d.history, func(){
            if ctx.Err()==nil && s.currentClient()==client { s.canHandleHistory(ctx,client,evt) }
        }, false)
    case *events.Message:
        s.canQueueMessage(s.ctx,client,evt)
""" + source[end:]
    source += "\nfunc (s *Session) canHandleHistory(ctx context.Context, client *whatsmeow.Client, evt *events.HistorySync) {\n" + """
    emit:=func(kind EventKind,payload *EventPayload){
        if ctx.Err()==nil && s.currentClient()==client {
            if kind==EventCall && payload!=nil { s.canObserveCall(payload) }
            s.canDeliverInbound(kind,payload)
        }
    }
""" + history + "\n}\n"
    source = once(source, "\tclient.Disconnect()\n\ts.ctxCancel(nil)\n\ts.clearClient(client)\n\treturn nil",
                  "\tclient.Disconnect()\n\ts.ctxCancel(nil)\n\ts.clearClient(client)\n\ts.canStopInbound()\n\treturn nil")
    start = source.index("func (s *Session) Disconnect() error {")
    end = source.index("\n}\n", start) + 2
    part = source[start:end]
    part = once(part, "\tif client == nil {\n\t\treturn nil", "\tif client == nil {\n\t\ts.canStopInbound()\n\t\treturn nil")
    source = source[:start] + part + source[end:]
    # Logout's control payload is queued after the native context was cancelled.
    source = once(source, "\t\ts.propagateEvent(EventLoggedOut, &EventPayload{LoggedOut: LoggedOut{Reason: evt.Reason.String()}})",
                  "\t\ts.propagateEvent(EventLoggedOut, &EventPayload{LoggedOut: LoggedOut{Reason: evt.Reason.String()}})\n\t\ts.canStopInbound()")
    return source


def event_source(source):
    if MARKER in source:
        return source
    source = once(source, "func getMessageAttachments(ctx context.Context, client *whatsmeow.Client, message *waE2E.Message) ([]Attachment, *waE2E.ContextInfo, error) {",
                  "func getMessageAttachments(ctx context.Context, client *whatsmeow.Client, message *waE2E.Message) ([]Attachment, *waE2E.ContextInfo, error) {\n"
                  "    // CAN inbound isolation v1\n    ctx, cancel := context.WithTimeout(ctx, 30*time.Second)\n    defer cancel()")
    source = once(source, "data, err := client.Download(ctx, msg)\n\t\tif err != nil {\n\t\t\treturn nil, nil, err",
                  "data, err := client.Download(ctx, msg)\n\t\tif err != nil {\n\t\t\treturn nil, info, err")
    for raw in ("evt.Message", "info.GetMessage()"):
        source = once(source,
                      f"if attach, context, err := getMessageAttachments(ctx, client, {raw}); err != nil {{\n"
                      '\t\tclient.Log.Errorf("Failed getting message attachments: %s", err)\n\t\treturn EventUnknown, nil',
                      f"if attach, context, err := getMessageAttachments(ctx, client, {raw}); err != nil {{\n"
                      '        client.Log.Warnf("CAN_INBOUND attachment_unavailable class=%s", canAttachmentFailureClass(err))\n'
                      f"        message = canUnavailableAttachment(message, {raw})\n"
                      "        if context != nil { message = getMessageWithContext(ctx,client,message,context) }")
    return source


def python_source(source):
    if "# CAN inbound isolation v1" in source:
        return source
    source = once(source, "import asyncio\n", "import asyncio\nfrom concurrent.futures import CancelledError as CallbackCancelled\n")
    source = once(source, "        return future.result()", "        try:\n            return future.result()\n        except CallbackCancelled:\n            return None")
    source += "\n# CAN inbound isolation v1\nfrom .inbound import install as _install_inbound\n_install_inbound(Session, whatsapp)\n"
    ast.parse(source)
    return source


def room_source(source):
    if "# CAN bounded join history v2" in source:
        return source
    if "# CAN bounded join history v1" in source:
        return release_join_history_page(source)
    source = once(source, "from asyncio import Lock\n",
                  "from asyncio import Lock, sleep as _can_inbound_yield\n")
    start = source.index("    async def __old_school_history(")
    end = source.index("    async def send_mam(", start)
    method = source[start:end]
    method = once(method, "        if since is None:\n", """        # CAN bounded join history v1. This is delivery-on-join, not MAM storage.
        if maxchars == 0 or (maxstanzas is not None and maxstanzas <= 0):
            return
        if maxstanzas is None:
            maxstanzas = 50
        await _can_inbound_yield(0)
        emitted = 0
        if since is None:
""")
    method = once(method, "            self.xmpp.send(msg, False)\n", """            self.xmpp.send(msg, False)
            emitted += 1
            if emitted % 5 == 0:
                await _can_inbound_yield(0)
""")
    source = source[:start] + method + source[end:]
    ast.parse(source)
    return release_join_history_page(source)


def release_join_history_page(source):
    # MessageArchive.get_all holds a database session until its generator ends.
    # Exhaust the requested page before sending or yielding to another join.
    source = once(source, "# CAN bounded join history v1", "# CAN bounded join history v2")
    source = once(source, """        for h_msg in self.archive.get_all(
            start_date=start_date, end_date=None, last_page_n=maxstanzas
        ):
""", """        history = list(self.archive.get_all(
            start_date=start_date, end_date=None, last_page_n=maxstanzas
        ))
        for h_msg in history:
""")
    ast.parse(source)
    return source


def patch(root, helpers):
    package = root / "slidge_whatsapp"
    changes = {}
    for file, transform in (("session.go", session_source), ("event.go", event_source),
                            ("session.py", python_source)):
        path = package / file
        changes[path] = transform(path.read_text(encoding="utf-8"))
    for source, target in (("bridge_inbound.go", "can_inbound.go"), ("bridge_inbound.py", "inbound.py")):
        helper = (helpers / source).read_text(encoding="utf-8")
        destination = package / target
        if destination.exists() and destination.read_text(encoding="utf-8") != helper:
            raise ValueError(f"Refusing to overwrite a different inbound helper: {target}")
        changes[destination] = helper
    room = root / "slidge/group/room.py"
    changes[room] = room_source(room.read_text(encoding="utf-8"))
    for path, value in changes.items():
        if path.suffix == ".py":
            ast.parse(value)
    for path, value in changes.items():
        path.write_text(value, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("helpers", type=Path)
    args = parser.parse_args()
    patch(args.site_packages, args.helpers)
