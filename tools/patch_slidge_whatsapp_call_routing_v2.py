from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not patch {description}: expected one match, found {count}."
        )
    return text.replace(old, new, 1)


def patch_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if '"chat_jid", "chat-jid"' in source:
        return False
    source = replace_once(
        source,
        '''        ("peer_jid", "peer-jid"),
        ("group_jid", "group-jid"),
        ("event_timestamp", "event-timestamp"),
''',
        '''        ("peer_jid", "peer-jid"),
        ("chat_jid", "chat-jid"),
        ("group_jid", "group-jid"),
        ("event_timestamp", "event-timestamp"),
''',
        "call XML routing attribute",
    )
    source = replace_once(
        source,
        '''        if metadata:
            message_kwargs["extra_xml"] = make_call_extension(metadata)
''',
        '''        if metadata:
            # Actor.JID is resolved through the bridge contact map. The resulting
            # bare JID is the canonical XMPP conversation target, unlike peer_jid
            # which remains the WhatsApp/LID-side identifier from the event.
            canonical_chat_jid = str(contact.jid.bare).strip()
            if canonical_chat_jid:
                metadata["chat_jid"] = canonical_chat_jid
            message_kwargs["extra_xml"] = make_call_extension(metadata)
''',
        "canonical XMPP routing derivation",
    )
    if backup:
        shutil.copy2(path, path.with_suffix(path.suffix + ".before-call-routing-v2"))
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add the v2 canonical XMPP call-routing attribute to the v1 bridge."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    path = args.site_packages.resolve() / "slidge_whatsapp/session.py"
    if not path.is_file():
        raise SystemExit(f"Missing bridge session: {path}")
    changed = patch_session(path, backup=not args.no_backup)
    print("Call routing v2 patch applied." if changed else "Call routing v2 patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())