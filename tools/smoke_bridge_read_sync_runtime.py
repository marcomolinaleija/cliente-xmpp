from pathlib import Path


def main() -> None:
    root = Path("/venv/lib/python3.13/site-packages/slidge_whatsapp")
    session_go = (root / "session.go").read_text(encoding="utf-8")
    event_go = (root / "event.go").read_text(encoding="utf-8")
    session_py = (root / "session.py").read_text(encoding="utf-8")

    assert "case *events.MarkChatAsRead:" in session_go
    assert "newMarkChatAsReadEvent" in event_go
    assert "ReceiptRead" in event_go
    assert "message.GetTimestamp() >= latestTimestamp" in event_go
    assert 'getattr(global_config, "NO_UPLOAD_METHOD", None)' in session_py
    print("read-sync runtime smoke: ok")


if __name__ == "__main__":
    main()
