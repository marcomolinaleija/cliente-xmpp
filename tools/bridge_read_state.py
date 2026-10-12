"""Bounded group read horizons, acknowledged only after successful native sends."""
from __future__ import annotations

import asyncio
from datetime import UTC


def _horizon_query(orm, room_id, stanza_id):
    from slidge.db.models import ArchivedMessage
    from sqlalchemy import select

    return orm.scalar(select(ArchivedMessage).where(
        ArchivedMessage.room_id == room_id, ArchivedMessage.stanza_id == stanza_id,
    ))


def pending_ids(recipient, stanza_id):
    from slidge.db.models import ArchivedMessage
    from sqlalchemy import and_, or_, select

    with recipient.xmpp.store.session() as orm:
        ref = _horizon_query(orm, recipient.stored.id, stanza_id)
        if ref is None:
            return []  # Never interpret an unknown horizon as "read everything".
        return list(orm.scalars(select(ArchivedMessage.stanza_id).where(
            ArchivedMessage.room_id == recipient.stored.id,
            ~ArchivedMessage.displayed_by_user,
            ArchivedMessage.legacy_id.is_not(None),
            or_(ArchivedMessage.timestamp < ref.timestamp, and_(
                ArchivedMessage.timestamp == ref.timestamp, ArchivedMessage.id <= ref.id,
            )),
        ).order_by(ArchivedMessage.timestamp, ArchivedMessage.id)))


def acknowledge_id(recipient, stanza_id):
    from slidge.db.models import ArchivedMessage
    from sqlalchemy import update

    with recipient.xmpp.store.session() as orm:
        orm.execute(update(ArchivedMessage).where(
            ArchivedMessage.room_id == recipient.stored.id,
            ArchivedMessage.stanza_id == stanza_id,
        ).values(displayed_by_user=True))
        orm.commit()


def original_timestamp(recipient, native_id):
    from slidge.db.models import ArchivedMessage
    from sqlalchemy import select

    with recipient.xmpp.store.session() as orm:
        stamp = orm.scalar(select(ArchivedMessage.timestamp).where(
            ArchivedMessage.room_id == recipient.stored.id,
            ArchivedMessage.legacy_id == native_id,
        ).order_by(ArchivedMessage.timestamp.desc()).limit(1))
        if stamp is None:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return int(stamp.timestamp())


async def _db(recipient, method, *args):
    executor = recipient.xmpp._can_message_info_store
    return await executor.run(method, recipient, *args)


async def unread_up_to(recipient, stanza_id):
    return await _db(recipient, pending_ids, stanza_id)


async def acknowledge(recipient, stanza_id):
    await _db(recipient, acknowledge_id, stanza_id)


async def native_with_retry(method, payload):
    # Both operations are idempotent. Keep retries bounded and surface final failure.
    for attempt in range(3):
        try:
            return await asyncio.to_thread(method, payload)
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep((0.2, 0.5)[attempt])


async def send_receipt(recipient, receipt):
    await native_with_retry(recipient.wa.SendReceipt, receipt)


async def sync_group_read(recipient, native_id):
    if not recipient.get_wa_chat().IsGroup:
        return
    # App-state versions are shared across chats of one WhatsApp session.
    # Serialize only these mutations, never the network loop or other accounts.
    lock = getattr(recipient.session, "_can_read_state_lock", None)
    if lock is None:
        lock = recipient.session._can_read_state_lock = asyncio.Lock()
    async with lock:
        await _sync_group_read(recipient, native_id)


async def _sync_group_read(recipient, native_id):
    from slidge_whatsapp.generated import go, whatsapp
    from slixmpp.exceptions import XMPPError

    stamp = await _db(recipient, original_timestamp, native_id)
    if not stamp:
        raise XMPPError("item-not-found", "Cannot resolve the group read horizon.")
    previous = getattr(recipient, "_can_read_state_horizon", None)
    if previous and (stamp < previous[0] or (stamp, native_id) == previous):
        return
    actor = await recipient.get_wa_actor(native_id)
    if actor.IsMe or not actor.LID:
        raise XMPPError("bad-request", "Group read horizon must identify an incoming sender.")
    receipt = whatsapp.Receipt(
        Chat=recipient.get_wa_chat(), OriginActor=actor,
        MessageIDs=go.Slice_string([native_id]), Timestamp=stamp,
    )
    # Timestamp is the original send time, not the read time: do not clear later messages.
    await native_with_retry(recipient.wa.MarkChatRead, receipt)
    recipient._can_read_state_horizon = (stamp, native_id)
