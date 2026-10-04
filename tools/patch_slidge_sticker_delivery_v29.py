from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected v28 source: {old[:80]!r}")
    return source.replace(old, new, 1)


def patched_sources(root: Path) -> dict[Path, str]:
    """Validate every source before writing anything; v28 is the pinned base."""
    dispatcher = root / "slidge/core/dispatcher/message/message.py"
    mixins = root / "slidge_whatsapp/mixins.py"
    event = root / "slidge_whatsapp/event.go"
    session = root / "slidge_whatsapp/session.py"
    paths = (dispatcher, mixins, event, session)
    originals = {p: p.read_text(encoding="utf-8") for p in paths}
    markers = (
        '    description: str = ""\n',
        'Caption=(sticker.fallback or "").strip(),',
        "ptrTo(strings.TrimSpace(attach.Caption))",
        "from .sticker_pack import normalize_pack",
    )
    applied = [marker in originals[p] for p, marker in zip(paths, markers, strict=True)]
    if all(applied):
        return {}
    if any(applied):
        raise SystemExit("Partially applied v29 patch; restore the pinned base first")
    source = originals[dispatcher]
    source = replace_once(
        source,
        "    cid: str | None = None\n",
        '    cid: str | None = None\n    description: str = ""\n',
    )
    source = replace_once(
        source,
        "        is_sticker = (\n",
        """        description = ""
        # Only file metadata is alt text: never use body/URL/quoted text as a label.
        for parent in (
            "{urn:xmpp:sfs:0}file-sharing/{urn:xmpp:file:metadata:0}file",
            "{urn:xmpp:reference:0}reference/{urn:xmpp:sims:1}media-sharing/"
            "{urn:xmpp:file:metadata:0}file",
        ):
            node = msg.xml.find(parent + "/{urn:xmpp:file:metadata:0}desc")
            if node is not None and node.text:
                description = node.text.strip()[:8000]
                break
        is_sticker = (
""",
    )
    # The SFS branch and both OOB/SIMS constructors must carry the same per-message text.
    source = replace_once(
        source,
        "                is_sticker=is_sticker,\n",
        "                is_sticker=is_sticker, description=description,\n",
    )
    source = replace_once(
        source,
        'XMPPAttachment(url=msg["oob"]["url"]), is_sticker=is_sticker',
        'XMPPAttachment(url=msg["oob"]["url"]), is_sticker=is_sticker, description=description',
    )
    source = replace_once(
        source,
        'XMPPAttachment(url=source["uri"]), is_sticker=is_sticker',
        'XMPPAttachment(url=source["uri"]), is_sticker=is_sticker, description=description',
    )
    source = replace_once(
        source,
        'attachment.attachment.content_type = msg["media-type"] or None',
        "attachment.attachment.content_type = "
        'msg["reference"]["sims"]["file"]["media-type"] or None',
    )
    # XEP-0300 SHA-256 is base64, whereas BoB keys use sha256+hex.
    source = replace_once(
        source,
        '                    attachment.cid = f"{algo}+{h}" if algo and h else None',
        """                    if algo == "sha-256" and h:
                        try:
                            digest = base64.b64decode(h, validate=True)
                        except ValueError:
                            digest = b""
                        if len(digest) == 32:
                            attachment.cid = "sha256+" + digest.hex()""",
    )
    start = source.index("    async def __dispatch_nonbob_sticker(\n")
    end = source.index("    async def __dispatch_bob(\n", start)
    source = (
        source[:start]
        + """    async def __dispatch_nonbob_sticker(
        self,
        attachment: _IncomingAttachment,
        recipient: AnyRecipient,
        fallback: str,
        reply: Reply | None = None,
        thread: str | None = None,
    ) -> str | None:
        sticker = None
        if attachment.cid:
            with self.xmpp.store.session() as orm:
                sticker = self.xmpp.store.bob.get_sticker(orm, attachment.cid)
        if sticker is None:
            async with attachment.attachment.get() as response:
                response.raise_for_status()
                data = await response.read()
            cid = "sha256+" + hashlib.sha256(data).hexdigest()
            with self.xmpp.store.session() as orm:
                sticker = self.xmpp.store.bob.get_sticker(orm, cid)
                if sticker is None:
                    sticker = self.xmpp.store.bob.set_sticker(
                        orm, cid, data, attachment.attachment.content_type
                    )
                    orm.commit()
                    if sticker is None:
                        sticker = self.xmpp.store.bob.get_sticker(orm, cid)
            if sticker is None:
                raise XMPPError("not-acceptable", "Unable to prepare native sticker")
        # Reply, thread and alt text belong to this send, not the shared cached object.
        sticker = copy(sticker)
        sticker.reply = reply
        sticker.thread = thread
        sticker.fallback = attachment.description or None
        return await recipient.on_sticker(sticker)

"""
        + source[end:]
    )
    result = {dispatcher: source}
    source = replace_once(
        originals[mixins],
        '            Caption="",\n        )\n',
        '            Caption=(sticker.fallback or "").strip(),\n        )\n',
    )
    result[mixins] = source
    source = replace_once(
        originals[event],
        "\t\t\tIsAnimated:    ptrTo(animated),\n",
        "\t\t\tIsAnimated:    ptrTo(animated),\n"
        "\t\t\tAccessibilityLabel: ptrTo(strings.TrimSpace(attach.Caption)),\n",
    )
    source = replace_once(
        source,
        "\tvar result []Attachment\n\tvar info *waE2E.ContextInfo\n",
        """\tif pack := message.GetStickerPackMessage(); pack != nil {
\t\tattachment := receiveStickerPack(ctx, client.Download, pack)
\t\treturn []Attachment{attachment}, pack.GetContextInfo(), nil
\t}
\tvar result []Attachment
\tvar info *waE2E.ContextInfo
""",
    )
    result[event] = source
    source = replace_once(
        originals[session],
        "from .generated import go, whatsapp\n",
        "from .generated import go, whatsapp\nfrom .sticker_pack import normalize_pack\n",
    )
    source = replace_once(
        source,
        "        is_sticker = caption.startswith(STICKER_CAPTION_MARKER)\n",
        """        if wa_attachment.MIME == "application/x-whatsapp-sticker-pack":
            try:
                data = await normalize_pack(bytes(wa_attachment.Data))
            except Exception:
                # Keep the message visible without leaking media keys/paths in diagnostics.
                caption = "No se pudo preparar el paquete de stickers. Pide que lo reenvíen."
                return Attachment(content_type="text/plain", data=caption.encode(),
                                  caption=caption, name="paquete-no-disponible.txt")
            return Attachment(content_type="application/zip", data=data,
                              caption=caption, name="stickers.canstickers")
        is_sticker = caption.startswith(STICKER_CAPTION_MARKER)
""",
    )
    result[session] = source
    return result


def patch_package(root: Path, *, backup: bool = True) -> bool:
    sources = patched_sources(root)
    for path, source in sources.items():
        if backup:
            saved = path.with_suffix(path.suffix + ".before-sticker-delivery-v29")
            if not saved.exists():
                shutil.copy2(path, saved)
        path.write_text(source, encoding="utf-8", newline="\n")
    return bool(sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    print(
        "Applied"
        if patch_package(args.site_packages, backup=not args.no_backup)
        else "Already applied"
    )
