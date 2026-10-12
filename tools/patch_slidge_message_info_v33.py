from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# ruff: noqa: E501 -- exact upstream source anchors


def replace(source: str, old: str, new: str, count: int = 1) -> str:
    if source.count(old) != count:
        raise ValueError(f"Message-info patch expected {count} anchors, found {source.count(old)}")
    return source.replace(old, new)


def patched_session(source: str) -> str:
    source = "from .message_info import ingest_message, record_receipt\n" + source
    # The future import must remain first.
    source = source.replace(
        "from .message_info import ingest_message, record_receipt\nfrom __future__ import annotations\n",
        "from __future__ import annotations\nfrom .message_info import ingest_message, record_receipt\n",
    )
    source = replace(
        source,
        "        for message_id in receipt.MessageIDs:\n",
        "        await record_receipt(self, receipt, contact)\n"
        "        for message_id in receipt.MessageIDs:\n",
    )
    source = replace(
        source,
        "        # Skip handing message that's already in our message archive.\n",
        "        await ingest_message(self, message)\n"
        "        # Skip handing message that's already in our message archive.\n",
    )
    return source


def patched_mixins(source: str) -> str:
    source = replace(
        source,
        "from .generated import go, whatsapp\n",
        "from .generated import go, whatsapp\n"
        "from .message_info import prepare_outgoing, outgoing_sent\n",
    )
    return replace(
        source,
        "        self.wa.SendMessage(message)  # type:ignore[no-untyped-call]\n        return message_id\n",
        "        info_snapshot = await prepare_outgoing(self, message)\n"
        "        self.wa.SendMessage(message)  # type:ignore[no-untyped-call]\n"
        "        await outgoing_sent(self, message, info_snapshot)\n        return message_id\n",
        count=3,
    )


def patched_gateway(source: str) -> str:
    source = replace(
        source,
        "from .generated import whatsapp\n",
        "from .generated import whatsapp\nfrom .message_info import install\n",
    )
    return replace(
        source,
        "        self.whatsapp.Init()\n",
        "        self.whatsapp.Init()\n        install(self)\n",
    )


def patched_event(source: str) -> str:
    source = replace(
        source,
        "func newReceiptEvent(ctx context.Context, client *whatsmeow.Client, evt *events.Receipt) (EventKind, *EventPayload) {\n",
        "func newReceiptEvent(ctx context.Context, client *whatsmeow.Client, evt *events.Receipt) (EventKind, *EventPayload) {\n"
        "\t// CAN: unknown/retry/self-sync receipts are not delivery confirmations.\n"
        "\tif evt.Type != types.ReceiptTypeDelivered && evt.Type != types.ReceiptTypeRead {\n"
        "\t\treturn EventUnknown, nil\n\t}\n",
    )
    old = """\t\tswitch info.GetStatus() {
\t\tcase waWeb.WebMessageInfo_DELIVERY_ACK:
\t\t\treceipt.Kind = ReceiptDelivered
\t\t\treceipt.Timestamp = r.GetReceiptTimestamp()
\t\tcase waWeb.WebMessageInfo_READ:
\t\t\treceipt.Kind = ReceiptRead
\t\t\treceipt.Timestamp = r.GetReadTimestamp()
\t\t}
\t\tmessage.Receipts = append(message.Receipts, receipt)
"""
    new = """\t\t// CAN: history status is per participant, not the whole message.
\t\tkind, timestamp, known := canHistoryReceipt(r.GetReceiptTimestamp(), r.GetReadTimestamp())
\t\tif !known {
\t\t\tcontinue
\t\t}
\t\treceipt.Kind = kind
\t\treceipt.Timestamp = timestamp
\t\tif kind == ReceiptRead && r.GetReceiptTimestamp() > 0 {
\t\t\tdelivered := receipt
\t\t\tdelivered.Kind = ReceiptDelivered
\t\t\tdelivered.Timestamp = r.GetReceiptTimestamp()
\t\t\tmessage.Receipts = append(message.Receipts, delivered)
\t\t}
\t\tmessage.Receipts = append(message.Receipts, receipt)
"""
    source = replace(source, old, new)
    return (
        source
        + """
// canHistoryReceipt extracts only explicit per-person receipt evidence.
func canHistoryReceipt(delivered, read int64) (ReceiptKind, int64, bool) {
    if read > 0 {
        return ReceiptRead, read, true
    }
    if delivered > 0 {
        return ReceiptDelivered, delivered, true
    }
    return ReceiptDelivered, 0, false
}
"""
    )


def patch_package(site_packages: Path, *, backup: bool = True) -> bool:
    package = site_packages.resolve() / "slidge_whatsapp"
    transforms = {
        "session.py": patched_session,
        "mixins.py": patched_mixins,
        "gateway.py": patched_gateway,
        "event.go": patched_event,
    }
    outputs = {}
    for name, transform in transforms.items():
        source = (package / name).read_text(encoding="utf-8")
        markers = {
            "session.py": "await ingest_message(self, message)",
            "mixins.py": "info_snapshot = await prepare_outgoing",
            "gateway.py": "        install(self)",
            "event.go": "func canHistoryReceipt(",
        }
        if markers[name] not in source:
            outputs[name] = transform(source)
    # Validate every anchor before writing any source.
    for name, result in outputs.items():
        path = package / name
        if backup:
            copy = path.with_suffix(path.suffix + ".before-message-info-v33")
            if not copy.exists():
                shutil.copy2(path, copy)
        path.write_text(result, encoding="utf-8", newline="\n")
    module = Path(__file__).with_name("bridge_message_info.py")
    target = package / "message_info.py"
    changed = not target.exists() or target.read_bytes() != module.read_bytes()
    if changed:
        shutil.copy2(module, target)
    return bool(outputs) or changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Add persistent private group message info to v32")
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print("Message-info patch applied" if changed else "Message-info patch already present")


if __name__ == "__main__":
    main()
