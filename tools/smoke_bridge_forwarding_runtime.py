from xml.etree import ElementTree as ET

from slidge.core.mixins.message_text import (
    WHATSAPP_FORWARDED_TAG,
    add_whatsapp_forwarded_flag,
)
from slidge.util.types import XMPPAttachmentMessage, XMPPTextMessage
from slidge_whatsapp.generated import whatsapp
from slixmpp import Message

EXPECTED_TAG = "{urn:marco-ml:whatsapp:forwarded:0}forwarded"


def main() -> None:
    assert WHATSAPP_FORWARDED_TAG == EXPECTED_TAG

    stanza = Message()
    add_whatsapp_forwarded_flag(stanza, True)
    assert stanza.xml.find(EXPECTED_TAG) is not None

    normal_stanza = Message()
    add_whatsapp_forwarded_flag(normal_stanza, False)
    assert normal_stanza.xml.find(EXPECTED_TAG) is None

    parsed = ET.fromstring(
        '<message><forwarded xmlns="urn:marco-ml:whatsapp:forwarded:0" /></message>'
    )
    is_forwarded = parsed.find(EXPECTED_TAG) is not None
    assert XMPPTextMessage(body="texto", is_forwarded=is_forwarded).is_forwarded
    assert XMPPAttachmentMessage(
        body=None,
        attachments=(),
        is_forwarded=is_forwarded,
    ).is_forwarded

    outgoing = whatsapp.Message(IsForwarded=True)
    assert outgoing.IsForwarded is True
    print("forwarding runtime smoke: ok")


if __name__ == "__main__":
    main()
