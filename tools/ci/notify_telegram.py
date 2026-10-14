"""Send release workflow results to private chats using GitHub Actions secrets."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROGRESS_PHASES = {
    "validation": "Validando etiqueta, versiones y release nueva",
    "environment": "Preparando Python y dependencias",
    "runtime": "Verificando las DLL de NVDA y libmpv",
    "checks": "Ejecutando pruebas completas y Ruff",
    "updater": "Compilando el actualizador",
    "client": "Compilando el cliente Windows",
    "zip": "Empaquetando y verificando el ZIP de actualización",
    "installer": "Generando el instalador con Inno Setup",
    "smoke": "Ejecutando el cliente desde el ZIP final",
    "artifacts": "Guardando ZIP, instalador y checksums",
    "publication_validation": "Validando los artefactos antes de publicar",
    "draft": "Subiendo los archivos a una release en borrador",
    "asset_verification": "Verificando los archivos descargados desde GitHub",
    "publishing": "Publicando la actualización estable",
}


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
    phase = environment.get("TELEGRAM_PROGRESS_PHASE", "")
    if phase:
        label = PROGRESS_PHASES[phase]
        return (
            f"⏳ WhatsApp CAN {environment.get('RELEASE_TAG', '')}\n"
            f"{label}\n"
            f"Ejecución y logs: {environment.get('RUN_URL', '')}"
        )
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
    last_phase = environment.get("LAST_PHASE", "")
    if (build != "success" or publish != "success") and last_phase in PROGRESS_PHASES:
        lines.append(f"Última fase: {PROGRESS_PHASES[last_phase]}")
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


def encrypted_chat_identity(token: str, destination: str, position: int, public_key: str) -> str:
    """Return only encrypted names; no chat IDs, tokens or plaintext reach public logs."""
    if not public_key.startswith("-----BEGIN PUBLIC KEY-----"):
        raise ValueError("La consulta de identidad requiere una clave pública PEM")
    request = Request(
        f"https://api.telegram.org/bot{token}/getChat",
        data=json.dumps({"chat_id": destination}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read(65536))
    except HTTPError as exc:
        raise delivery_error(exc) from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("Telegram no confirmó la consulta del chat")
    chat = result.get("result", {})
    name = " ".join(str(chat.get(key) or "") for key in ("first_name", "last_name")).strip()
    name = name or str(chat.get("title") or "Sin nombre disponible")
    data = json.dumps({"position": position, "name": name}, ensure_ascii=False).encode("utf-8")
    blocks = []
    with tempfile.TemporaryDirectory(prefix="telegram-public-key-") as folder:
        key_path = Path(folder) / "public.pem"
        key_path.write_text(public_key, encoding="ascii")
        for offset in range(0, len(data), 128):
            process = subprocess.run([
                "openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(key_path),
                "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
            ], input=data[offset:offset + 128], capture_output=True, timeout=15)
            if process.returncode != 0:
                raise RuntimeError("No se pudo cifrar el nombre del chat")
            blocks.append(base64.b64encode(process.stdout).decode("ascii"))
    return json.dumps(blocks)


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
    public_key = environment.get("TELEGRAM_IDENTITY_PUBLIC_KEY", "").strip()
    if public_key:
        if environment.get("TELEGRAM_NOTIFICATION_TEST") != "1" or only == "all":
            raise ValueError("Selecciona un único destinatario para consultar su identidad")
        position = int(only)
        result = encrypted_chat_identity(token, recipients[position - 1], position, public_key)
        print(f"TELEGRAM_CHAT_IDENTITY_ENCRYPTED={result}")
        return 0
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


def report_progress(phase: str) -> None:
    if os.environ.get("TELEGRAM_PROGRESS_ENABLED") != "1":
        return
    if phase not in PROGRESS_PHASES:
        raise ValueError("Fase de progreso desconocida")
    github_env = os.environ.get("GITHUB_ENV", "")
    try:
        if github_env:
            with Path(github_env).open("a", encoding="utf-8") as output:
                output.write(f"TELEGRAM_CURRENT_PHASE={phase}\n")
        notify({**os.environ, "TELEGRAM_PROGRESS_PHASE": phase})
    except (OSError, ValueError, RuntimeError):
        print("::warning::No se pudo enviar el aviso de progreso de Telegram; el trabajo continúa")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PROGRESS_PHASES)
    arguments = parser.parse_args(argv)
    if arguments.phase:
        report_progress(arguments.phase)
        return 0
    try:
        return notify(dict(os.environ))
    except (ValueError, RuntimeError) as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
