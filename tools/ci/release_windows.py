"""Publish verified binaries from an existing client tag without changing that tag."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "marcomolinaleija/cliente-xmpp"
TAG_PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def command(args: list[str], root: Path) -> str:
    return subprocess.check_output(args, cwd=root, text=True, encoding="utf-8").strip()


def source_version(tag: str, root: Path) -> str:
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError("La etiqueta del cliente debe tener formato vX.Y.Z")
    version = tag[1:]
    if tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ] != version:
        raise ValueError("La etiqueta no coincide con pyproject.toml")
    tree = ast.parse((root / "cliente_xmpp/__init__.py").read_text(encoding="utf-8"))
    declared = next((node.value.value for node in tree.body
                     if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                     and any(isinstance(target, ast.Name) and target.id == "__version__"
                             for target in node.targets)), None)
    if declared != version:
        raise ValueError("La etiqueta no coincide con cliente_xmpp.__version__")
    resource = (root / "windows_version_info.txt").read_text(encoding="utf-8")
    fixed = ", ".join((*version.split("."), "0"))
    for field in ("FileVersion", "ProductVersion"):
        if f"StringStruct(u'{field}', u'{version}')" not in resource:
            raise ValueError(f"La etiqueta no coincide con {field}")
    for field in ("filevers", "prodvers"):
        if f"{field}=({fixed})" not in resource:
            raise ValueError(f"La etiqueta no coincide con {field}")
    notes = (root / "release_notes.txt").read_text(encoding="utf-8-sig").strip()
    if not notes.startswith(f"WhatsApp CAN {version}\n"):
        raise ValueError("Las notas deben empezar con el título de esta versión")
    return version


def remote_tag_commit(text: str, tag: str) -> str:
    references = dict(line.split() for line in text.splitlines())
    # ls-remote prints SHA then ref; prefer the peeled commit of annotated tags.
    by_ref = {ref: sha for sha, ref in references.items()}
    commit = by_ref.get(f"refs/tags/{tag}^{{}}", by_ref.get(f"refs/tags/{tag}", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("No se pudo resolver la etiqueta publicada en origin")
    return commit


def check_releases(tag: str, releases: list[dict]) -> None:
    candidate = tuple(map(int, tag[1:].split(".")))
    for release in releases:
        other = release["tag_name"]
        if other == tag:
            raise ValueError("La release ya existe; no se modificará ni sustituirá")
        if release["draft"] or release["prerelease"] or TAG_PATTERN.fullmatch(other) is None:
            continue
        if tuple(map(int, other[1:].split("."))) >= candidate:
            raise ValueError("No se publicará como latest una versión anterior o igual")


def preflight(tag: str, root: Path, expected_commit: str | None = None) -> tuple[str, str]:
    version = source_version(tag, root)
    if command(["git", "status", "--porcelain", "--untracked-files=normal"], root):
        raise ValueError("La publicación exige un árbol limpio")
    origin = command(["git", "remote", "get-url", "origin"], root)
    if origin not in (f"https://github.com/{REPOSITORY}", f"https://github.com/{REPOSITORY}.git",
                      f"git@github.com:{REPOSITORY}.git"):
        raise ValueError("origin no corresponde al repositorio oficial")
    commit = command(["git", "rev-parse", "HEAD"], root)
    if expected_commit is not None and commit != expected_commit:
        raise ValueError("El checkout no corresponde al commit que se compiló")
    if command(["git", "rev-parse", f"{tag}^{{commit}}"], root) != commit:
        raise ValueError("La etiqueta local no corresponde al checkout")
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"],
                   cwd=root, check=True)
    remote = command(["git", "ls-remote", "--tags", "origin",
                      f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], root)
    if remote_tag_commit(remote, tag) != commit:
        raise ValueError("La etiqueta remota cambió; no se publicará esta compilación")
    pages = json.loads(command([
        "gh", "api", f"repos/{REPOSITORY}/releases?per_page=100", "--paginate", "--slurp",
    ], root))
    check_releases(tag, [release for page in pages for release in page])
    return version, commit


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def verify_artifacts(root: Path, version: str, mode: str) -> list[Path]:
    if mode not in ("zip", "installer"):
        raise ValueError("El modo debe ser zip o installer")
    names = [f"WhatsApp-CAN-{version}.zip"]
    if mode == "installer":
        names.append(f"WhatsApp-CAN-{version}-Setup.exe")
    assets = []
    for name in names:
        binary = root / "release" / name
        checksum = binary.with_name(name + ".sha256")
        expected = checksum.read_text(encoding="utf-8-sig").strip()
        if expected != f"{sha256(binary)}  {name}":
            raise ValueError(f"El checksum o nombre no coincide: {name}")
        assets.extend((binary, checksum))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.validate_release import validate_release

    validate_release(assets[0], assets[1])
    return assets


def publish(tag: str, root: Path, mode: str, expected_commit: str) -> str:
    version, _ = preflight(tag, root, expected_commit)
    assets = verify_artifacts(root, version, mode)
    from tools.ci.notify_telegram import report_progress

    report_progress("draft")
    command([
        "gh", "release", "create", tag, *(str(path) for path in assets),
        "--repo", REPOSITORY, "--verify-tag", "--draft", "--latest=false",
        "--title", f"WhatsApp CAN {version}", "--notes-file", str(root / "release_notes.txt"),
    ], root)
    # Keep an incomplete release as a draft; never delete or rewrite the pushed tag.
    with tempfile.TemporaryDirectory(prefix="can-release-verification-") as folder:
        report_progress("asset_verification")
        command(["gh", "release", "download", tag, "--repo", REPOSITORY,
                 "--dir", folder], root)
        for asset in assets:
            if sha256(Path(folder) / asset.name) != sha256(asset):
                raise ValueError(f"El asset descargado no coincide: {asset.name}")
    report_progress("publishing")
    command(["gh", "release", "edit", tag, "--repo", REPOSITORY,
             "--draft=false", "--latest"], root)
    result = json.loads(command(["gh", "release", "view", tag, "--repo", REPOSITORY,
                                "--json", "isDraft,isPrerelease,url"], root))
    if result["isDraft"] or result["isPrerelease"]:
        raise ValueError("La release no quedó publicada como estable")
    return result["url"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--mode", choices=("zip", "installer"), default="zip")
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "preflight":
        version, commit = preflight(args.tag, ROOT)
        output = f"version={version}\ncommit={commit}\n"
    else:
        if not args.commit or not re.fullmatch(r"[0-9a-f]{40}", args.commit):
            parser.error("publish requiere --commit con el SHA que pasó la compilación")
        url = publish(args.tag, ROOT, args.mode, args.commit)
        output = f"url={url}\n"
    if args.output:
        with args.output.open("a", encoding="utf-8") as destination:
            destination.write(output)
    print(output.strip())


if __name__ == "__main__":
    main()
