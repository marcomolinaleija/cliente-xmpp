from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from slidge_whatsapp.contact import Contact
from slidge_whatsapp.generated import whatsapp


async def check_missing_timestamp_reuses_cached_last_seen() -> None:
    cached_last_seen = datetime(2026, 7, 19, 2, 21, 10, tzinfo=UTC)
    contact = SimpleNamespace(
        _get_last_presence=Mock(
            return_value=SimpleNamespace(last_seen=cached_last_seen)
        ),
        away=Mock(),
        online=Mock(),
    )

    await Contact.update_presence(contact, whatsapp.PresenceUnavailable, 0)

    contact.away.assert_called_once_with(last_seen=cached_last_seen)
    contact.online.assert_not_called()


async def check_new_timestamp_replaces_cached_last_seen() -> None:
    cached_last_seen = datetime(2026, 7, 19, 1, 0, tzinfo=UTC)
    new_timestamp = 1_784_435_200
    contact = SimpleNamespace(
        _get_last_presence=Mock(
            return_value=SimpleNamespace(last_seen=cached_last_seen)
        ),
        away=Mock(),
        online=Mock(),
    )

    await Contact.update_presence(
        contact,
        whatsapp.PresenceUnavailable,
        new_timestamp,
    )

    contact.away.assert_called_once_with(
        last_seen=datetime.fromtimestamp(new_timestamp, tz=UTC)
    )


def main() -> int:
    source = inspect.getsource(Contact.update_presence)
    assert "cached_presence = self._get_last_presence()" in source
    asyncio.run(check_missing_timestamp_reuses_cached_last_seen())
    asyncio.run(check_new_timestamp_replaces_cached_last_seen())
    print("presence last-seen runtime smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
