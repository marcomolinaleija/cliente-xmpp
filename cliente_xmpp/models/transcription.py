"""Deepgram transcription preferences kept by the bridge.

The bridge owns this state (the ``/transcribe`` chat commands change the same
values), so the preferences dialog always reloads it instead of caching a copy.
The API key is write-only: the bridge only reports its last four characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

TRANSCRIPTION_NS = "urn:can:whatsapp:transcription:0"
DELIVERY_PRIVATE = "private"
DELIVERY_REPLY = "reply"
AUTO_OFF = "off"
AUTO_ALL = "all"
AUTO_SELECTED = "selected"
AUTO_ALL_EXCEPT = "all_except"
MAX_API_KEY_LENGTH = 256


@dataclass(frozen=True, slots=True)
class TranscriptionSettings:
    available: bool = False
    service_enabled: bool = True
    auto_enabled: bool = True
    delivery: str = DELIVERY_PRIVATE
    key_source: str = "none"  # account | server | none
    key_hint: str = ""
    chat_overrides: dict[str, bool] = field(default_factory=dict)

    @property
    def auto_mode(self) -> str:
        """Project the bridge's global switch plus exceptions onto one choice."""
        if self.auto_enabled:
            return AUTO_ALL_EXCEPT if any(not v for v in self.chat_overrides.values()) else AUTO_ALL
        return AUTO_SELECTED if any(self.chat_overrides.values()) else AUTO_OFF

    @property
    def selected_chats(self) -> frozenset[str]:
        """Chats listed for the current mode (exceptions that differ from the global)."""
        wanted = not self.auto_enabled
        return frozenset(jid for jid, enabled in self.chat_overrides.items() if enabled is wanted)


@dataclass(frozen=True, slots=True)
class TranscriptionSettingsUpdate:
    service_enabled: bool
    auto_mode: str
    delivery: str
    chats: frozenset[str] = frozenset()
    api_key: str | None = None  # None keeps the stored key
    clear_api_key: bool = False

    @property
    def auto_enabled(self) -> bool:
        return self.auto_mode in {AUTO_ALL, AUTO_ALL_EXCEPT}

    @property
    def chat_overrides(self) -> dict[str, bool]:
        if self.auto_mode == AUTO_SELECTED:
            return {jid: True for jid in sorted(self.chats)}
        if self.auto_mode == AUTO_ALL_EXCEPT:
            return {jid: False for jid in sorted(self.chats)}
        return {}


def validate_api_key(value: str) -> str:
    key = value.strip()
    if not key:
        raise ValueError("Escribe la clave API de Deepgram.")
    if len(key) > MAX_API_KEY_LENGTH or any(c.isspace() or not c.isprintable() for c in key):
        raise ValueError("La clave API no puede contener espacios ni caracteres especiales.")
    return key


def _flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "on"}


def parse_transcription_settings(xml: ET.Element) -> TranscriptionSettings:
    node = xml if xml.tag == f"{{{TRANSCRIPTION_NS}}}settings" else xml.find(
        f".//{{{TRANSCRIPTION_NS}}}settings"
    )
    if node is None:
        raise ValueError("La respuesta del puente no incluye la configuración.")
    delivery = node.get("delivery", DELIVERY_PRIVATE)
    source = node.get("key-source", "none")
    overrides: dict[str, bool] = {}
    for chat in node.findall(f"{{{TRANSCRIPTION_NS}}}chat"):
        jid = (chat.get("jid") or "").strip().casefold()
        if jid:
            overrides[jid] = _flag(chat.get("enabled"))
    return TranscriptionSettings(
        available=_flag(node.get("available")),
        service_enabled=_flag(node.get("service"), True),
        auto_enabled=_flag(node.get("auto"), True),
        delivery=delivery if delivery in {DELIVERY_PRIVATE, DELIVERY_REPLY} else DELIVERY_PRIVATE,
        key_source=source if source in {"account", "server", "none"} else "none",
        key_hint=(node.get("key-hint") or "")[:8],
        chat_overrides=overrides,
    )


def build_transcription_settings_update(update: TranscriptionSettingsUpdate) -> ET.Element:
    node = ET.Element(
        f"{{{TRANSCRIPTION_NS}}}settings",
        {
            "service": "1" if update.service_enabled else "0",
            "auto": "1" if update.auto_enabled else "0",
            "delivery": update.delivery,
        },
    )
    if update.clear_api_key:
        ET.SubElement(node, f"{{{TRANSCRIPTION_NS}}}api-key", {"clear": "1"})
    elif update.api_key:
        ET.SubElement(node, f"{{{TRANSCRIPTION_NS}}}api-key").text = update.api_key
    chats = ET.SubElement(node, f"{{{TRANSCRIPTION_NS}}}chats")
    for jid, enabled in update.chat_overrides.items():
        ET.SubElement(
            chats, f"{{{TRANSCRIPTION_NS}}}chat", {"jid": jid, "enabled": "1" if enabled else "0"}
        )
    return node


TRANSCRIPTION_ERRORS = {
    "not-acceptable": "Deepgram rechazó la clave o el audio.",
    "not-authorized": "Deepgram rechazó la clave API. Revísala en Preferencias.",
    "payment-required": "La cuenta de Deepgram no tiene crédito disponible.",
    "conflict": "Falta una clave API para activar el servicio.",
    "service-unavailable": "La transcripción no está configurada o está desactivada.",
    "feature-not-implemented": "Este puente no permite configurar la transcripción.",
    "item-not-found": "El puente ya no conserva este audio.",
    "remote-server-timeout": "Deepgram no respondió a tiempo. Inténtalo más tarde.",
    "remote-server-not-found": "Deepgram no está disponible en este momento.",
    "bad-request": "El puente rechazó la solicitud de transcripción.",
}


def transcription_error_text(condition: str, text: str = "") -> str:
    if condition == "not-acceptable" and text:
        return f"No se pudo transcribir: {text}."
    return TRANSCRIPTION_ERRORS.get(condition, "No se pudo completar la operación con el puente.")
