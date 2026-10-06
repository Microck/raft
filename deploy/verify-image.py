"""Inspect a clean unified Incus image and write its public release manifest."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile


def credential_path(path):
    """Reject credential locations even when a tar entry is a link or special file."""
    return (
        ".ssh" in path.parts
        or (path.parent.name == "ssh" and path.name.startswith("ssh_host_"))
        or path.name in {".netrc", "incus.json"}
        or (path.name == ".npmrc" and path.parts[:2] in {("rootfs", "root"), ("rootfs", "home")})
    )


def verify(archive, architecture, fingerprint):
    if archive.stat().st_size >= 2 * 1024**3:
        raise ValueError("Image exceeds GitHub's per-release-asset limit")
    with archive.open("rb") as exported:
        digest = hashlib.file_digest(exported, "sha256").hexdigest()
    if digest != fingerprint:
        raise ValueError("Export SHA256 does not match its immutable Incus fingerprint")
    wanted = {
        "rootfs/opt/raft/packages.tsv": f"packages-{architecture}.tsv",
        "rootfs/opt/raft/npm-packages.json": f"npm-packages-{architecture}.json",
        "metadata.yaml": None,
        "rootfs/etc/machine-id": None,
    }
    found = set()
    with tarfile.open(archive, "r|gz") as image:
        for member in image:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe image member: " + member.name)
            name = str(path)
            if not member.isdir() and (
                credential_path(path)
                or path.parts[:2] == ("rootfs", "workspace")
                or (
                    (member.issym() or member.islnk())
                    and credential_path(PurePosixPath(member.linkname))
                )
            ):
                raise ValueError("Operator content or credential entry in template: " + name)
            if not member.isfile():
                continue
            # npm itself ships a package-local .npmrc. Check all npm configs
            # for credentials rather than treating every package config as a secret.
            if path.name in {".npmrc", "npmrc"}:
                content = image.extractfile(member).read().decode(errors="replace")
                if re.search(
                    r"(?mi)^[^#;\n]*(?:_authToken|_auth|_password|password)\s*=\s*\S+", content
                ):
                    raise ValueError("Credential setting in npm config: " + name)
            if name in wanted:
                content = image.extractfile(member).read()
                found.add(name)
                if name.endswith("machine-id") and content.strip():
                    raise ValueError("Template has a populated machine ID")
                if wanted[name]:
                    (archive.parent / wanted[name]).write_bytes(content)
    if found != set(wanted):
        raise ValueError("Missing template resources: " + str(set(wanted) - found))
    manifest = {
        "architecture": architecture,
        "filename": archive.name,
        "sizeBytes": archive.stat().st_size,
        "sha256": digest,
        "incusFingerprint": fingerprint,
        "sourceCommit": os.environ["GITHUB_SHA"],
        "buildRun": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "base": "Debian 13 default system container",
        "isolation": "Unprivileged system container; shares the host kernel",
        "checks": ["extended lifecycle", "31 developer tools", "same-host full-image recovery"],
        "licensing": "Raft is MIT; bundled third-party packages retain their licenses in /usr/share/doc and upstream distributions",
    }
    (archive.parent / f"manifest-{architecture}.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print("Clean image verified: " + archive.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--architecture", choices=["arm64", "amd64"], required=True)
    parser.add_argument("--fingerprint", required=True)
    args = parser.parse_args()
    verify(args.archive, args.architecture, args.fingerprint)
