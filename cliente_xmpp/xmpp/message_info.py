from __future__ import annotations

from xml.etree import ElementTree as ET

from cliente_xmpp.models.message_info import GroupMessageInfo, ParticipantReceipt

MESSAGE_INFO_NS = "urn:can:whatsapp:message-info:0"
MAX_PARTICIPANTS = 2048


def parse_message_info(xml: ET.Element, chat_jid: str, message_id: str) -> GroupMessageInfo:
    node = xml.find(f"{{{MESSAGE_INFO_NS}}}info")
    if node is None or node.get("id") != message_id:
        raise ValueError("La respuesta no corresponde al mensaje solicitado.")
    rows = []
    for participant in node.findall(f"{{{MESSAGE_INFO_NS}}}participant"):
        if len(rows) >= MAX_PARTICIPANTS:
            raise ValueError("La respuesta tiene demasiados participantes.")
        jid, lid = participant.get("jid", ""), participant.get("lid", "")
        if not jid and not lid:
            raise ValueError("La respuesta contiene un participante sin identidad.")
        times = []
        for attr in ("delivered-at", "read-at"):
            value = int(participant.get(attr, "0"))
            if value < 0 or value > 253402300799:
                raise ValueError("La respuesta contiene una fecha inválida.")
            times.append(value)
        rows.append(ParticipantReceipt(jid, lid, participant.get("name", ""), *times))
    return GroupMessageInfo(
        chat_jid,
        message_id,
        node.get("legacy-id", ""),
        node.get("recipients-complete") == "true",
        tuple(rows),
    )
