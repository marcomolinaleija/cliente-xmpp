from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from slixmpp import ClientXMPP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cliente_xmpp.config.credentials import CredentialStore  # noqa: E402
from cliente_xmpp.config.settings import SettingsStore  # noqa: E402
from cliente_xmpp.xmpp.client import (  # noqa: E402
    DATA_FORMS_NS,
    SLIDGE_PAIR_PHONE_COMMAND,
    SLIDGE_RELOGIN_COMMAND,
    _format_xmpp_error,
)


def _is_whatsapp_component_jid(jid: str) -> bool:
    bare = jid.split("/", 1)[0].casefold()
    return bool(bare and "@" not in bare and "whatsapp" in bare)


def _safe(value: object) -> str:
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


class WhatsAppLinkProbe(ClientXMPP):
    def __init__(self, action: str, phone: str) -> None:
        settings = SettingsStore().load_connection()
        password = CredentialStore().get_password(settings.jid)
        if not settings.jid or not password:
            raise RuntimeError("No hay JID o password guardado en keyring.")

        super().__init__(settings.jid, password)
        self.settings = settings
        self.action = action
        self.phone = phone
        self.component_jid = ""
        self.done = asyncio.Event()

        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("message", self._on_message)
        self.add_event_handler("presence_available", self._on_presence)
        self.add_event_handler("presence_unavailable", self._on_presence)
        self.add_event_handler("presence_error", self._on_presence)
        self.add_event_handler("changed_status", self._on_presence)
        self.add_event_handler("failed_auth", self._on_failed_auth)

    async def _on_session_start(self, _event: object) -> None:
        self.send_presence()
        await self.get_roster()
        print("[probe] connected", flush=True)
        self.component_jid = await self._find_whatsapp_component()
        print(f"[probe] component={self.component_jid or '-'}", flush=True)
        if not self.component_jid:
            self.done.set()
            return

        await self._print_commands()
        match self.action:
            case "logout":
                await self._request_logout()
            case "logout-relogin":
                await self._request_logout()
                await asyncio.sleep(2)
                await self._request_relogin()
            case "relogin":
                await self._request_relogin()
            case "pair-code":
                await self._request_pair_code()
            case "chat-relogin":
                self.send_message(
                    mto=self.component_jid,
                    mbody="re-login",
                    mtype="chat",
                )
                print("[probe] sent chat command re-login", flush=True)
            case "chat-qr":
                self.send_message(mto=self.component_jid, mbody="qr", mtype="chat")
                print("[probe] sent chat command qr", flush=True)
            case "none":
                pass

        await asyncio.sleep(90)
        self.done.set()

    def _on_message(self, msg: object) -> None:
        try:
            from_jid = str(msg["from"])
            message_type = str(msg["type"])
            body = str(msg["body"] or "")
            xml = ET.tostring(msg.xml, encoding="unicode")
        except Exception as exc:
            print(f"[probe] message parse error={exc}", flush=True)
            return

        if not _is_whatsapp_component_jid(from_jid) and "slidge" not in xml.casefold():
            return

        print(
            _safe(f"[probe] message from={from_jid} type={message_type} body={body!r}"),
            flush=True,
        )
        print(_safe(f"[probe] xml={xml}"), flush=True)

    def _on_presence(self, pres: object) -> None:
        try:
            from_jid = str(pres["from"])
            presence_type = str(pres["type"] or "")
            show = str(pres["show"] or "")
            status = str(pres["status"] or "")
            xml = ET.tostring(pres.xml, encoding="unicode")
        except Exception as exc:
            print(f"[probe] presence parse error={exc}", flush=True)
            return

        if not _is_whatsapp_component_jid(from_jid):
            return

        print(
            _safe(
                "[probe] presence "
                f"from={from_jid} type={presence_type or '-'} show={show or '-'} "
                f"status={status or '-'}"
            ),
            flush=True,
        )
        print(_safe(f"[probe] presence_xml={xml}"), flush=True)

    def _on_failed_auth(self, _event: object) -> None:
        print("[probe] auth failed", flush=True)
        self.done.set()

    async def _find_whatsapp_component(self) -> str:
        domains = {self.boundjid.domain}
        for jid in self.client_roster.keys():
            bare = str(jid).split("/", 1)[0]
            if "@" in bare:
                domains.add(bare.split("@", 1)[1])
            elif bare:
                domains.add(bare)

        candidates: set[str] = set()
        for domain in domains:
            try:
                items = await self["xep_0030"].get_items(jid=domain, timeout=10)
            except Exception:
                continue
            for item in items.xml.findall(".//{http://jabber.org/protocol/disco#items}item"):
                jid = item.attrib.get("jid", "")
                if _is_whatsapp_component_jid(jid):
                    candidates.add(jid)

        for candidate in sorted(candidates):
            return candidate
        return ""

    async def _print_commands(self) -> None:
        try:
            items = await self["xep_0030"].get_items(
                jid=self.component_jid,
                node="http://jabber.org/protocol/commands",
                timeout=10,
            )
        except Exception as exc:
            print(f"[probe] commands error={_format_xmpp_error(exc)}", flush=True)
            return

        print("[probe] commands:", flush=True)
        for item in items.xml.findall(".//{http://jabber.org/protocol/disco#items}item"):
            print(
                _safe(
                    f"  node={item.attrib.get('node', '')} "
                    f"name={item.attrib.get('name', '')}"
                ),
                flush=True,
            )

    async def _request_relogin(self) -> None:
        try:
            result = await self["xep_0050"].send_command(
                self.component_jid,
                SLIDGE_RELOGIN_COMMAND,
                timeout=20,
            )
        except Exception as exc:
            print(f"[probe] relogin error={_format_xmpp_error(exc)}", flush=True)
            return

        print("[probe] relogin result:", flush=True)
        print(ET.tostring(result.xml, encoding="unicode"), flush=True)

    async def _request_logout(self) -> None:
        try:
            result = await self["xep_0050"].send_command(
                self.component_jid,
                "wa_logout",
                timeout=20,
            )
        except Exception as exc:
            print(f"[probe] logout error={_format_xmpp_error(exc)}", flush=True)
            return

        print("[probe] logout result:", flush=True)
        print(_safe(ET.tostring(result.xml, encoding="unicode")), flush=True)

    async def _request_pair_code(self) -> None:
        try:
            command = await self["xep_0050"].send_command(
                self.component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                timeout=20,
            )
            print("[probe] pair initial:", flush=True)
            print(ET.tostring(command.xml, encoding="unicode"), flush=True)
            form = command.xml.find(f".//{{{DATA_FORMS_NS}}}x")
            if form is None:
                print("[probe] pair code form missing", flush=True)
                return

            from slixmpp.plugins.xep_0004.stanza import Form

            reply = Form(xml=ET.fromstring(ET.tostring(form, encoding="unicode")))
            reply.reply()
            reply["values"] = {"phone": self.phone}
            result = await self["xep_0050"].send_command(
                self.component_jid,
                SLIDGE_PAIR_PHONE_COMMAND,
                action="complete",
                payload=reply,
                sessionid=str(command["command"]["sessionid"] or "") or None,
                timeout=20,
            )
        except Exception as exc:
            print(f"[probe] pair code error={_format_xmpp_error(exc)}", flush=True)
            return

        print("[probe] pair code result:", flush=True)
        print(ET.tostring(result.xml, encoding="unicode"), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "none",
            "logout",
            "logout-relogin",
            "relogin",
            "pair-code",
            "chat-relogin",
            "chat-qr",
        ),
    )
    parser.add_argument("--phone", default="")
    args = parser.parse_args()

    probe = WhatsAppLinkProbe(args.action, args.phone)
    plugins = (
        "xep_0030",
        "xep_0050",
        "xep_0004",
        "xep_0066",
        "xep_0231",
        "xep_0363",
        "xep_0385",
        "xep_0447",
    )
    for plugin in plugins:
        probe.register_plugin(plugin)

    if probe.settings.host:
        probe.connect((probe.settings.host, probe.settings.port))
    else:
        probe.connect()
    probe.loop.run_until_complete(probe.done.wait())
    probe.disconnect()


if __name__ == "__main__":
    main()
