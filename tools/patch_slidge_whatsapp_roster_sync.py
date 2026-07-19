from __future__ import annotations

import argparse
from pathlib import Path

CONTACT_HELPERS = '''\n\ndef _modern_mexico_legacy_id(legacy_id: str) -> str | None:
    local, separator, server = legacy_id.partition("@")
    if (
        separator
        and server == whatsapp.DefaultUserServer
        and local.startswith("521")
        and len(local) == 13
        and local.isdigit()
    ):
        return f"52{local[3:]}@{server}"
    return None
'''


OLD_ROSTER = '''class Roster(LegacyRoster[Contact]):
    session: "Session"

    async def fill(self) -> AsyncIterator[Contact]:
        """
        Retrieve contacts from remote WhatsApp service, subscribing to their presence and adding to
        local roster.
        """
        wa_contacts = self.session.whatsapp.GetContacts(  # type:ignore[no-untyped-call]
            refresh=config.ALWAYS_SYNC_ROSTER
        )
        for wa_contact in wa_contacts:
            contact = await self.add_whatsapp_contact(wa_contact)
            if contact is not None:
                yield contact
        self.session.whatsapp.SubscribeToPresences()  # type:ignore[no-untyped-call]

    async def add_whatsapp_contact(self, data: whatsapp.Contact) -> Contact | None:
        """
        Adds a WhatsApp contact to local roster, filling all required and optional information.
        """
        # Don't attempt to add ourselves to the roster.
        if self.user_legacy_id == data.Actor.JID:
            return None
        if not data.Actor.JID:
            return None
        contact = await self.by_legacy_id(data.Actor.JID)
        await contact.update_whatsapp_info(data)
        return contact

    async def legacy_id_to_jid_username(self, legacy_id: str) -> str:
        if "@" not in legacy_id:
            raise XMPPError("item-not-found", "Invalid contact ID, not a JID")
        return "+" + legacy_id[: legacy_id.find("@")]

    async def jid_username_to_legacy_id(self, jid_username: str) -> str:
        if jid_username.startswith("#"):
            raise XMPPError("item-not-found", "Invalid contact ID: group ID given")
        if not jid_username.startswith("+"):
            raise XMPPError("item-not-found", "Invalid contact ID, expected '+' prefix")
        return jid_username.removeprefix("+") + "@" + whatsapp.DefaultUserServer
'''


NEW_ROSTER = '''class Roster(LegacyRoster[Contact]):
    session: "Session"

    def _refresh_mexico_aliases(self, wa_contacts: list[whatsapp.Contact]) -> None:
        legacy_ids = {data.Actor.JID for data in wa_contacts if data.Actor.JID}
        self._mexico_aliases = {
            legacy_id: modern
            for legacy_id in legacy_ids
            if (modern := _modern_mexico_legacy_id(legacy_id)) is not None
            and modern in legacy_ids
        }

    def _canonical_legacy_id(self, legacy_id: str) -> str:
        return getattr(self, "_mexico_aliases", {}).get(legacy_id, legacy_id)

    async def fill(self) -> AsyncIterator[Contact]:
        """
        Retrieve contacts from remote WhatsApp service, subscribing to their presence and adding to
        local roster.
        """
        wa_contacts = list(
            self.session.whatsapp.GetContacts(  # type:ignore[no-untyped-call]
                refresh=config.ALWAYS_SYNC_ROSTER
            )
        )
        self._refresh_mexico_aliases(wa_contacts)

        contacts_by_legacy_id: dict[str, whatsapp.Contact] = {}
        for wa_contact in wa_contacts:
            original_id = wa_contact.Actor.JID
            canonical_id = self._canonical_legacy_id(original_id)
            current = contacts_by_legacy_id.get(canonical_id)
            if current is None or original_id == canonical_id:
                contacts_by_legacy_id[canonical_id] = wa_contact

        for legacy_id, wa_contact in contacts_by_legacy_id.items():
            wa_contact.Actor.JID = legacy_id
            contact = await self.add_whatsapp_contact(wa_contact)
            if contact is not None:
                yield contact
        self.session.whatsapp.SubscribeToPresences()  # type:ignore[no-untyped-call]

    async def add_whatsapp_contact(self, data: whatsapp.Contact) -> Contact | None:
        """
        Adds a WhatsApp contact to local roster, filling all required and optional information.
        """
        # Don't attempt to add ourselves to the roster.
        if self.user_legacy_id == data.Actor.JID:
            return None
        if not data.Actor.JID:
            return None
        data.Actor.JID = self._canonical_legacy_id(data.Actor.JID)
        contact = await self.by_legacy_id(data.Actor.JID)
        await contact.update_whatsapp_info(data)
        return contact

    async def by_legacy_id(self, legacy_id: str) -> Contact:
        return await super().by_legacy_id(self._canonical_legacy_id(legacy_id))

    async def legacy_id_to_jid_username(self, legacy_id: str) -> str:
        if "@" not in legacy_id:
            raise XMPPError("item-not-found", "Invalid contact ID, not a JID")
        return "+" + legacy_id[: legacy_id.find("@")]

    async def jid_username_to_legacy_id(self, jid_username: str) -> str:
        if jid_username.startswith("#"):
            raise XMPPError("item-not-found", "Invalid contact ID: group ID given")
        if not jid_username.startswith("+"):
            raise XMPPError("item-not-found", "Invalid contact ID, expected '+' prefix")
        legacy_id = jid_username.removeprefix("+") + "@" + whatsapp.DefaultUserServer
        return self._canonical_legacy_id(legacy_id)
'''


SESSION_INIT_OLD = '''        self.__handle_event = make_sync(self.handle_event, self.xmpp.loop)
        self.whatsapp.SetEventHandler(self.__handle_event)
        self.__reset_connected()
'''


SESSION_INIT_NEW = '''        self.__handle_event = make_sync(self.handle_event, self.xmpp.loop)
        self.whatsapp.SetEventHandler(self.__handle_event)
        self.__reset_connected()
        self.__roster_sync_task: asyncio.Task[None] | None = None
'''


SESSION_CONNECT_OLD = '''            self.xmpp.loop.call_soon_threadsafe(
                self.__connected.set_result, self.__get_connected_status_message()
            )

    async def on_wa_logged_out(self, logged_out: whatsapp.LoggedOut) -> None:
'''


SESSION_CONNECT_NEW = '''            self.xmpp.loop.call_soon_threadsafe(
                self.__connected.set_result, self.__get_connected_status_message()
            )

        if connect.Error == "":
            self.__schedule_roster_sync()

    def __schedule_roster_sync(self) -> None:
        if self.__roster_sync_task is not None and not self.__roster_sync_task.done():
            return
        self.__roster_sync_task = self.create_task(
            self.__sync_roster_after_connect(),
            name=f"whatsapp-roster-sync-{self.user_jid.bare}",
        )

    async def __sync_roster_after_connect(self) -> None:
        try:
            await self.contacts.ready
            result = await SyncContacts.sync(self, self, self.user_jid)  # type:ignore
            self.log.info("Automatic XMPP roster sync completed: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log.exception("Automatic XMPP roster sync failed")

    async def on_wa_logged_out(self, logged_out: whatsapp.LoggedOut) -> None:
'''


def replace_once(text: str, old: str, new: str, description: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise SystemExit(f"Could not find a unique {description} block.")
    return text.replace(old, new, 1)


def patch_contact(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = text
    if "def _modern_mexico_legacy_id(" not in updated:
        marker = 'if TYPE_CHECKING:\n    from .session import Session\n'
        if updated.count(marker) != 1:
            raise SystemExit("Could not find contact helper insertion point.")
        updated = updated.replace(marker, marker + CONTACT_HELPERS, 1)
    updated = replace_once(updated, OLD_ROSTER, NEW_ROSTER, "Roster")
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def patch_session(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = replace_once(text, SESSION_INIT_OLD, SESSION_INIT_NEW, "Session.__init__")
    updated = replace_once(
        updated,
        SESSION_CONNECT_OLD,
        SESSION_CONNECT_NEW,
        "Session.on_wa_connect",
    )
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch slidge-whatsapp roster sync and Mexican contact aliases."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    package = args.package_root.resolve() / "slidge_whatsapp"
    targets = (package / "contact.py", package / "session.py")
    for target in targets:
        if not target.is_file():
            raise SystemExit(f"File not found: {target}")
        if not args.no_backup:
            backup = target.with_suffix(target.suffix + ".before-roster-sync")
            if not backup.exists():
                backup.write_bytes(target.read_bytes())

    changed = [patch_contact(targets[0]), patch_session(targets[1])]
    print("Roster sync patch applied." if any(changed) else "Roster sync patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
