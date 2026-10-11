"""Apply avatar safety only to the pinned, complete v31 Python package."""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path

BASE_SHA256 = {
    "gateway.py": "0089464111a102e34ac46886c568fe7df8491407979543ab7a1db99ba48bdbb6",
    "session.py": "106c0a60fcff2e757dfe433eac860fa5b955dbe12c81d98f3adf9e79920b3fd7",
}
GATEWAY_CHANGES = (
    (
        "from . import config\n",
        "from . import config\n"
        "from .avatar_safety import disable_avatar_sync, safe_avatar_preferences\n",
    ),
    (
        "        *BaseGateway.PREFERENCES,\n",
        "        *safe_avatar_preferences(BaseGateway.PREFERENCES),\n",
    ),
    (
        "        super().__init__()\n",
        "        super().__init__()\n"
        "        migrated = disable_avatar_sync(self.store)\n"
        '        log.info("Avatar synchronization disabled for %s stored accounts", migrated)\n',
    ),
)
SESSION_CHANGE = (
    '''        """
        Update profile picture in WhatsApp for corresponding avatar change in XMPP.
        """
        self.whatsapp.SetAvatar(
            "",
            BINDING_CALL
        )
'''.replace(
        "BINDING_CALL",
        "go.Slice_byte.from_bytes(bytes_) if bytes_ else go.Slice_byte(),  "
        "# type:ignore[no-untyped-call]",
    ),
    '''        """The own WhatsApp profile picture is managed in WhatsApp only."""
        # Missing XMPP metadata is not a request to delete a WhatsApp picture.
        # Also reject nonempty updates, including stale XMPP profile images.
        raise NotImplementedError("WhatsApp profile picture synchronization is disabled")
''',
)


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise SystemExit("Unexpected avatar safety source contract")
    return source.replace(old, new, 1)


def patch_package(root: Path) -> bool:
    package = root / "slidge_whatsapp"
    paths = {name: package / name for name in BASE_SHA256}
    sources = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
    helper = Path(__file__).with_name("bridge_avatar_safety.py").read_text(encoding="utf-8")
    helper_path = package / "avatar_safety.py"
    transformations = {"gateway.py": GATEWAY_CHANGES, "session.py": (SESSION_CHANGE,)}
    applied = [new in sources[name] for name, pairs in transformations.items() for _, new in pairs]
    if any(applied) and not all(applied):
        raise SystemExit("Partially applied v32 avatar safety patch")
    originals = dict(sources)
    if all(applied):
        if not helper_path.exists() or helper_path.read_text(encoding="utf-8") != helper:
            raise SystemExit("Applied v32 helper differs or is missing")
        for name, pairs in transformations.items():
            for old, new in reversed(pairs):
                originals[name] = replace_once(originals[name], new, old)
    elif helper_path.exists():
        raise SystemExit("Unexpected existing avatar safety helper")
    for name, source in originals.items():
        if hashlib.sha256(source.encode()).hexdigest() != BASE_SHA256[name]:
            raise SystemExit(f"Unexpected pinned v31 source: {name}")
    if all(applied):
        return False
    changes = {"avatar_safety.py": helper}
    for name, pairs in transformations.items():
        source = originals[name]
        for old, new in pairs:
            source = replace_once(source, old, new)
        changes[name] = source
    for source in changes.values():
        ast.parse(source)
    for name, source in changes.items():
        (package / name).write_text(source, encoding="utf-8", newline="\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Protect the own WhatsApp profile picture on v31")
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    print("Applied" if patch_package(args.site_packages) else "Already applied")
