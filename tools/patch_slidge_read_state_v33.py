from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

# ruff: noqa: E501 -- guarded upstream anchors and native source templates

if __package__:
    from .patch_slidge_message_info_v33 import replace
else:
    from patch_slidge_message_info_v33 import replace


NATIVE_METHOD = '''
// canGroupReadPatch binds the app-state horizon to the original message, never wall-clock time.
func canGroupReadPatch(receipt Receipt) (appstate.PatchInfo, error) {
    if !receipt.Chat.IsGroup || len(receipt.MessageIDs) != 1 || receipt.MessageIDs[0] == "" || receipt.Timestamp <= 0 {
        return appstate.PatchInfo{}, fmt.Errorf("invalid group read horizon")
    }
    chat, err := types.ParseJID(receipt.Chat.JID)
    if err != nil || chat.Server != types.GroupServer {
        return appstate.PatchInfo{}, fmt.Errorf("invalid group read chat")
    }
    sender, err := types.ParseJID(receipt.OriginActor.LID)
    if err != nil || sender.IsEmpty() || receipt.OriginActor.IsMe || sender.Server != types.HiddenUserServer {
        return appstate.PatchInfo{}, fmt.Errorf("invalid group read sender")
    }
    key := &waCommon.MessageKey{
        RemoteJID: ptrTo(chat.String()), FromMe: ptrTo(false),
        ID: ptrTo(receipt.MessageIDs[0]), Participant: ptrTo(sender.ToNonAD().String()),
    }
    return appstate.BuildMarkChatAsRead(chat, true, time.Unix(receipt.Timestamp, 0), key), nil
}

// MarkChatRead synchronizes the chat state separately from per-message read receipts.
func (s *Session) MarkChatRead(receipt Receipt) error {
    client := s.currentClient()
    if client == nil || client.Store == nil || client.Store.ID == nil {
        return fmt.Errorf("cannot synchronize read state for unauthenticated session")
    }
    patch, err := canGroupReadPatch(receipt)
    if err != nil { return err }
    ctx, cancel := context.WithTimeout(s.ctx, 15*time.Second)
    defer cancel()
    return client.SendAppState(ctx, patch)
}
'''


def patch_mixins(source):
    source = replace(source, "from .generated import go, whatsapp\n", "from .generated import go, whatsapp\n"
        "from .read_state import acknowledge, unread_up_to, send_receipt, sync_group_read\n")
    source = replace(source, "        self.wa.SendReceipt(receipt)  # type:ignore[no-untyped-call]\n",
        "        await send_receipt(self, receipt)\n")
    return replace(source, "    async def on_displayed(self, legacy_msg_id: str, thread: str | None) -> None:\n",
        "    async def can_unread_up_to(self, stanza_id):\n"
        "        return await unread_up_to(self, stanza_id)\n\n"
        "    async def can_ack_read(self, stanza_id):\n"
        "        await acknowledge(self, stanza_id)\n\n"
        "    async def on_chat_read(self, native_id):\n"
        "        await sync_group_read(self, native_id)\n\n"
        "    async def on_displayed(self, legacy_msg_id: str, thread: str | None) -> None:\n")


def patch_marker(source):
    source = replace(source, "    def __to_mark(self, recipient: AnyRecipient, msg_id: str) -> list[str]:\n",
        "    async def __to_mark(self, recipient: AnyRecipient, msg_id: str) -> list[str]:\n"
        "        hook = getattr(recipient, 'can_unread_up_to', None)\n"
        "        if isinstance(recipient, LegacyMUC) and hook is not None:\n"
        "            return await hook(msg_id)\n")
    source = replace(source, "to_mark = self.__to_mark(", "to_mark = await self.__to_mark(", count=2)
    for thread in ("legacy_thread", "None"):
        anchor = ("            await recipient.on_displayed(\n"
            f"                self._xmpp_msg_id_to_legacy(xmpp_id, recipient), {thread}\n"
            "            )\n")
        source = replace(source, anchor, anchor +
            "            ack = getattr(recipient, 'can_ack_read', None)\n"
            "            if isinstance(recipient, LegacyMUC) and ack is not None:\n"
            "                await ack(xmpp_id)\n")
    source = replace(source, "        if isinstance(recipient, LegacyMUC):\n            await recipient.echo(msg, None)\n",
        "        if isinstance(recipient, LegacyMUC):\n"
        "            hook = getattr(recipient, 'on_chat_read', None)\n"
        "            if hook is not None:\n"
        "                await hook(self._xmpp_msg_id_to_legacy(msg['displayed']['id'], recipient))\n"
        "            await recipient.echo(msg, None)\n")
    return source + ("\n        # Do not feed reflected app-state publications back into WhatsApp.\n"
        "        hook = getattr(recipient, 'on_chat_read', None)\n"
        "        if to_mark and hook is not None:\n"
        "            await hook(self._xmpp_msg_id_to_legacy(stanza_id, recipient))\n")


def clone_binding(source, kind):
    if kind == "go":
        pattern = r"//export whatsapp_Session_SendReceipt\nfunc whatsapp_Session_SendReceipt\([^\n]+\n.*?^}\n"
    elif kind == "py":
        pattern = r"^\tdef SendReceipt\(self, receipt\):\n.*?(?=^\tdef )"
    elif kind == "c":
        pattern = r"^PyObject \*\n_wrap__whatsapp_whatsapp_Session_SendReceipt\(.*?^PyObject \* _wrap__whatsapp_whatsapp_Session_SendReceipt[^\n]+\n"
    else:
        pattern = r"^extern char\* whatsapp_Session_SendReceipt[^\n]+\n"
    matches = list(re.finditer(pattern, source, re.M | re.S))
    if len(matches) != 1:
        raise ValueError(f"Expected one {kind} native binding")
    match = matches[0]
    clone = match.group().replace("SendReceipt", "MarkChatRead")
    if kind == "go":
        clone = replace(clone, "\tif __err != nil {\n\t\treturn errorGoToPy(nil)\n\t}\n",
            "\tif __err != nil {\n\t\tC.PyEval_RestoreThread(_saved_thread)\n"
            "\t\treturn errorGoToPy(nil)\n\t}\n")
    source = source[:match.end()] + clone + source[match.end():]
    if kind == "c":
        line = next(line for line in source.splitlines(True) if '{(char *) "whatsapp_Session_SendReceipt"' in line)
        source = replace(source, line, line + line.replace("SendReceipt", "MarkChatRead"))
    return source


def patch_package(site_packages: Path):
    package = site_packages / "slidge_whatsapp"
    marker = site_packages / "slidge/core/dispatcher/message/marker.py"
    transforms = {
        package / "session.go": lambda source: source + NATIVE_METHOD,
        package / "mixins.py": patch_mixins,
        marker: patch_marker,
    }
    for name, kind in (("whatsapp.go", "go"), ("whatsapp.py", "py"),
                       ("whatsapp.c", "c"), ("whatsapp_go.h", "h")):
        transforms[package / "generated" / name] = lambda source, kind=kind: clone_binding(source, kind)
    outputs = {}
    for path, transform in transforms.items():
        source = path.read_text(encoding="utf-8")
        marker_text = "canGroupReadPatch" if path.name == "session.go" else (
            "can_ack_read" if path.name in ("mixins.py", "marker.py") else "MarkChatRead"
        )
        if marker_text not in source:
            outputs[path] = transform(source)
    # Validate all anchors before writing any files; generated bindings stay ABI-compatible.
    for path, source in outputs.items():
        path.write_text(source, encoding="utf-8", newline="\n")
    target = package / "read_state.py"
    module = Path(__file__).with_name("bridge_read_state.py")
    changed = not target.exists() or target.read_bytes() != module.read_bytes()
    if changed:
        shutil.copy2(module, target)
    return bool(outputs) or changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synchronize bounded group read state on v33")
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Read-state patch applied" if patch_package(args.site_packages) else "Read-state patch already present")
