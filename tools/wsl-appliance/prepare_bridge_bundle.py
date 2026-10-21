"""Prepare a digest-verified initial bridge bundle from a locally built OCI archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

REPOSITORY = "ghcr.io/marcomolinaleija/cliente-xmpp-bridge"


def bundle_identity(archive: Path, image: str) -> dict:
    if not re.fullmatch(re.escape(REPOSITORY) + r":v[1-9][0-9]*", image):
        raise ValueError("The bundle must use an approved bridge repository and version tag")
    with tarfile.open(archive, "r:*") as source:
        members = {}
        for member in source:
            name = member.name.removeprefix("./")
            if name in members:
                raise ValueError("Duplicate OCI archive member")
            members[name] = member

        def document(name: str, expected_digest: str = "") -> dict:
            member = members.get(name)
            if member is None or not member.isfile() or member.size > 8 * 1024 * 1024:
                raise ValueError("Missing or invalid OCI metadata")
            data = source.extractfile(member).read()
            if expected_digest and "sha256:" + hashlib.sha256(data).hexdigest() != expected_digest:
                raise ValueError("OCI metadata digest mismatch")
            return json.loads(data)

        def blob(digest: str) -> dict:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("Invalid OCI digest")
            return document("blobs/sha256/" + digest[7:], digest)

        index = document("index.json")
        descriptors = index.get("manifests", [])
        if index.get("schemaVersion") != 2 or len(descriptors) != 1:
            raise ValueError("One native bridge image is required")
        descriptor = descriptors[0]
        if descriptor.get("annotations", {}).get("org.opencontainers.image.ref.name") != image:
            raise ValueError("The archive tag does not match the requested bridge")
        manifest = blob(descriptor["digest"])
        config = blob(manifest["config"]["digest"])
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("The appliance requires a Linux amd64 image")
        return {"digest": descriptor["digest"], "image_id": manifest["config"]["digest"]}


def prepare_bundle(archive: Path, image: str, directory: Path, *, voice: bool = False) -> dict:
    identity = bundle_identity(archive, image)
    name = "slidge-" + image.rsplit(":", 1)[1] + ".oci"
    names = (name, name + ".sha256", name.removesuffix(".oci") + ".image-id", "bundled-image.json")
    if any((directory / item).exists() for item in names):
        raise ValueError("The bundle output already exists")
    directory.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target = directory / name
    shutil.copyfile(archive, target)
    with target.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("The copied OCI archive failed verification")
    (directory / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="ascii")
    (directory / names[2]).write_text(identity["image_id"] + "\n", encoding="ascii")
    manifest = {
        "schema_version": 1,
        "image": image,
        **identity,
        "archive": name,
        "archive_sha256": digest,
        "voice": voice,
    }
    (directory / "bundled-image.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--voice", action="store_true")
    args = parser.parse_args()
    manifest = prepare_bundle(args.archive, args.image, args.output_directory, voice=args.voice)
    print(f"Prepared {manifest['image']}@{manifest['digest']}; no registry publication")


if __name__ == "__main__":
    main()
