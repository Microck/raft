"""Exercise the real image inspector with clean and credential-bearing tar archives."""

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


def verify():
    with tempfile.TemporaryDirectory(prefix="raft-image-check-") as directory:
        work = Path(directory)
        key = work / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        home = PurePosixPath("rootfs", "home", "developer")
        root = PurePosixPath("rootfs", "root")
        cases = [
            ("clean", None, tarfile.REGTYPE, "", True),
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
            }
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
            else:
                assert "credential" in response.stderr.lower(), (label, response.stderr)
                assert not (case / "manifest-arm64.json").exists()
        print(f"Image inspector: {len(cases)} real clean/credential archive cases passed")


if __name__ == "__main__":
    verify()
