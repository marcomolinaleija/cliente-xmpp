from __future__ import annotations

import argparse
import ast
from pathlib import Path


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise SystemExit(f"Unexpected v30 source: {old[:80]!r}")
    return source.replace(old, new, 1)


def patch_package(root: Path) -> bool:
    """Validate all contracts and helper files before modifying the pinned v30 package."""
    package = root / "slidge_whatsapp"
    paths = {name: package / name for name in ("event.go", "mixins.py", "gateway.py")}
    originals = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    helpers = {
        "audio_modes.py": Path(__file__)
        .with_name("bridge_audio_modes.py")
        .read_text(encoding="utf-8"),
        "audio_modes.go": Path(__file__)
        .with_name("bridge_audio_modes.go")
        .read_text(encoding="utf-8"),
    }
    markers = {
        "event.go": "uploadCANAudioAttachment(ctx, client, attach)",
        "mixins.py": "from .audio_modes import",
        "gateway.py": 'add_feature("urn:can:audio-mode:0")',
    }
    applied = [markers[name] in source for name, source in originals.items()]
    if all(applied):
        if all(
            (package / name).read_text(encoding="utf-8") == source
            for name, source in helpers.items()
        ):
            return False
        raise SystemExit("Audio mode helpers differ from the applied v31 patch")
    if any(applied) or any((package / name).exists() for name in helpers):
        raise SystemExit("Partially applied v31 patch; restore the pinned base first")
    # Refuse a different upstream/base rather than claiming a capability it cannot provide.
    if "return uploadNativeStickerPack(ctx, client, attach)" not in originals["event.go"]:
        raise SystemExit("The v31 audio patch requires the v30 native-pack base")
    upload_header = (
        "func uploadAttachment(ctx context.Context, client *whatsmeow.Client, "
        "attach *Attachment) (*waE2E.Message, error) {\n"
    )
    event = replace_once(
        originals["event.go"],
        upload_header,
        upload_header
        + "\tif handled, payload, err := uploadCANAudioAttachment(ctx, client, attach); handled {\n"
        "\t\treturn payload, err\n\t}\n",
    )
    mixins = replace_once(
        originals["mixins.py"],
        "class RecipientMixin(abc.ABC):\n",
        "from .audio_modes import (\n"
        "    AUDIO_MODE_NS, MAX_AUDIO_ATTACHMENT_BYTES,\n"
        "    audio_attachment_mime, audio_attachment_caption,\n)\n\n\n"
        "class RecipientMixin(abc.ABC):\n",
    )
    mixins = replace_once(
        mixins,
        "                else:\n                    data = await resp.read()\n",
        """                elif (xmpp_msg.thread or "").startswith(AUDIO_MODE_NS):
                    if resp.content_length and resp.content_length > MAX_AUDIO_ATTACHMENT_BYTES:
                        raise XMPPError("not-acceptable", "Audio attachment exceeds 64 MiB")
                    chunks = bytearray()
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        if len(chunks) + len(chunk) > MAX_AUDIO_ATTACHMENT_BYTES:
                            raise XMPPError("not-acceptable", "Audio attachment exceeds 64 MiB")
                        chunks.extend(chunk)
                    data = bytes(chunks)
                else:
                    data = await resp.read()
""",
    )
    # v30 handles native packs after reading. Reject conflicting sticker intent before that.
    mixins = replace_once(
        mixins,
        "            content_type = resp.content_type\n",
        """            content_type = resp.content_type
        try:
            content_type = audio_attachment_mime(
                xmpp_msg.thread, att.content_type or content_type, is_sticker=att.is_sticker
            ) if (xmpp_msg.thread or "").startswith(AUDIO_MODE_NS) else audio_attachment_mime(
                xmpp_msg.thread, content_type
            )
        except ValueError as exc:
            raise XMPPError("not-acceptable", str(exc)) from exc
""",
    )
    mixins = replace_once(
        mixins,
        '            Caption="" if att.is_sticker else xmpp_msg.body or "",\n',
        '            Caption="" if att.is_sticker else audio_attachment_caption(\n'
        "                xmpp_msg.thread, xmpp_msg.body, att.url\n            ),\n",
    )
    gateway = replace_once(
        originals["gateway.py"],
        '        self["xep_0030"].add_feature("urn:can:sticker-pack:0")\n',
        '        self["xep_0030"].add_feature("urn:can:sticker-pack:0")\n'
        '        self["xep_0030"].add_feature("urn:can:audio-mode:0")\n',
    )
    changes = {"event.go": event, "mixins.py": mixins, "gateway.py": gateway, **helpers}
    for name, source in changes.items():
        if name.endswith(".py"):
            ast.parse(source)
    # Prepare helpers first and advertise the feature last. Never apply to a running service.
    for name in ("audio_modes.py", "audio_modes.go", "event.go", "mixins.py", "gateway.py"):
        (package / name).write_text(changes[name], encoding="utf-8", newline="\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prepare explicit audio modes on the pinned v30 bridge"
    )
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Applied" if patch_package(args.site_packages) else "Already applied")
