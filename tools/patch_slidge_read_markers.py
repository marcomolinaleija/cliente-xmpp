"""Keep contact read-marker commits off the gateway loop, preserving its locks."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path


def marker_source(source: str) -> str:
    if "# CAN nonblocking contact read markers v1" in source:
        return source
    old = "            return recipient.pop_unread_xmpp_ids_up_to(msg_id)\n"
    new = '''            # CAN nonblocking contact read markers v1
            # Snapshot the scalar on the owner loop; no ORM instance crosses threads.
            contact_pk = recipient.stored.id
            assert contact_pk is not None
            store = self.xmpp.store

            def pop():
                # Each worker owns and closes its SQLAlchemy session, even on failure.
                with store.session.begin() as orm:
                    # A duplicate/unknown horizon must not consume newer messages.
                    if orm.scalar(select(ContactSent.id).where(
                        ContactSent.contact_id == contact_pk, ContactSent.msg_id == msg_id,
                    )) is None:
                        return []
                    ids = store.contacts.pop_sent_up_to(orm, contact_pk, msg_id)
                    return ids

            # Reuse the session's named locks: overlapping markers cannot pop twice.
            async with recipient.session.lock(("can-read-markers", contact_pk)):
                task = asyncio.create_task(asyncio.to_thread(pop))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    # A cancelled coroutine must not unlock while its SQL worker runs.
                    await asyncio.gather(task, return_exceptions=True)
                    raise
'''
    if source.count(old) != 1:
        raise ValueError("Read-marker patch expected exactly one contact-pop anchor")
    result = ("import asyncio\nfrom sqlalchemy import select\n"
              "from ....db.models import ContactSent\n\n" + source.replace(old, new, 1))
    ast.parse(result)
    return result


def sqlite_source(source: str) -> str:
    if "# CAN rollback unfinished SQLite commits v1" in source:
        return source
    old = "    engine = sa.create_engine(path, pool_size=pool_size)\n"
    new = old + '''    # CAN rollback unfinished SQLite commits v1
    # A failed COMMIT can leave DBAPI active even after SQLAlchemy closes it.
    if engine.dialect.name == "sqlite":
        @sa.event.listens_for(engine, "reset")
        def rollback_unfinished(dbapi_connection, _record, reset_state):
            if not reset_state.terminate_only and dbapi_connection.in_transaction:
                dbapi_connection.rollback()
'''
    if source.count(old) != 1:
        raise ValueError("Read-marker patch expected exactly one engine anchor")
    result = source.replace(old, new, 1)
    ast.parse(result)
    return result


def patch_package(site_packages: Path) -> bool:
    outputs = {}
    for name, transform in (
        ("slidge/core/dispatcher/message/marker.py", marker_source),
        ("slidge/db/meta.py", sqlite_source),
    ):
        path = site_packages / name
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if new != old:
            outputs[path] = new
    # Validate both exact runtime anchors before writing either file.
    for path, source in outputs.items():
        path.write_text(source, encoding="utf-8", newline="\n")
    return bool(outputs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Read-marker patch applied" if patch_package(args.site_packages) else "Already patched")
