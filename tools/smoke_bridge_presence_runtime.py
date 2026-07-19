from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

from slidge_whatsapp.contact import Contact
from slidge_whatsapp.generated import whatsapp


async def check_metadata_refresh_reuses_cached_presence() -> None:
    contact = SimpleNamespace(
        legacy_id="521111111111@s.whatsapp.net",
        send_last_presence=Mock(),
    )
    with patch.object(whatsapp, "IsAnonymousJID", return_value=False):
        await Contact.update_info(contact)
    contact.send_last_presence.assert_called_once_with(
        force=True,
        no_cache_online=True,
    )


def main() -> int:
    source = inspect.getsource(Contact.update_info)
    marker = "self.send_last_presence(force=True, no_cache_online=True)"
    assert marker in source, "presence-cache patch is not installed"
    assert "self.online()" not in source, "metadata refresh still clears last_seen"
    asyncio.run(check_metadata_refresh_reuses_cached_presence())
    print("presence cache runtime smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
