"""Authenticated status probe with an isolated low-priority XMPP resource; no pairing commands."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from uuid import uuid4
from xml.etree import ElementTree as ET

from slixmpp import JID, ClientXMPP

from cliente_xmpp.config.credentials import CredentialStore
from cliente_xmpp.config.settings import CONNECTION_MODE_REMOTE, SettingsStore
from cliente_xmpp.xmpp.linking import NAMESPACE

PROGRESS = {"stage": "configuration", "authenticated_xmpp": False}


async def probe():
    settings = SettingsStore().load_connection_profile(CONNECTION_MODE_REMOTE)
    domain = JID(settings.jid).domain
    if domain not in {"xmpp.rayoscompany.com", "rayoscompany.com"}:
        raise ValueError("No hay un perfil remoto para el servidor autorizado")
    password = CredentialStore().get_password(settings.jid)
    if not password:
        raise ValueError("No se encontró la credencial remota en el almacén seguro")
    # Never replace the resource of the CAN instance currently being tested.
    resource = f"can-linking-probe-{uuid4().hex[:12]}"
    client = ClientXMPP(f"{JID(settings.jid).bare}/{resource}", password)
    client.force_starttls = settings.use_tls
    client.enable_starttls = settings.use_tls
    client.enable_direct_tls = False
    if settings.ca_file:
        client.ssl_context = ssl.create_default_context(cafile=settings.ca_file)
    client.register_plugin("xep_0030")
    result = asyncio.get_running_loop().create_future()
    component = "whatsapp." + domain

    async def connected(_):
        PROGRESS.update(stage="discovery", authenticated_xmpp=True)
        # This gateway requires an available resource for IQ routing. A unique,
        # negative-priority resource cannot replace CAN or take bare-JID messages.
        # Group history-on-join is bounded separately on the bridge.
        client.send_presence(ppriority=-128)
        PROGRESS["peer"] = client.transport.get_extra_info("peername")
        PROGRESS["component"] = component
        try:
            info = await client["xep_0030"].get_info(jid=component, timeout=10)
            features = {node.get("var") for node in info.xml.findall(
                ".//{http://jabber.org/protocol/disco#info}feature")}
            if NAMESPACE not in features:
                raise ValueError("El componente no anuncia el nuevo contrato")
            PROGRESS["stage"] = "status"
            iq = client.make_iq_get(ito=component)
            ET.SubElement(iq.xml, f"{{{NAMESPACE}}}query", {"op": "status"})
            reply = await iq.send(timeout=15)
            node = reply.xml.find(f"{{{NAMESPACE}}}state")
            if node is None or node.get("version") != "1":
                raise ValueError("Respuesta de estado incompatible")
            result.set_result({
                "authenticated_xmpp": True, "linking_contract": 1,
                "whatsapp_authenticated": node.get("authenticated") == "1",
                "phase": node.get("phase"), "sync": node.get("sync"),
                "reason": node.get("reason", ""),
                "voice_capability": "urn:can:whatsapp:voice:1" in features,
            })
        except Exception as exc:
            if not result.done():
                result.set_exception(exc)

    client.add_event_handler("session_start", connected)
    try:
        client.connect(settings.host, settings.port) if settings.host else client.connect()
        return await asyncio.wait_for(result, 40)
    finally:
        client.disconnect()
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(asyncio.run(probe())))
    except Exception as exc:
        print(json.dumps({"probe": "failed", "error_type": type(exc).__name__, **PROGRESS}))
        raise SystemExit(1) from None
