"""Inspect a clean unified Incus image and write its public release manifest."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tomllib


def credential_path(path):
    """Reject credential locations even when a tar entry is a link or special file."""
    return (
        ".ssh" in path.parts
        or (path.parent.name == "ssh" and path.name.startswith("ssh_host_"))
        or path.name in {".netrc", "incus.json"}
        or (path.name == ".npmrc" and path.parts[:2] in {("rootfs", "root"), ("rootfs", "home")})
    )


def verify_voxl_source(archive, version):
    """Check the source bundle's required rebuild inputs before publication."""
    root = f"voxl-server-{version}"
    required = {
        "README.txt",
        "install-voxl.sh",
        "build-environment.txt",
        "native-packages.txt",
        "pkgconfig/libyuv.pc",
        f"{root}/Cargo.toml",
        f"{root}/Cargo.lock",
        f"{root}/LICENSE",
        f"{root}/.cargo/config.toml",
    }
    files = set()
    native_packages = ""
    native_sources = {}
    lock = {"package": []}
    try:
        with tarfile.open(archive, "r|gz") as source:
            for member in source:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe Voxl source member: " + member.name)
                if member.isfile() and member.size:
                    name = str(path)
                    files.add(name)
                    if name == "native-packages.txt":
                        native_packages = source.extractfile(member).read().decode()
                    elif name.startswith("native/") and name.endswith(".dsc"):
                        native_sources[name] = source.extractfile(member).read().decode()
                    elif name == f"{root}/Cargo.lock":
                        lock = tomllib.loads(source.extractfile(member).read().decode())
        for package in native_packages.splitlines():
            name, package_version = package.split("=", 1)
            dsc = f"native/{name}_{package_version.split(':')[-1]}.dsc"
            required.add(dsc)
            source_files = re.findall(
                r"(?m)^ [0-9a-f]{64}\s+\d+\s+(\S+)$", native_sources.get(dsc, "")
            )
            if not source_files:
                raise ValueError("Missing Debian source archive checksums: " + dsc)
            required.update("native/" + name for name in source_files)
        for package in lock["package"]:
            if package.get("source"):
                vendor = f"vendor/{package['name']}-{package['version']}"
                required.update({f"{vendor}/Cargo.toml", f"{vendor}/.cargo-checksum.json"})
    except (tarfile.TarError, OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid Voxl source bundle: {error}") from error
    if (
        not required <= files
        or not any(name.startswith(f"{root}/src/") and name.endswith(".rs") for name in files)
        or not any(name.startswith("vendor/") and name.endswith("/Cargo.toml") for name in files)
        or not any(name.startswith("native/") and ".tar." in name for name in files)
    ):
        raise ValueError("Incomplete Voxl source bundle: missing rebuild inputs")


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
        "rootfs/opt/raft/voxl-source.tar.gz": f"voxl-source-{architecture}.tar.gz",
        "rootfs/opt/raft/voxl-GPL-2": f"voxl-GPL-2-{architecture}.txt",
        "rootfs/opt/raft/voxl-LICENSE": f"voxl-LICENSE-{architecture}.txt",
        "rootfs/opt/raft/voxl-NOTICE": f"voxl-NOTICE-{architecture}.txt",
        "rootfs/opt/raft/voxl.tsv": f"voxl-{architecture}.tsv",
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
                found.add(name)
                if name.endswith("machine-id") and image.extractfile(member).read().strip():
                    raise ValueError("Template has a populated machine ID")
                if wanted[name]:
                    if not member.size:
                        raise ValueError("Empty template resource: " + name)
                    with (
                        image.extractfile(member) as source,
                        (archive.parent / wanted[name]).open("wb") as destination,
                    ):
                        shutil.copyfileobj(source, destination)
    if found != set(wanted):
        raise ValueError("Missing template resources: " + str(set(wanted) - found))
    version = (archive.parent / f"voxl-{architecture}.tsv").read_text().split("\t")[1]
    verify_voxl_source(archive.parent / f"voxl-source-{architecture}.tar.gz", version)
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
        "licensing": "Raft is MIT; the x264-linked Voxl executable is GPL-2.0. Its notices and corresponding source accompany the image and release. Other bundled packages retain their licenses in /usr/share/doc and upstream distributions.",
        "voxlSource": f"voxl-source-{architecture}.tar.gz",
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
