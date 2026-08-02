from __future__ import annotations

import argparse
import shutil
from pathlib import Path

OLD_CONVERT_DECLARATION = "\tvar convertSpec *media.Spec\n"

OLD_AUDIO_CASE = '''\t\tcase *waE2E.AudioMessage:
\t\t\t// Convert Opus-encoded voice messages to AAC-encoded audio, which has better support.
\t\t\ta.MIME = msg.GetMimetype()
\t\t\tif msg.GetPTT() {
\t\t\t\tconvertSpec = &media.Spec{MIME: media.TypeM4A}
\t\t\t}
'''

NEW_AUDIO_CASE = '''\t\tcase *waE2E.AudioMessage:
\t\t\t// Preserve incoming WhatsApp voice notes as their original Ogg/Opus payload.
\t\t\t// The XMPP client supports Opus directly, so transcoding to AAC only adds loss.
\t\t\ta.MIME = msg.GetMimetype()
'''

OLD_INCOMING_CONVERSION_COMMENT = (
    "\t\t// Convert incoming data if a specification has been given, "
    "ignoring any errors that occur.\n"
)

OLD_INCOMING_CONVERSION = '''\t\tif convertSpec != nil {
\t\t\tdata, err = media.Convert(ctx, a.Data, convertSpec)
\t\t\tif err != nil {
\t\t\t\tclient.Log.Warnf("failed to convert incoming attachment: %s", err)
\t\t\t} else {
\t\t\t\ta.Data, a.MIME = data, string(convertSpec.MIME)
\t\t\t}
\t\t}
'''

OLD_INCOMING_CONVERSION_SCOPED = OLD_INCOMING_CONVERSION.replace(
    "data, err = media.Convert",
    "data, err := media.Convert",
)

PATCH_MARKER = "Preserve incoming WhatsApp voice notes as their original Ogg/Opus payload."


def patch_event_go(path: Path, *, backup: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return False

    expected = (
        (OLD_CONVERT_DECLARATION, "incoming conversion declaration"),
        (OLD_AUDIO_CASE, "incoming audio case"),
        (OLD_INCOMING_CONVERSION_COMMENT, "incoming conversion comment"),
    )
    for source, label in expected:
        count = text.count(source)
        if count != 1:
            raise SystemExit(
                f"Could not patch {path}: expected one {label}, found {count}."
            )

    conversion_candidates = [
        source
        for source in (
            OLD_INCOMING_CONVERSION,
            OLD_INCOMING_CONVERSION_SCOPED,
        )
        if source in text
    ]
    if len(conversion_candidates) != 1:
        raise SystemExit(
            f"Could not patch {path}: expected one incoming conversion block, "
            f"found {len(conversion_candidates)}."
        )

    if backup:
        backup_path = path.with_suffix(path.suffix + ".before-incoming-ptt")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    updated = text.replace(OLD_CONVERT_DECLARATION, "", 1)
    updated = updated.replace(OLD_AUDIO_CASE, NEW_AUDIO_CASE, 1)
    updated = updated.replace(OLD_INCOMING_CONVERSION_COMMENT, "", 1)
    updated = updated.replace(conversion_candidates[0], "", 1)
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch slidge-whatsapp so incoming WhatsApp PTT messages retain "
            "their original Ogg/Opus payload instead of being transcoded to AAC."
        )
    )
    parser.add_argument(
        "slidge_whatsapp_tree",
        type=Path,
        help="Path containing the slidge_whatsapp package directory.",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    event_path = args.slidge_whatsapp_tree.resolve() / "slidge_whatsapp" / "event.go"
    if not event_path.is_file():
        raise SystemExit(f"File not found: {event_path}")

    if patch_event_go(event_path, backup=not args.no_backup):
        print("Incoming WhatsApp PTT preservation patch applied; rebuild the bridge.")
    else:
        print("Incoming WhatsApp PTT preservation patch already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
