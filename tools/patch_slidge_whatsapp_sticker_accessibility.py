from __future__ import annotations

# ruff: noqa: E501
import argparse
import shutil
from pathlib import Path


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Could not patch {description}: expected one match, found {count}."
        )
    return text.replace(old, new, 1)


def patch_event_go(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "func lottieStickerAccessibilityCaption(" in source:
        return False

    source = replace_once(
        source,
        '''import (
\t// Standard library.
\t"context"
\t"encoding/hex"
''',
        '''import (
\t// Standard library.
\t"archive/zip"
\t"bytes"
\t"context"
\t"encoding/hex"
\t"encoding/json"
\t"io"
''',
        "sticker accessibility imports",
    )

    helper = r'''const (
	stickerCaptionMarker          = "\x1eWHATSAPP_STICKER\x1f"
	lottieStickerMetadataPath     = "animation/animation.json.overridden_metadata"
	maxLottieStickerPackageBytes = 5 * 1024 * 1024
	maxLottieStickerMetadataBytes = 64 * 1024
	maxLottieStickerArchiveFiles  = 32
)

type lottieStickerMetadata struct {
	AccessibilityText string   `json:"accessibility-text"`
	Emojis            []string `json:"emojis"`
}

func stickerAccessibilityCaption(label, emojis string) string {
	if label = strings.TrimSpace(label); label != "" {
		return label
	}
	return strings.TrimSpace(emojis)
}

func lottieStickerAccessibilityCaption(data []byte) string {
	if len(data) == 0 || len(data) > maxLottieStickerPackageBytes {
		return ""
	}

	archive, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(archive.File) > maxLottieStickerArchiveFiles {
		return ""
	}

	for _, file := range archive.File {
		if file.Name != lottieStickerMetadataPath {
			continue
		}
		if file.UncompressedSize64 == 0 || file.UncompressedSize64 > maxLottieStickerMetadataBytes {
			return ""
		}

		reader, err := file.Open()
		if err != nil {
			return ""
		}
		payload, readErr := io.ReadAll(io.LimitReader(reader, maxLottieStickerMetadataBytes+1))
		closeErr := reader.Close()
		if readErr != nil || closeErr != nil || len(payload) > maxLottieStickerMetadataBytes {
			return ""
		}

		var metadata lottieStickerMetadata
		if json.Unmarshal(payload, &metadata) != nil {
			return ""
		}
		if label := strings.TrimSpace(metadata.AccessibilityText); label != "" {
			return label
		}

		emojis := make([]string, 0, len(metadata.Emojis))
		for _, emoji := range metadata.Emojis {
			if emoji = strings.TrimSpace(emoji); emoji != "" {
				emojis = append(emojis, emoji)
			}
		}
		return strings.Join(emojis, " ")
	}
	return ""
}

'''
    source = replace_once(
        source,
        "// GetMessageAttachments fetches and decrypts attachments (images, audio, video, or documents) sent\n",
        helper
        + "// GetMessageAttachments fetches and decrypts attachments (images, audio, video, or documents) sent\n",
        "bounded Lottie accessibility metadata parser",
    )
    source = replace_once(
        source,
        '''\t\tcase *waE2E.StickerMessage:
\t\t\ta.MIME = msg.GetMimetype()
\t\t\tinfo = msg.GetContextInfo()
''',
        '''\t\tcase *waE2E.StickerMessage:
\t\t\ta.MIME = msg.GetMimetype()
\t\t\ta.Caption = stickerCaptionMarker + stickerAccessibilityCaption(
\t\t\t\tmsg.GetAccessibilityLabel(),
\t\t\t\tmsg.GetEmojis(),
\t\t\t)
\t\t\tinfo = msg.GetContextInfo()
''',
        "native sticker accessibility fields",
    )
    source = replace_once(
        source,
        '''\t\ta.Data = data

\t\t// Set filename from SHA256 checksum and MIME type, if none is already set.
''',
        '''\t\ta.Data = data
\t\tif strings.HasPrefix(a.Caption, stickerCaptionMarker) &&
\t\t\tstrings.TrimSpace(strings.TrimPrefix(a.Caption, stickerCaptionMarker)) == "" {
\t\t\ta.Caption = stickerCaptionMarker + lottieStickerAccessibilityCaption(data)
\t\t}

\t\t// Set filename from SHA256 checksum and MIME type, if none is already set.
''',
        "Lottie sticker accessibility extraction",
    )

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-sticker-accessibility")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_session(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    if "STICKER_CAPTION_MARKER =" in source:
        return False

    source = replace_once(
        source,
        "class Attachment(LegacyAttachment):\n",
        'STICKER_CAPTION_MARKER = "\\x1eWHATSAPP_STICKER\\x1f"\n\n\n'
        "class Attachment(LegacyAttachment):\n",
        "Python sticker caption marker",
    )
    source = replace_once(
        source,
        '''        return Attachment(
            content_type=wa_attachment.MIME,
            data=bytes(wa_attachment.Data),
            caption=(
                wa_attachment.Caption
                if muc is None
                else await muc.replace_mentions(wa_attachment.Caption)
            ),
            name=wa_attachment.Filename,
        )
''',
        '''        caption = (
            wa_attachment.Caption
            if muc is None
            else await muc.replace_mentions(wa_attachment.Caption)
        )
        is_sticker = caption.startswith(STICKER_CAPTION_MARKER)
        if is_sticker:
            caption = caption.removeprefix(STICKER_CAPTION_MARKER).strip()
        return Attachment(
            content_type=wa_attachment.MIME,
            data=bytes(wa_attachment.Data),
            caption=caption,
            name=wa_attachment.Filename,
            is_sticker=is_sticker,
        )
''',
        "sticker marker conversion",
    )
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-sticker-accessibility")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_core_attachment(path: Path, *, backup: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    marker = "caption=None if attachment.is_sticker else attachment.caption,"
    if marker in source:
        return False
    source = replace_once(
        source,
        '''        msgs = self.__send_url(
            msg,
            legacy_msg_id,
            uploaded_url=new_url,
            caption=attachment.caption,
''',
        '''        msgs = self.__send_url(
            msg,
            legacy_msg_id,
            uploaded_url=new_url,
            caption=None if attachment.is_sticker else attachment.caption,
''',
        "single-message accessible sticker forwarding",
    )
    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-sticker-accessibility")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
    path.write_text(source, encoding="utf-8", newline="\n")
    return True


def patch_package(site_packages: Path, *, backup: bool) -> bool:
    site_packages = site_packages.resolve()
    paths = (
        site_packages / "slidge_whatsapp/event.go",
        site_packages / "slidge_whatsapp/session.py",
        site_packages / "slidge/core/mixins/attachment.py",
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("Missing bridge files:\n" + "\n".join(missing))
    changes = (
        patch_event_go(paths[0], backup=backup),
        patch_session(paths[1], backup=backup),
        patch_core_attachment(paths[2], backup=backup),
    )
    return any(changes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve WhatsApp sticker accessibility descriptions in attachment captions."
    )
    parser.add_argument("site_packages", type=Path)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    changed = patch_package(args.site_packages, backup=not args.no_backup)
    print(
        "Sticker accessibility patch applied."
        if changed
        else "Sticker accessibility patch already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
