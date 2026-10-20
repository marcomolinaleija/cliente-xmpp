"""Fail-closed experimental v35 voice lifecycle patch. Does not deploy or connect."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

MODULE = "codeberg.org/slidge/slidge-whatsapp/slidge_whatsapp"
MARKER = "// CAN experimental voice lifecycle v1"


def replace(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"Voice patch requires one exact anchor, found {source.count(old)}")
    return source.replace(old, new)


def native_session(source: str) -> str:
    if MARKER in source:
        return source
    source = replace(source, f'"{MODULE}/media"',
                     f'"{MODULE}/media"\n\t"{MODULE}/voicecore"')
    old = '''func (s *Session) setClient(client *whatsmeow.Client) {
\ts.clientMutex.Lock()
\ts.client = client
\ts.clientMutex.Unlock()
}
'''
    source = replace(source, old, '''func (s *Session) setClient(client *whatsmeow.Client) {
\t// CAN experimental voice lifecycle v1
\ts.clientMutex.Lock()
\told := s.client
\ts.client = client
\ts.clientMutex.Unlock()
\tif old != nil { voicecore.Detach(old) }
\tvoicecore.Attach(s.ctx, client)
\tif s.currentClient() != client { voicecore.Detach(client) }
}
''')
    old = '''func (s *Session) clearClient(client *whatsmeow.Client) {
\ts.clientMutex.Lock()
\tif s.client == client {
\t\ts.canCancelCallRecoveries()
\t\ts.client = nil
\t}
\ts.clientMutex.Unlock()
}
'''
    source = replace(source, old, '''func (s *Session) clearClient(client *whatsmeow.Client) {
\ts.clientMutex.Lock()
\tcleared := s.client == client
\tif cleared {
\t\ts.canCancelCallRecoveries()
\t\ts.client = nil
\t}
\ts.clientMutex.Unlock()
\tif cleared { voicecore.Detach(client) }
}
''')
    return replace(source, "\ts.canTraceCallSignal(evt)\n",
                   "\tvoicecore.Dispatch(client, evt)\n\ts.canTraceCallSignal(evt)\n")


def python_gateway(source: str) -> str:
    if "        install_voice(self)\n" in source:
        return source
    source = replace(source, "from .generated import whatsapp\n",
                     "from .generated import whatsapp\n"
                     "from .voice import install as install_voice\n")
    return replace(source, "        self.whatsapp.Init()\n",
                   "        self.whatsapp.Init()\n        install_voice(self)\n")


def python_session(source: str) -> str:
    if "        await revoke_voice(self)\n" in source:
        return source
    source = replace(source, "from __future__ import annotations\n",
                     "from __future__ import annotations\n"
                     "from .voice import revoke as revoke_voice\n")
    return replace(source, "        self.whatsapp.Disconnect()  # type:ignore[no-untyped-call]\n",
                   "        await revoke_voice(self)\n"
                   "        self.whatsapp.Disconnect()  # type:ignore[no-untyped-call]\n")


def patch(package: Path, core: Path, upstream: Path, helper: Path) -> None:
    transforms = {"session.go": native_session, "gateway.py": python_gateway,
                  "session.py": python_session}
    # Check every upstream anchor and input before any source is changed.
    outputs = {name: transform((package / name).read_text(encoding="utf-8"))
               for name, transform in transforms.items()}
    copies: dict[Path, bytes] = {package / "voice.py": helper.read_bytes()}
    for directory, target in ((core, package / "voicecore"),
                              (upstream / "internal" / "voip", package / "voicecore/internal/voip"),
                              (upstream / "internal" / "wa", package / "voicecore/internal/wa")):
        if not directory.is_dir():
            raise ValueError(f"Missing voice source: {directory.name}")
        for source in directory.rglob("*"):
            if not source.is_file():
                continue
            if source.suffix == ".go":
                body = source.read_text(encoding="utf-8").replace(
                    '"wacalls/internal/', f'"{MODULE}/voicecore/internal/',
                ).encode()
            else:
                # go:embed MLow tables and upstream test vectors are part of the codec.
                body = source.read_bytes()
            copies[target / source.relative_to(directory)] = body
    licenses = [upstream / "LICENSE", upstream / "internal/voip/media/mlow/LICENSE"]
    for license_file in licenses:
        if not license_file.is_file():
            raise ValueError("Missing upstream license")
    for name, output in outputs.items():
        (package / name).write_text(output, encoding="utf-8", newline="\n")
    for path, body in copies.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    target = package / "voicecore/licenses"
    target.mkdir(parents=True, exist_ok=True)
    for name, license_file in zip(("WaCalls", "MLow"), licenses, strict=True):
        shutil.copyfile(license_file, target / f"{name}.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("core", type=Path)
    parser.add_argument("upstream", type=Path)
    parser.add_argument("helper", type=Path)
    args = parser.parse_args()
    patch(args.package, args.core, args.upstream, args.helper)


if __name__ == "__main__":
    main()
