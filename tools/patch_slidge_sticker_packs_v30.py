from __future__ import annotations

import argparse
from pathlib import Path

from patch_slidge_sticker_delivery_v29 import replace_once


def patch_package(root: Path) -> bool:
    paths = {
        name: root / "slidge_whatsapp" / name
        for name in ("event.go", "mixins.py", "gateway.py", "session.go")
    }
    paths["whatsmeow_send.go"] = (
        root / "slidge_whatsapp/vendor/go.mau.fi/whatsmeow/send.go"
    )
    originals = {name: p.read_text(encoding="utf-8") for name, p in paths.items()}
    markers = {
        "event.go": "return uploadNativeStickerPack(ctx, client, attach)",
        "mixins.py": "from .outgoing_sticker_pack import prepare_outgoing_pack",
        "gateway.py": 'add_feature("urn:can:sticker-pack:0")',
        "session.go": "mergeContext(&payload.StickerPackMessage.ContextInfo)",
        "whatsmeow_send.go": '\t\treturn "sticker_pack"',
    }
    applied = [markers[name] in originals[name] for name in paths]
    if all(applied):
        return False
    if any(applied):
        raise SystemExit("Partially applied v30 patch")
    event = replace_once(
        originals["event.go"],
        "\tif attach.MIME == nativeStickerMIME {\n",
        "\tif attach.MIME == nativePackMIME {\n"
        "\t\treturn uploadNativeStickerPack(ctx, client, attach)\n\t}\n"
        "\tif attach.MIME == nativeStickerMIME {\n",
    )
    mixins = replace_once(
        originals["mixins.py"],
        "class RecipientMixin(abc.ABC):\n",
        "from .outgoing_sticker_pack import prepare_outgoing_pack\n\n\n"
        "class RecipientMixin(abc.ABC):\n",
    )
    needle = "            content_type = resp.content_type\n"
    mixins = replace_once(
        mixins,
        "                data = await resp.read()\n",
        """                if att.content_type == "application/x-can-sticker-pack":
                    if resp.content_length and resp.content_length > 32 * 1024 * 1024:
                        raise XMPPError("not-acceptable", "Native sticker pack too large")
                    chunks = bytearray()
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > 32 * 1024 * 1024:
                            raise XMPPError("not-acceptable", "Native sticker pack too large")
                    data = bytes(chunks)
                else:
                    data = await resp.read()
""",
    )
    mixins = replace_once(
        mixins,
        needle,
        needle
        + """        if att.content_type == "application/x-can-sticker-pack":
            try:
                data = await prepare_outgoing_pack(data)
            except Exception as exc:
                raise XMPPError(
                    "not-acceptable", "Invalid native pack (1 to 60 compatible stickers required)"
                ) from exc
            content_type = "application/x-can-sticker-pack"
""",
    )
    gateway = replace_once(
        originals["gateway.py"],
        "        super().__init__()\n",
        "        super().__init__()\n"
        '        self["xep_0030"].add_feature("urn:can:sticker-pack:0")\n',
    )
    session = replace_once(
        originals["session.go"],
        "\tcase payload.StickerMessage != nil:\n"
        "\t\tmergeContext(&payload.StickerMessage.ContextInfo)\n",
        "\tcase payload.StickerMessage != nil:\n"
        "\t\tmergeContext(&payload.StickerMessage.ContextInfo)\n"
        "\tcase payload.StickerPackMessage != nil:\n"
        "\t\tmergeContext(&payload.StickerPackMessage.ContextInfo)\n",
    )
    session = replace_once(
        session,
        "\tcase payload.DocumentMessage != nil:\n"
        "\t\tsetFlag(&payload.DocumentMessage.ContextInfo)\n",
        "\tcase payload.DocumentMessage != nil:\n"
        "\t\tsetFlag(&payload.DocumentMessage.ContextInfo)\n"
        "\tcase payload.StickerPackMessage != nil:\n"
        "\t\tsetFlag(&payload.StickerPackMessage.ContextInfo)\n",
    )
    routing = replace_once(
        originals["whatsmeow_send.go"],
        '\tcase msg.StickerMessage != nil:\n\t\treturn "sticker"\n',
        '\tcase msg.StickerPackMessage != nil:\n\t\treturn "sticker_pack"\n'
        '\tcase msg.StickerMessage != nil:\n\t\treturn "sticker"\n',
    )
    for name, source in {
        "event.go": event,
        "mixins.py": mixins,
        "gateway.py": gateway,
        "session.go": session,
        "whatsmeow_send.go": routing,
    }.items():
        paths[name].write_text(source, encoding="utf-8", newline="\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Applied" if patch_package(args.site_packages) else "Already applied")
