from __future__ import annotations

from pathlib import Path

import slidge_whatsapp
import slidge_whatsapp.generated._whatsapp  # noqa: F401

package_dir = Path(slidge_whatsapp.__file__).parent
event_source = (package_dir / "event.go").read_text(encoding="utf-8")
start = event_source.index("func getMessageAttachments(")
end = event_source.index("\n}\n", start) + 3
function = event_source[start:end]

assert "Preserve incoming WhatsApp voice notes" in function
assert "a.MIME = msg.GetMimetype()" in function
assert "a.Data = data" in function
assert "convertSpec" not in function
assert "media.Convert(ctx, a.Data" not in function
assert "failed to convert incoming attachment" not in function

binary = package_dir / "generated" / "_whatsapp.cpython-313-x86_64-linux-gnu.so"
assert binary.is_file()
assert b"failed to convert incoming attachment" not in binary.read_bytes()

print("incoming WhatsApp PTT remains Ogg/Opus: ok")
