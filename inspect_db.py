import sqlite3
from pathlib import Path
from datetime import datetime
import json

db_path = Path.home() / ".cliente-xmpp" / "messages.sqlite3"
print(f"Opening DB: {db_path}")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Get chats
print("--- CHATS ---")
chats = conn.execute("SELECT jid, name, last_message_preview, last_message_at FROM chats").fetchall()
for c in chats:
    if "Ari" in c["name"] or "Jessi" in c["name"]:
        print(dict(c))

print("\n--- MESSAGES FOR ARI ---")
# Get Ari's JID
ari_jid = None
for c in chats:
    if "Ari" in c["name"]:
        ari_jid = c["jid"]
        break

if ari_jid:
    messages = conn.execute("SELECT body, sent_at, outgoing FROM messages WHERE chat_jid = ? ORDER BY sent_at DESC LIMIT 10", (ari_jid,)).fetchall()
    for m in messages:
        print(dict(m))

print("\n--- MESSAGES FOR JESSI ---")
jessi_jid = None
for c in chats:
    if "Jessi" in c["name"]:
        jessi_jid = c["jid"]
        break

if jessi_jid:
    messages = conn.execute("SELECT body, sent_at, outgoing, reply_quote FROM messages WHERE chat_jid = ? ORDER BY sent_at DESC LIMIT 10", (jessi_jid,)).fetchall()
    for m in messages:
        print(dict(m))

conn.close()
