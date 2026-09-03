from __future__ import annotations

import unittest
from datetime import datetime

from cliente_xmpp.formatting import format_call_body, format_datetime, format_duration


class FormattingTests(unittest.TestCase):
    def test_duration_chooses_readable_units(self) -> None:
        self.assertEqual(format_duration(31), "31 segundos")
        self.assertEqual(format_duration(61), "1 minuto y 1 segundo")
        self.assertEqual(format_duration(2763), "46 minutos y 3 segundos")
        self.assertEqual(format_duration(3661), "1 hora y 1 minuto")

    def test_call_body_replaces_technical_suffix(self) -> None:
        event_at = datetime.fromisoformat("2026-09-02T15:45:01+00:00")
        body = (
            "Outgoing voice call: connected with Contacto de prueba, 2763 seconds "
            "at 2026-09-02 15:45:01+00:00"
        )
        formatted = format_call_body(
            body,
            duration_seconds=2763,
            event_timestamp=event_at,
        )
        self.assertIn("46 minutos y 3 segundos", formatted)
        self.assertNotIn("2763 seconds", formatted)
        self.assertNotIn("2026-09-02 15:45:01+00:00", formatted)

    def test_call_body_translates_legacy_and_modern_prefixes(self) -> None:
        event_at = datetime.fromisoformat("2026-09-02T15:45:01+00:00")
        self.assertTrue(
            format_call_body(
                "Incoming call from Contacto de prueba at 2026-09-02 15:45:01+00:00",
                event_timestamp=event_at,
            ).startswith("Llamada entrante de Contacto de prueba")
        )
        translated = format_call_body(
            "Outgoing video call: connected with Contacto de prueba, 17 seconds "
            "at 2026-09-02 15:45:01+00:00",
            duration_seconds=17,
            event_timestamp=event_at,
        )
        self.assertTrue(
            translated.startswith("Llamada saliente de video: conectada con Contacto de prueba")
        )
        self.assertNotIn("Outgoing", translated)
        self.assertNotIn("connected", translated)

    def test_call_body_translates_all_outcomes(self) -> None:
        expected = {
            "rejected": "rechazada",
            "cancelled": "cancelada",
            "accepted_elsewhere": "contestada en otro dispositivo",
            "missed": "perdida",
            "unavailable": "no disponible",
            "ongoing": "en curso",
        }
        for outcome, label in expected.items():
            with self.subTest(outcome=outcome):
                body = f"Incoming voice call: {outcome} with Contacto de prueba"
                self.assertIn(label, format_call_body(body))

    def test_datetime_uses_relative_day(self) -> None:
        value = datetime.fromisoformat("2026-09-02T15:45:01+00:00")
        now = datetime.fromisoformat("2026-09-02T20:00:00+00:00")
        self.assertIn("hoy a las", format_datetime(value, now=now))


if __name__ == "__main__":
    unittest.main()
