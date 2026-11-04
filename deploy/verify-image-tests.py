"""Exercise the image inspector with clean, licensing, and credential-bearing archives."""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def source_fixture(omitted):
    root = "voxl-server-0.3.2"
    files = {
        "README.txt": b"Fixture build instructions\n",
        "install-voxl.sh": b"#!/bin/bash\n",
        "build-environment.txt": b"arm64\nfixture compiler\n",
        "native-packages.txt": b"x264=1.0\nlibyuv=1.0\nopus=1.0\npulseaudio=1.0\nlibxcb=1.0\n",
        "pkgconfig/libyuv.pc": b"Name: fixture\n",
        f"{root}/Cargo.toml": b'[package]\nname = "voxl-server"\nversion = "0.3.2"\n',
        f"{root}/Cargo.lock": (
            b'[[package]]\nname = "fixture-dependency"\nversion = "1.0"\n'
            b'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        ),
        f"{root}/LICENSE": b"MIT fixture\n",
        f"{root}/src/main.rs": b"fn main() {}\n",
        f"{root}/.cargo/config.toml": b'[source.vendored-sources]\ndirectory = "../vendor"\n',
        "vendor/fixture-dependency-1.0/Cargo.toml": b'[package]\nname = "fixture-dependency"\n',
        "vendor/fixture-dependency-1.0/.cargo-checksum.json": b'{"files":{}}\n',
    }
    for package in ["x264", "libyuv", "opus", "pulseaudio", "libxcb"]:
        filename = f"{package}_1.0.tar.xz"
        files[f"native/{package}_1.0.dsc"] = (
            f"Checksums-Sha256:\n {'0' * 64} 7 {filename}\n".encode()
        )
        files[f"native/{filename}"] = b"fixture"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            if name == omitted:
                continue
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def verify():
    with tempfile.TemporaryDirectory(prefix="raft-image-check-") as directory:
        work = Path(directory)
        key = work / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        home = PurePosixPath("rootfs", "home", "developer")
        root = PurePosixPath("rootfs", "root")
        cases = [
            ("clean", None, tarfile.REGTYPE, "", True),
            ("missing-voxl-source", None, tarfile.REGTYPE, "", False),
            ("missing-voxl-gpl", None, tarfile.REGTYPE, "", False),
            ("empty-voxl-gpl", None, tarfile.REGTYPE, "", False),
            ("corrupt-voxl-source", None, tarfile.REGTYPE, "", False),
            ("missing-vendored-dependency", None, tarfile.REGTYPE, "", False),
            ("missing-native-source", None, tarfile.REGTYPE, "", False),
            ("user-key", home / ".ssh/id_ed25519", tarfile.REGTYPE, "", False),
            ("root-key", root / ".ssh/id_ed25519", tarfile.REGTYPE, "", False),
            ("user-key-link", home / ".ssh/id_ed25519", tarfile.SYMTYPE, "/etc/passwd", False),
            ("ssh-directory-link", home / ".ssh", tarfile.SYMTYPE, "/etc", False),
            (
                "user-key-hardlink",
                home / ".ssh/id_ed25519",
                tarfile.LNKTYPE,
                "metadata.yaml",
                False,
            ),
            ("user-key-fifo", home / ".ssh/id_ed25519", tarfile.FIFOTYPE, "", False),
            ("key-target-link", home / "alias", tarfile.SYMTYPE, ".ssh/id_ed25519", False),
            (
                "key-target-hardlink",
                home / "alias",
                tarfile.LNKTYPE,
                str(home / ".ssh/id_ed25519"),
                False,
            ),
            (
                "host-key-link",
                PurePosixPath("rootfs/etc/ssh/ssh_host_ed25519_key"),
                tarfile.SYMTYPE,
                "/etc/passwd",
                False,
            ),
            ("npm-token", home / ".npmrc", tarfile.REGTYPE, "", False),
            ("empty-ssh-directory", home / ".ssh", tarfile.DIRTYPE, "", True),
            (
                "package-config",
                PurePosixPath("rootfs/usr/lib/node_modules/npm/.npmrc"),
                tarfile.REGTYPE,
                "",
                True,
            ),
            (
                "package-token",
                PurePosixPath("rootfs/usr/lib/node_modules/npm/npmrc"),
                tarfile.REGTYPE,
                "",
                False,
            ),
            (
                "normal-link",
                PurePosixPath("rootfs/usr/bin/example"),
                tarfile.SYMTYPE,
                "../lib/example",
                True,
            ),
        ]
        for label, path, kind, target, accepted in cases:
            case = work / label
            case.mkdir()
            archive = case / "image.tar.gz"
            resources = {
                "metadata.yaml": b"architecture: aarch64\n",
                "rootfs/etc/machine-id": b"",
                "rootfs/opt/raft/packages.tsv": b"example\t1\n",
                "rootfs/opt/raft/npm-packages.json": b"{}\n",
                "rootfs/opt/raft/voxl-source.tar.gz": source_fixture(
                    {
                        "missing-vendored-dependency": "vendor/fixture-dependency-1.0/Cargo.toml",
                        "missing-native-source": "native/x264_1.0.tar.xz",
                    }.get(label)
                ),
                "rootfs/opt/raft/voxl-GPL-2": b"GPL-2.0 fixture\n",
                "rootfs/opt/raft/voxl-LICENSE": b"MIT fixture\n",
                "rootfs/opt/raft/voxl-NOTICE": b"x264-linked executable: GPL-2.0\n",
                "rootfs/opt/raft/voxl.tsv": b"voxl-server\t0.3.2\tfixture\n",
            }
            if label == "missing-voxl-source":
                del resources["rootfs/opt/raft/voxl-source.tar.gz"]
            if label == "missing-voxl-gpl":
                del resources["rootfs/opt/raft/voxl-GPL-2"]
            if label == "empty-voxl-gpl":
                resources["rootfs/opt/raft/voxl-GPL-2"] = b""
            if label == "corrupt-voxl-source":
                resources["rootfs/opt/raft/voxl-source.tar.gz"] = b"not a gzip archive"
            with tarfile.open(archive, "w:gz") as output:
                for name, content in resources.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    output.addfile(member, io.BytesIO(content))
                if path:
                    member = tarfile.TarInfo(str(path))
                    member.type = kind
                    member.linkname = target
                    content = (
                        key.read_bytes()
                        if "key" in label
                        else b"registry=https://registry.npmjs.org/\n"
                    )
                    if label in {"npm-token", "package-token"}:
                        content = b"//registry.npmjs.org/:_authToken=fixture-token\n"
                    if kind == tarfile.REGTYPE:
                        member.size = len(content)
                    output.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
            fingerprint = hashlib.sha256(archive.read_bytes()).hexdigest()
            response = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "deploy/verify-image.py"),
                    str(archive),
                    "--architecture",
                    "arm64",
                    "--fingerprint",
                    fingerprint,
                ],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "GITHUB_SHA": "0" * 40,
                    "GITHUB_REPOSITORY": "Microck/raft",
                    "GITHUB_RUN_ID": "0",
                },
            )
            assert (response.returncode == 0) == accepted, (label, response.stderr)
            if accepted:
                manifest = json.loads((case / "manifest-arm64.json").read_text())
                assert manifest["sha256"] == fingerprint
                assert (case / "packages-arm64.tsv").read_bytes() == resources[
                    "rootfs/opt/raft/packages.tsv"
                ]
            elif label in {"missing-voxl-source", "missing-voxl-gpl"}:
                assert "Missing template resources" in response.stderr, (label, response.stderr)
                assert not (case / "manifest-arm64.json").exists()
            elif label == "empty-voxl-gpl":
                assert "Empty template resource" in response.stderr, (label, response.stderr)
                assert not (case / "manifest-arm64.json").exists()
            elif label in {
                "corrupt-voxl-source",
                "missing-vendored-dependency",
                "missing-native-source",
            }:
                assert "Voxl source bundle" in response.stderr, (label, response.stderr)
                assert not (case / "manifest-arm64.json").exists()
            else:
                assert "credential" in response.stderr.lower(), (label, response.stderr)
                assert not (case / "manifest-arm64.json").exists()
        print(f"Image inspector: {len(cases)} clean/licensing/credential archive cases passed")


if __name__ == "__main__":
    verify()
