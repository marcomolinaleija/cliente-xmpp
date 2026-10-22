"""Real installed SQL readers and watchdog, offline with temporary fictional data."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from slidge.core.mixins.attachment import AttachmentMixin
from slidge.db.models import Attachment
from slidge.group.room import LegacyMUC
from slidge_whatsapp import session as session_module
from slidge_whatsapp.contact import Roster
from slidge_whatsapp.group import Bookmarks
from slidge_whatsapp.session import Session
from slixmpp import Message
from slixmpp.plugins.xep_0203.stanza import Delay
from slixmpp.plugins.xep_0359.stanza import StanzaID
from slixmpp.xmlstream import register_stanza_plugin
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

register_stanza_plugin(Message, Delay)
register_stanza_plugin(Message, StanzaID)


async def readers(expect_old):
    with tempfile.TemporaryDirectory() as temp:
        engine = create_engine(f"sqlite:///{temp}/fixture.sqlite", connect_args={"timeout": 0.05})
        session = sessionmaker(engine)
        try:
            Attachment.__table__.create(engine)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE fixture (id INTEGER PRIMARY KEY, stanza TEXT)"))
                conn.execute(text("INSERT INTO fixture VALUES (:id, :stanza)"), [
                    {"id": i, "stanza":
                     '<message xmlns="jabber:client"><body>fixture</body></message>'}
                    for i in range(20)
                ])
            with session() as orm:
                orm.add(Attachment(user_account_id=1, legacy_file_id="fixture", url="https://example.invalid"))
                orm.commit()

            def get_messages(orm, room, ids):
                assert ids == ["fixture"]
                yield from orm.execute(text("SELECT stanza FROM fixture ORDER BY id")).yield_per(1)

            room = SimpleNamespace(
                stored=SimpleNamespace(id=1), xmpp=SimpleNamespace(store=SimpleNamespace(
                    session=session, mam=SimpleNamespace(get_messages=get_messages),
                )),
            )
            iterator = LegacyMUC.get_archived_messages(room, "fixture")
            next(iterator)
            reader_held = engine.pool.checkedout() != 0

            def write():
                with engine.begin() as conn:
                    conn.execute(text("UPDATE fixture SET stanza=stanza WHERE id=19"))

            locked = False
            try:
                await asyncio.to_thread(write)
            except OperationalError as exc:
                if "database is locked" not in str(exc):
                    raise
                locked = True
            finally:
                iterator.close()
            assert reader_held == expect_old and locked == expect_old, (reader_held, locked)

            held_at_http = []

            async def validate(url):
                held_at_http.append(engine.pool.checkedout())
                return True

            recipient = SimpleNamespace(
                _AttachmentMixin__is_component=False, session=SimpleNamespace(user_pk=1),
                xmpp=SimpleNamespace(store=SimpleNamespace(session=session)),
                _AttachmentMixin__valid_url=validate,
            )
            stored = await AttachmentMixin._AttachmentMixin__get_stored(
                recipient, SimpleNamespace(legacy_file_id="fixture"),
            )
            assert stored.url == "https://example.invalid"
            assert held_at_http == ([1] if expect_old else [0]), held_at_http
            assert engine.pool.checkedout() == 0
            return {"archive_reader_held": reader_held, "writer_locked": locked,
                    "attachment_checkouts_at_http": held_at_http[0]}
        finally:
            engine.dispose()


def watchdog(path):
    subprocess.run(["sh", "-n", str(path)], check=True)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        bindir = root / "bin"
        bindir.mkdir()
        scripts = {
            "flock": "exit 0\n",
            "logger": 'printf "%s\\n" "$*" >> "$CALLS"\n',
            "docker": '''printf 'docker %s\n' "$*" >> "$CALLS"
if [ "$1" = inspect ]; then
  [ "${FAKE_INSPECT_FAIL:-0}" = 0 ] || exit 1
  case "$3" in
    *Running*) printf '%s\n' "$FAKE_RUNNING" ;;
    *) printf '%s\n' "$FAKE_HEALTH" ;;
  esac
elif [ "$1" = restart ]; then exit 0
else exit 2
fi
''',
        }
        for name, source in scripts.items():
            script = bindir / name
            script.write_text("#!/bin/sh\n" + source)
            script.chmod(0o700)
        cases = [
            ("running-no-health", "true", "none", False, False),
            ("running-healthy", "true", "healthy", False, False),
            ("running-starting", "true", "starting", False, False),
            ("unhealthy", "true", "unhealthy", True, False),
            ("stopped", "false", "none", True, False),
            ("inspect-failed", "true", "none", False, True),
        ]
        for name, running, health, restart, fail in cases:
            directory = root / name
            directory.mkdir()
            calls = directory / "calls"
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                       STATE_DIR=str(directory), LOCK_FILE=str(directory / "lock"),
                       CALLS=str(calls), FAKE_RUNNING=running, FAKE_HEALTH=health,
                       FAKE_INSPECT_FAIL=str(int(fail)))
            result = subprocess.run(["sh", str(path)], env=env, capture_output=True)
            assert (result.returncode != 0) == fail, name
            log = calls.read_text()
            assert ("docker restart" in log) == restart, name
            assert "docker logs" not in log, "historical warnings must not drive restarts"
            if restart:
                before = log.count("docker restart")
                subprocess.run(["sh", str(path)], env=env, check=True)
                assert calls.read_text().count("docker restart") == before, "cooldown bypassed"
                now = int(time.time())
                (directory / "state").write_text(
                    f"window_start={now}\nrestart_count=2\nlast_restart=0\n"
                )
                subprocess.run(["sh", str(path)], env=env, check=True)
                assert calls.read_text().count("docker restart") == before, "restart limit bypassed"
        return len(cases)


async def roster_roundtrip(expect_old):
    loop = asyncio.get_running_loop()
    owner_thread = threading.get_ident()
    completed, off_loop = [], []

    async def callback():
        return None

    def native(*args, **kwargs):
        off_loop.append(threading.get_ident() != owner_thread)
        # Simulates native contact/app-state work that needs a Python callback.
        # Calling this on the owner loop blocks its own completion.
        future = asyncio.run_coroutine_threadsafe(callback(), loop)
        try:
            future.result(timeout=0.2)
        except TimeoutError:
            future.cancel()
            raise
        completed.append(True)
        return []

    ready = loop.create_future()
    ready.set_result(None)
    wa = SimpleNamespace(GetContacts=native, GetGroups=native, SubscribeToPresences=native)
    session = SimpleNamespace(whatsapp=wa, contacts=SimpleNamespace(ready=ready),
                              log=Mock(), user_jid=SimpleNamespace(bare="fixture@example.invalid"))
    roster = SimpleNamespace(session=session, _refresh_mexico_aliases=Mock())
    bookmarks = SimpleNamespace(session=session)

    async def contacts():
        assert [item async for item in Roster.fill(roster)] == []

    checks = (
        contacts,
        lambda: Bookmarks.fill(bookmarks),
        lambda: Session._Session__sync_roster_after_connect(session),
        lambda: Session.on_preferences(session, {}, {"roster_add_non_friends": True}),
    )
    for check in checks:
        try:
            with patch.object(session_module.SyncContacts, "sync", new_callable=AsyncMock), \
                    patch.object(session_module.asyncio, "sleep", new_callable=AsyncMock):
                await check()
        except TimeoutError:
            assert expect_old, "native callback blocked the event loop"
    assert all(off_loop) == (not expect_old)
    assert len(completed) == (0 if expect_old else 5), (off_loop, completed)
    return {"native_roster_roundtrips": len(completed), "native_calls_off_loop": all(off_loop)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-old", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(readers(args.expect_old))
    result.update(asyncio.run(roster_roundtrip(args.expect_old)))
    if not args.expect_old:
        result["watchdog_scenarios"] = watchdog(Path(__file__).with_name("slidge-watchdog"))
    print(json.dumps({"recovery_runtime": "baseline_reproduced" if args.expect_old else "pass",
                      **result}))
