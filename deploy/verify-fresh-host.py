"""Provision and reboot a disposable KVM Ubuntu host using the canonical deployer.

CI-only harness. Writes temporary verified SSH/configuration in its own HOME.
Uses a sparse 100 GiB virtual disk, not an 80 GiB physical capacity guarantee.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "OpenAI File Downloader, XaiImageApiFetch/1.0"
BASE = "https://cloud-images.ubuntu.com/noble/current/"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def verify(archive):
    # Fail rather than silently switching to software emulation.
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("Fresh-host verification requires accessible KVM")
    with tempfile.TemporaryDirectory(prefix="raft-fresh-") as directory:
        work = Path(directory)
        home = work / "controller"
        ssh = home / ".ssh"
        config = home / ".config/raft"
        ssh.mkdir(parents=True, mode=0o700)
        config.mkdir(parents=True, mode=0o700)
        for key in ["client", "host"]:
            run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(work / key))
        (ssh / "config").write_text(
            f"Host raft-fresh\n  HostName localhost\n  Port 2222\n  User raftci\n"
            f"  IdentityFile {work / 'client'}\n  IdentitiesOnly yes\n"
            f"  UserKnownHostsFile {ssh / 'known_hosts'}\n  StrictHostKeyChecking yes\n"
        )
        (ssh / "known_hosts").write_text("[localhost]:2222 " + (work / "host.pub").read_text())
        configuration = config / "incus.json"
        configuration.write_text(json.dumps({"lab": {"ssh": "raft-fresh", "image": ""}}))
        configuration.chmod(0o600)
        # A per-harness HOME keeps the operator's controller config untouched.
        env = {**os.environ, "HOME": str(home)}
        cloud = {
            "hostname": "raft-fresh",
            "users": [
                {
                    "name": "raftci",
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "shell": "/bin/bash",
                    "ssh_authorized_keys": [(work / "client.pub").read_text().strip()],
                }
            ],
            "ssh_pwauth": False,
            "ssh_deletekeys": False,
            "ssh_genkeytypes": [],
            "packages": ["curl", "gnupg", "btrfs-progs"],
            "write_files": [
                {
                    "path": "/etc/ssh/ssh_host_ed25519_key",
                    "permissions": "0600",
                    "content": (work / "host").read_text(),
                },
                {
                    "path": "/etc/ssh/ssh_host_ed25519_key.pub",
                    "permissions": "0644",
                    "content": (work / "host.pub").read_text(),
                },
            ],
        }
        (work / "user-data").write_text("#cloud-config\n" + json.dumps(cloud))
        (work / "meta-data").write_text("instance-id: raft-fresh\nlocal-hostname: raft-fresh\n")
        image = work / "noble-server-cloudimg-amd64.img"
        request = urllib.request.Request(BASE + "SHA256SUMS", headers={"User-Agent": USER_AGENT})
        sums = urllib.request.urlopen(request, timeout=60).read().decode()
        checksum = next(
            line.split()[0]
            for line in sums.splitlines()
            if line.split()[-1].lstrip("*") == image.name
        )
        run("curl", "-fsSL", "-A", USER_AGENT, BASE + image.name, "-o", str(image))
        with image.open("rb") as downloaded:
            assert hashlib.file_digest(downloaded, "sha256").hexdigest() == checksum
        run("qemu-img", "resize", str(image), "100G")
        run(
            "cloud-localds",
            str(work / "seed.img"),
            str(work / "user-data"),
            str(work / "meta-data"),
        )
        with (work / "serial.log").open("wb") as serial:
            vm = subprocess.Popen(
                [
                    "qemu-system-x86_64",
                    "-enable-kvm",
                    "-cpu",
                    "host",
                    "-smp",
                    "2",
                    "-m",
                    "8192",
                    "-display",
                    "none",
                    "-serial",
                    "stdio",
                    "-monitor",
                    "none",
                    "-drive",
                    f"file={image},format=qcow2,if=virtio",
                    "-drive",
                    f"file={work / 'seed.img'},format=raw,if=virtio",
                    "-netdev",
                    "user,id=net,hostfwd=tcp:127.0.0.1:2222-:22",
                    "-device",
                    "virtio-net-pci,netdev=net",
                ],
                stdout=serial,
                stderr=subprocess.STDOUT,
            )
            try:
                # SSH expands ~ using passwd, not HOME. Pass the fixture config explicitly
                # through a PATH wrapper shared by CLI SSH and SCP calls.
                binaries = work / "bin"
                binaries.mkdir()
                for tool in ["ssh", "scp"]:
                    (binaries / tool).write_text(
                        f'#!/bin/sh\nexec /usr/bin/{tool} -F "{ssh / "config"}" "$@"\n'
                    )
                    (binaries / tool).chmod(0o755)
                env["PATH"] = str(binaries) + os.pathsep + env["PATH"]
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    if vm.poll() is not None:
                        raise RuntimeError("Fresh VM exited during boot")
                    probe = subprocess.run(
                        [
                            "ssh",
                            "-T",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=2",
                            "raft-fresh",
                            "true",
                        ],
                        env=env,
                        capture_output=True,
                    )
                    if probe.returncode == 0:
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError("Fresh VM SSH did not become ready")
                run("ssh", "raft-fresh", "sudo cloud-init status --wait", env=env)
                run(sys.executable, str(ROOT / "deploy/deploy.py"), "--location", "lab", env=env)
                # A second deploy must preserve owned infrastructure and succeed.
                run(sys.executable, str(ROOT / "deploy/deploy.py"), "--location", "lab", env=env)
                run("scp", str(archive), "raft-fresh:/tmp/raft-image.tar.gz", env=env)
                run(
                    "ssh",
                    "raft-fresh",
                    "sudo incus --project raft image import /tmp/raft-image.tar.gz --alias raft-dev",
                    env=env,
                )
                images = subprocess.check_output(
                    [
                        "ssh",
                        "raft-fresh",
                        "sudo incus --project raft image list raft-dev --format json",
                    ],
                    env=env,
                    text=True,
                )
                fingerprint = next(
                    item["fingerprint"]
                    for item in json.loads(images)
                    if any(a["name"] == "raft-dev" for a in item["aliases"])
                )
                configuration.write_text(
                    json.dumps({"lab": {"ssh": "raft-fresh", "image": fingerprint}})
                )
                run("ssh", "raft-fresh", "rm /tmp/raft-image.tar.gz", env=env)
                run(
                    sys.executable,
                    str(ROOT / "deploy/verify-live.py"),
                    "--location",
                    "lab",
                    "--reboot-host",
                    env=env,
                )
                print(
                    "Fresh Ubuntu provisioning, repeat deployment, image import and real host reboot passed",
                    flush=True,
                )
            finally:
                vm.terminate()
                vm.wait(timeout=30)
                # Report diagnostics only on failure; never publish fixture keys.
                if sys.exc_info()[0]:
                    print(
                        (work / "serial.log").read_text(errors="replace")[-10000:], file=sys.stderr
                    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    verify(args.archive.resolve())
