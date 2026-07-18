from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import phonenumbers
from phonenumbers import geocoder

DEFAULT_PHONE_REGION = "MX"
_ALLOWED_PHONE_INPUT = re.compile(r"^[+0-9\s().-]+$")
_MISSING_SPANISH_REGION_NAMES = {
    "AC": "Isla de la Ascension",
    "BL": "San Bartolome",
    "EH": "Sahara Occidental",
    "MF": "San Martin",
    "TA": "Tristan de Acuna",
    "XK": "Kosovo",
}


class PhoneNumberError(ValueError):
    """Raised when a phone number cannot be used as an international recipient."""


@dataclass(frozen=True, slots=True)
class CountryDialingOption:
    region_code: str
    country_name: str
    calling_code: int

    @property
    def label(self) -> str:
        return f"{self.country_name} (+{self.calling_code})"


@dataclass(frozen=True, slots=True)
class NormalizedPhoneNumber:
    e164: str
    international: str


@lru_cache(maxsize=1)
def country_dialing_options() -> tuple[CountryDialingOption, ...]:
    options = [
        CountryDialingOption(
            region_code=region_code,
            country_name=_country_name_es(region_code),
            calling_code=phonenumbers.country_code_for_region(region_code),
        )
        for region_code in phonenumbers.SUPPORTED_REGIONS
    ]
    return tuple(
        sorted(
            options,
            key=lambda option: (_search_key(option.country_name), option.region_code),
        )
    )


def normalize_phone_number(
    value: str,
    region_code: str = DEFAULT_PHONE_REGION,
) -> NormalizedPhoneNumber:
    raw_value = value.strip()
    if not raw_value:
        raise PhoneNumberError("Escribe un numero de telefono.")
    if not _ALLOWED_PHONE_INPUT.fullmatch(raw_value):
        raise PhoneNumberError(
            "El numero solo puede contener digitos, espacios, parentesis, puntos o guiones."
        )
    if "+" in raw_value and not raw_value.startswith("+"):
        raise PhoneNumberError("El signo + solo puede aparecer al inicio del numero.")
    if raw_value.count("+") > 1:
        raise PhoneNumberError("El numero contiene mas de un signo +.")

    region_code = region_code.strip().upper()
    if region_code not in phonenumbers.SUPPORTED_REGIONS:
        raise PhoneNumberError("Selecciona un pais o region valido.")

    parse_value = raw_value
    parse_region: str | None = region_code
    compact_value = _compact_phone_input(raw_value)
    if compact_value.startswith("00"):
        parse_value = f"+{compact_value[2:]}"
        parse_region = None
    elif compact_value.startswith("+"):
        parse_value = compact_value
        parse_region = None

    try:
        parsed = phonenumbers.parse(parse_value, parse_region)
    except phonenumbers.NumberParseException as exc:
        raise PhoneNumberError("No se pudo interpretar el numero de telefono.") from exc

    if not phonenumbers.is_possible_number(parsed):
        raise PhoneNumberError(
            "El numero no tiene una longitud posible para el pais seleccionado."
        )

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    if not re.fullmatch(r"\+[1-9]\d{3,14}", e164):
        raise PhoneNumberError("El numero internacional no es valido.")

    return NormalizedPhoneNumber(
        e164=e164,
        international=phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.INTERNATIONAL,
        ),
    )


def whatsapp_contact_jid(phone_number: str, component_jid: str) -> str:
    component_jid = component_jid.strip().split("/", 1)[0]
    if not component_jid or "@" in component_jid:
        raise ValueError("El componente de WhatsApp no tiene un JID valido.")
    if not re.fullmatch(r"\+[1-9]\d{3,14}", phone_number):
        raise ValueError("El numero debe estar normalizado en formato internacional.")
    return f"{phone_number}@{component_jid}"


def _country_name_es(region_code: str) -> str:
    example = phonenumbers.example_number(region_code)
    name = geocoder.country_name_for_number(example, "es") if example is not None else ""
    return name or _MISSING_SPANISH_REGION_NAMES.get(region_code, region_code)


def _compact_phone_input(value: str) -> str:
    return "".join(character for character in value if character.isdigit() or character == "+")


def _search_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
