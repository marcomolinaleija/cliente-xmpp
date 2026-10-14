"""Send release workflow results to private chats using GitHub Actions secrets."""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramDeliveryError(RuntimeError):
    """A fixed diagnostic that contains no recipient data or request URL."""


def delivery_error(error: HTTPError) -> TelegramDeliveryError:
    description = ""
    try:
        payload = json.loads(error.read(65536))
        if isinstance(payload, dict):
            description = str(payload.get("description", "")).casefold()
    except (OSError, ValueError):
        pass
    reason = "Telegram rechazó la petición"
    if "chat not found" in description:
        reason = "chat no encontrado; revisa el ID y que la persona haya iniciado el bot"
    elif "blocked by the user" in description:
        reason = "la persona bloqueó el bot"
    elif "can't initiate conversation" in description:
        reason = "la persona debe iniciar una conversación con el bot"
    elif "bot is not a member" in description or "not enough rights" in description:
        reason = "el bot no tiene acceso o permisos en ese chat"
    elif error.code == 401:
        reason = "token rechazado"
    elif error.code == 429:
        reason = "límite temporal de Telegram"
    return TelegramDeliveryError(f"HTTP {error.code}: {reason}")


def chat_ids(value: str) -> list[str]:
    items = list(dict.fromkeys(item for item in re.split(r"[,\s]+", value.strip()) if item))
    if not items or any(re.fullmatch(r"-?[1-9][0-9]*", item) is None for item in items):
        raise ValueError("TELEGRAM_CHAT_IDS debe contener IDs numéricos separados por coma o línea")
    return items


def notification_text(environment: dict[str, str]) -> str:
    if environment.get("TELEGRAM_NOTIFICATION_TEST") == "1":
        return (
            "🧪 Prueba de notificaciones de WhatsApp CAN.\n"
            "Este chat recibe correctamente los avisos del workflow.\n"
            f"Ejecución: {environment.get('RUN_URL', '')}"
        )
    build = environment.get("BUILD_RESULT", "unknown")
    publish = environment.get("PUBLISH_RESULT", "unknown")
    tag = environment.get("RELEASE_TAG", "")
    if build == "success" and publish == "success":
        title = f"✅ WhatsApp CAN {tag}: actualización publicada"
    elif "cancelled" in (build, publish):
        title = f"⚠️ WhatsApp CAN {tag}: ejecución cancelada"
    elif build != "success":
        title = f"❌ WhatsApp CAN {tag}: falló la compilación o sus comprobaciones"
    else:
        title = f"❌ WhatsApp CAN {tag}: falló el job de publicación"
    lines = [title]
    if publish == "success" and environment.get("RELEASE_URL"):
        lines.append(f"Descargas: {environment['RELEASE_URL']}")
    lines.append(f"Ejecución y logs: {environment.get('RUN_URL', '')}")
    return "\n".join(lines)


def send_message(token: str, destination: str, text: str) -> None:
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": destination, "text": text,
                         "link_preview_options": {"is_disabled": True}}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(65536))
    except HTTPError as exc:
        raise delivery_error(exc) from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram no confirmó el envío")


def notify(environment: dict[str, str]) -> int:
    token = environment.get("TELEGRAM_BOT_TOKEN", "").strip()
    destinations = environment.get("TELEGRAM_CHAT_IDS", "").strip()
    if not token and not destinations:
        if environment.get("TELEGRAM_NOTIFICATION_TEST") == "1":
            raise ValueError("La prueba requiere los dos secrets de Telegram")
        print("Telegram sin configurar: se omite el aviso")
        return 0
    if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) is None or not destinations:
        raise ValueError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_IDS en los secrets")
    recipients = chat_ids(destinations)
    only = environment.get("TELEGRAM_TEST_RECIPIENT", "all")
    if environment.get("TELEGRAM_NOTIFICATION_TEST") == "1" and only != "all":
        if only not in ("1", "2") or int(only) > len(recipients):
            raise ValueError("El destinatario de prueba debe ser all, 1 o 2 y estar configurado")
    else:
        only = "all"
    text = notification_text(environment)
    failures = []
    attempted = 0
    for position, destination in enumerate(recipients, start=1):
        if only != "all" and position != int(only):
            continue
        attempted += 1
        try:
            send_message(token, destination, text)
        except TelegramDeliveryError as exc:
            print(f"::warning::Destino en posición {position}: {exc}")
            failures.append(position)
        except (HTTPError, URLError, OSError, ValueError, RuntimeError):
            # Never print a request URL, token, chat ID, response body or recipient name.
            # An unreachable chat must not prevent delivery to the remaining recipients.
            failures.append(position)
    print(f"Telegram: {attempted - len(failures)} de {attempted} destinos avisados")
    if failures:
        positions = ", ".join(str(position) for position in failures)
        raise RuntimeError(f"No se pudo avisar a los destinos en posición: {positions}")
    return 0


def main() -> int:
    try:
        return notify(dict(os.environ))
    except (ValueError, RuntimeError) as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
