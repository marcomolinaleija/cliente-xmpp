from __future__ import annotations

import argparse
from pathlib import Path

CHATSTATES_NS = "http://jabber.org/protocol/chatstates"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch a slidge-whatsapp source tree so WhatsApp voice-recording "
            "chatstates are preserved as XEP-0085 composing stanzas with media=audio."
        )
    )
    parser.add_argument(
        "source_tree",
        type=Path,
        help="Path to the root of a slidge-whatsapp checkout.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files before modifying sources.",
    )
    args = parser.parse_args()

    source_tree = args.source_tree.resolve()
    if not source_tree.exists():
        raise SystemExit(f"Source tree does not exist: {source_tree}")

    event_go = source_tree / "slidge_whatsapp" / "event.go"
    session_py = source_tree / "slidge_whatsapp" / "session.py"
    for path in (event_go, session_py):
        if not path.exists():
            raise SystemExit(f"Expected file not found: {path}")

    changed = False
    changed |= patch_event_go(event_go, backup=not args.no_backup)
    changed |= patch_session_py(session_py, backup=not args.no_backup)

    if changed:
        print("Patch applied. Rebuild slidge-whatsapp so gopy regenerates bindings.")
    else:
        print("Patch already present; no files changed.")
    return 0


def patch_event_go(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    if "Media string" not in text:
        text = replace_once(
            text,
            """type ChatState struct {
\tKind  ChatStateKind
\tChat  Chat
\tActor Actor
}""",
            """type ChatState struct {
\tKind  ChatStateKind
\tChat  Chat
\tActor Actor
\tMedia string
}""",
            path,
        )

    if "Media: string(evt.Media)," not in text:
        text = replace_once(
            text,
            """\tvar state = ChatState{
\t\tActor: newActor(ctx, client, evt.Sender, evt.SenderAlt),
\t}""",
            """\tvar state = ChatState{
\t\tActor: newActor(ctx, client, evt.Sender, evt.SenderAlt),
\t\tMedia: string(evt.Media),
\t}""",
            path,
        )

    if text == original:
        return False

    write_text(path, text, backup=backup)
    return True


def patch_session_py(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    old_block = """        if state.Kind == whatsapp.ChatStateComposing:
            contact.composing()
            contact.online(last_seen=datetime.now())
        elif state.Kind == whatsapp.ChatStatePaused:
            contact.paused()
"""
    new_block = """        if state.Kind == whatsapp.ChatStateComposing:
            if getattr(state, "Media", "") == "audio":
                self.__send_audio_chat_state(contact)
            else:
                contact.composing()
            contact.online(last_seen=datetime.now())
        elif state.Kind == whatsapp.ChatStatePaused:
            contact.paused()
"""
    if "__send_audio_chat_state" not in text:
        text = replace_once(text, old_block, new_block, path)

        insert_after = """    async def on_wa_receipt(self, receipt: whatsapp.Receipt) -> None:
"""
        helper = f"""    def __send_audio_chat_state(self, contact: object) -> None:
        message = contact._make_message(state="composing", hints={{"no-store"}})
        for node in message.xml.iter():
            if node.tag == "{{{CHATSTATES_NS}}}composing":
                node.attrib["media"] = "audio"
                break
        contact._send(message)

"""
        text = replace_once(text, insert_after, helper + insert_after, path)

    if text == original:
        return False

    write_text(path, text, backup=backup)
    return True


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not apply patch to {path}: expected one match, found {count}."
        )
    return text.replace(old, new, 1)


def write_text(path: Path, text: str, *, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"patched {path}")


if __name__ == "__main__":
    raise SystemExit(main())
