# ruff: noqa: E501

from __future__ import annotations

import argparse
from pathlib import Path

OLD_ALIAS_ROUTING = '''    def _refresh_mexico_aliases(self, wa_contacts: list[whatsapp.Contact]) -> None:
        legacy_ids = {data.Actor.JID for data in wa_contacts if data.Actor.JID}
        self._mexico_aliases = {
            legacy_id: modern
            for legacy_id in legacy_ids
            if (modern := _modern_mexico_legacy_id(legacy_id)) is not None
            and modern in legacy_ids
        }

    def _canonical_legacy_id(self, legacy_id: str) -> str:
        return getattr(self, "_mexico_aliases", {}).get(legacy_id, legacy_id)
'''


NEW_ALIAS_ROUTING = '''    def _refresh_mexico_aliases(self, wa_contacts: list[whatsapp.Contact]) -> None:
        legacy_ids = {data.Actor.JID for data in wa_contacts if data.Actor.JID}
        aliases = {
            legacy_id: modern
            for legacy_id in legacy_ids
            if (modern := _modern_mexico_legacy_id(legacy_id)) is not None
            and modern in legacy_ids
        }
        self._mexico_aliases = aliases
        self._mexico_outbound_aliases = {
            modern: legacy_id for legacy_id, modern in aliases.items()
        }

    def _canonical_legacy_id(self, legacy_id: str) -> str:
        return getattr(self, "_mexico_aliases", {}).get(legacy_id, legacy_id)

    def _outbound_legacy_id(self, legacy_id: str) -> str:
        return getattr(self, "_mexico_outbound_aliases", {}).get(legacy_id, legacy_id)
'''


OLD_CONTACT_ROUTING = '''    def get_wa_chat(self) -> whatsapp.Chat:
        return whatsapp.Chat(JID=self.legacy_id, IsGroup=False)  # type:ignore[no-untyped-call]

    async def get_wa_actor(self, legacy_msg_id: str) -> whatsapp.Actor:
        carbon = self.session.message_is_carbon(self, legacy_msg_id)
        return whatsapp.Actor(  # type:ignore[no-untyped-call]
            JID=self.session.contacts.user_legacy_id if carbon else self.legacy_id,
            IsMe=carbon,
        )
'''


NEW_CONTACT_ROUTING = '''    def _wa_legacy_id(self) -> str:
        return self.session.contacts._outbound_legacy_id(self.legacy_id)

    def get_wa_chat(self) -> whatsapp.Chat:
        return whatsapp.Chat(  # type:ignore[no-untyped-call]
            JID=self._wa_legacy_id(),
            IsGroup=False,
        )

    async def get_wa_actor(self, legacy_msg_id: str) -> whatsapp.Actor:
        carbon = self.session.message_is_carbon(self, legacy_msg_id)
        return whatsapp.Actor(  # type:ignore[no-untyped-call]
            JID=(
                self.session.contacts.user_legacy_id
                if carbon
                else self._wa_legacy_id()
            ),
            IsMe=carbon,
        )
'''


OLD_REPLY_ACTOR = '''        wa_msg.OriginActor.JID = self.legacy_id
'''


NEW_REPLY_ACTOR = '''        wa_msg.OriginActor.JID = self._wa_legacy_id()
'''


def replace_once(text: str, old: str, new: str, description: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise SystemExit(f"Could not find a unique {description} block.")
    return text.replace(old, new, 1)


def patch_contact(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    updated = replace_once(
        text,
        OLD_ALIAS_ROUTING,
        NEW_ALIAS_ROUTING,
        "Mexican alias routing",
    )
    updated = replace_once(
        updated,
        OLD_CONTACT_ROUTING,
        NEW_CONTACT_ROUTING,
        "contact WhatsApp routing",
    )
    updated = replace_once(
        updated,
        OLD_REPLY_ACTOR,
        NEW_REPLY_ACTOR,
        "reply actor routing",
    )
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve legacy Mexican WhatsApp IDs for outbound routing."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    target = (
        args.package_root.resolve()
        / "slidge_whatsapp"
        / "contact.py"
    )
    if not target.is_file():
        raise SystemExit(f"File not found: {target}")
    if not args.no_backup:
        backup = target.with_suffix(target.suffix + ".before-mexico-outbound")
        if not backup.exists():
            backup.write_bytes(target.read_bytes())

    changed = patch_contact(target)
    print(
        "Mexican outbound alias patch applied."
        if changed
        else "Mexican outbound alias patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
