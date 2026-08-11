from __future__ import annotations

# ruff: noqa: E501, I001

import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Could not patch {description}: expected one match, found {count}.")
    return text.replace(old, new, 1)


def write(path: Path, text: str, *, backup: bool) -> None:
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-native-stickers")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_mixins(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if 'MIME="application/x-whatsapp-can-sticker" if att.is_sticker' in source:
        return False
    source = replace_once(
        source,
        "            MIME=content_type,\n            Filename=basename(att.url),\n            Data=go.Slice_byte.from_bytes(data),  # type:ignore[no-untyped-call]\n            Caption=xmpp_msg.body or \"\",\n            ViewOnce=xmpp_msg.thread == VIEW_ONCE_THREAD,\n",
        "            MIME=\"application/x-whatsapp-can-sticker\" if att.is_sticker else content_type,\n            Filename=basename(att.url),\n            Data=go.Slice_byte.from_bytes(data),  # type:ignore[no-untyped-call]\n            Caption=\"\" if att.is_sticker else xmpp_msg.body or \"\",\n            ViewOnce=xmpp_msg.thread == VIEW_ONCE_THREAD,\n",
        "slidge_whatsapp mixins sticker attachment",
    )
    write(path, source, backup=backup)
    return True


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "func uploadStickerAttachment(" in source:
        return False
    source = replace_once(
        source,
        "var knownMediaTypes = map[string]whatsmeow.MediaType{\n",
        "const nativeStickerMIME = \"application/x-whatsapp-can-sticker\"\n\nvar knownMediaTypes = map[string]whatsmeow.MediaType{\n",
        "native sticker MIME marker",
    )
    marker = "// UploadAttachment attempts to push the given attachment data to WhatsApp according to the MIME\n"
    helper = '''func uploadStickerAttachment(ctx context.Context, client *whatsmeow.Client, attach *Attachment) (*waE2E.Message, error) {
	if media.DetectMIMEType(attach.Data) != media.TypeWebP {
		return nil, fmt.Errorf("native WhatsApp stickers must be WebP")
	}
	spec, err := attach.GetSpec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed reading sticker metadata: %w", err)
	}
	if spec.ImageWidth < 1 || spec.ImageHeight < 1 {
		return nil, fmt.Errorf("sticker has no image dimensions")
	}
	attach.MIME = string(media.TypeWebP)
	upload, err := client.Upload(ctx, attach.Data, whatsmeow.MediaImage)
	if err != nil {
		return nil, err
	}
	return buildStickerMessage(attach, upload, spec), nil
}

func buildStickerMessage(attach *Attachment, upload whatsmeow.UploadResponse, spec *media.Spec) *waE2E.Message {
	mime := string(media.TypeWebP)
	animated := spec.ImageFrameRate > 0
	return &waE2E.Message{
		StickerMessage: &waE2E.StickerMessage{
			URL:           &upload.URL,
			DirectPath:    &upload.DirectPath,
			MediaKey:      upload.MediaKey,
			FileEncSHA256: upload.FileEncSHA256,
			FileSHA256:    upload.FileSHA256,
			FileLength:    ptrTo(uint64(len(attach.Data))),
			Mimetype:      &mime,
			Width:         ptrTo(uint32(spec.ImageWidth)),
			Height:        ptrTo(uint32(spec.ImageHeight)),
			IsAnimated:    ptrTo(animated),
		},
	}
}

'''
    source = replace_once(source, marker, helper + marker, "native sticker helpers")
    source = replace_once(
        source,
        "func uploadAttachment(ctx context.Context, client *whatsmeow.Client, attach *Attachment) (*waE2E.Message, error) {\n\tvar originalMIME = attach.MIME\n",
        "func uploadAttachment(ctx context.Context, client *whatsmeow.Client, attach *Attachment) (*waE2E.Message, error) {\n\tif attach.MIME == nativeStickerMIME {\n\t\treturn uploadStickerAttachment(ctx, client, attach)\n\t}\n\n\tvar originalMIME = attach.MIME\n",
        "native sticker upload branch",
    )
    write(path, source, backup=backup)
    return True


def patch_package(package_root: Path, *, backup: bool) -> bool:
    package_root = package_root.resolve()
    mixins = package_root / "slidge_whatsapp/mixins.py"
    event_go = package_root / "slidge_whatsapp/event.go"
    missing = [str(path) for path in (mixins, event_go) if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changed_mixins = patch_mixins(mixins, backup=backup)
    changed_event_go = patch_event_go(event_go, backup=backup)
    return changed_mixins or changed_event_go


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send XEP-0449 XMPP stickers as native WhatsApp StickerMessage payloads."
    )
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    changed = patch_package(args.package_root, backup=not args.no_backup)
    print("Native WhatsApp sticker patch applied." if changed else "Native WhatsApp sticker patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
