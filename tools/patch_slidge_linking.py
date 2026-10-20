"""Apply the linking contract to the existing bridge, preserving all previous patches."""
# ruff: noqa: E501
from __future__ import annotations

import argparse
import ast
from pathlib import Path

from patch_slidge_failed_relogin_state import patch_login

MARKER = "// CAN linking lifecycle v1"


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Linking patch expected one anchor: {old[:100]!r}")
    return source.replace(old, new, 1)


def native(source):
    if MARKER in source:
        return source
    source = once(source, "\tclient.AddEventHandler(s.handleEvent)", '''\t// CAN linking lifecycle v1
\tclient.AddEventHandler(func(event any) {
\t\tif s.currentClient() == client { s.handleEvent(event) }
\t})''')
    source = once(source, "\tqrChan, _ := client.GetQRChannel(s.ctx)", '''\tctx := s.ctx
\tqrChan, qrErr := client.GetQRChannel(ctx)
\tif qrErr != nil {
\t\ts.stopPresenceRefresh()
\t\ts.clearClient(client)
\t\ts.ctxCancel(qrErr)
\t\treturn fmt.Errorf("cannot initialize QR channel: %w", qrErr)
\t}''')
    start = source.index("\tgo func() {\n\t\tfor e := range qrChan {")
    end = source.index("\n\treturn nil\n}", start)
    source = source[:start] + '''\tgo func() {
\t\tfor e := range qrChan {
\t\t\tif s.currentClient() != client || ctx.Err() != nil { return }
\t\t\tswitch e.Event {
\t\t\tcase whatsmeow.QRChannelEventCode:
\t\t\t\ts.propagateEvent(EventQRCode, &EventPayload{QRCode: canLinkQRPayload(e.Code, e.Timeout)})
\t\t\tcase whatsmeow.QRChannelSuccess.Event:
\t\t\t\treturn
\t\t\tdefault:
\t\t\t\treason := "pair_failed"
\t\t\t\tswitch e.Event {
\t\t\t\tcase whatsmeow.QRChannelTimeout.Event: reason = "expired"
\t\t\t\tcase whatsmeow.QRChannelClientOutdated.Event: reason = "client_outdated"
\t\t\t\tcase "passkey-request", "passkey-confirmation": reason = "unsupported_pairing"
\t\t\t\t}
\t\t\t\t// Unknown/empty events never masquerade as a successful connection.
\t\t\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:"+reason}})
\t\t\t\treturn
\t\t\t}
\t\t}
\t\tif s.currentClient() == client && ctx.Err() == nil && !client.IsLoggedIn() {
\t\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:pair_failed"}})
\t\t}
\t}()
''' + source[end:]
    source = once(source, "\tcase *events.Connected, *events.PushNameSetting:", '''\tcase *events.Disconnected:
\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:disconnected"}})
\tcase *events.ClientOutdated:
\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:client_outdated"}})
\tcase *events.StreamReplaced:
\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:stream_replaced"}})
\tcase *events.PairError:
\t\ts.propagateEvent(EventConnect, &EventPayload{Connect: Connect{Error: "can-link:pair_failed"}})
\tcase *events.Connected, *events.PushNameSetting:''')
    source = once(source, "\t\tif len(client.Store.PushName) == 0 {", "\t\tif !client.IsLoggedIn() || client.Store.ID == nil {")
    source = once(source,
                  "if len(client.Store.PushName) > 0 && evt.Name == appstate.WAPatchCriticalBlock {",
                  "if client.IsLoggedIn() && client.Store.ID != nil && evt.Name == appstate.WAPatchCriticalBlock {")
    source = once(source, "func (s *Session) PairPhone(phone string) (string, error) {\n\tif s.client == nil {",
                  "func (s *Session) PairPhone(phone string) (string, error) {\n\tclient := s.currentClient()\n\tif client == nil || !client.IsConnected() {")
    # Keep the existing exported API and gopy binding intact.
    start = source.index("func (s *Session) PairPhone(")
    end = source.index("\n}", start) + 2
    part = source[start:end].replace("s.client.Store", "client.Store").replace(
        "s.client.PairPhone", "client.PairPhone")
    source = source[:start] + part + source[end:]
    source += '''
// canLinkQRPayload preserves WhatsMeow's per-code expiry across the existing gopy API.
func canLinkQRPayload(code string, ttl time.Duration) string {
    data, _ := json.Marshal(map[string]any{"code": code, "ttl": ttl.Seconds()})
    return string(data)
}
'''
    return source


def python_session(source):
    source = source.replace('hasattr(self, "__connected")', 'hasattr(self, "_Session__connected")')
    # A second Connected/appstate event can queue a completion before the first runs.
    source = source.replace('''            self.xmpp.loop.call_soon_threadsafe(
                self.__connected.set_exception,
                XMPPError("internal-server-error", connect.Error),
            )''', '''            if not self.__connected.done():
                self.__connected.set_exception(XMPPError("internal-server-error", connect.Error))''')
    source = source.replace('''            self.xmpp.loop.call_soon_threadsafe(
                self.__connected.set_result, self.__get_connected_status_message()
            )''', '''            if not self.__connected.done():
                self.__connected.set_result(self.__get_connected_status_message())''')
    ast.parse(source)
    return source


def patch(root: Path, helper: Path):
    package = root / "slidge_whatsapp"
    paths = {package / "session.go": native((package / "session.go").read_text()),
             package / "session.py": python_session((package / "session.py").read_text()),
             root / "slidge/command/user.py": patch_login(
                 (root / "slidge/command/user.py").read_text())}
    path = package / "gateway.py"
    source = path.read_text()
    if "from .linking import install as install_linking" not in source:
        source = once(source, "from .generated import whatsapp",
                      "from .generated import whatsapp\nfrom .linking import install as install_linking")
        source = once(source, "        install(self)", "        install(self)\n        install_linking(self)")
    paths[path] = source
    for path, value in paths.items():
        if path.suffix == ".py":
            ast.parse(value)
    for path, value in paths.items():
        path.write_text(value, encoding="utf-8", newline="\n")
    (package / "linking.py").write_text(helper.read_text(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("helper", type=Path)
    args = parser.parse_args()
    patch(args.site_packages, args.helper)
