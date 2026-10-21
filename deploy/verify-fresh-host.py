"""Onboard and reboot a disposable KVM Ubuntu host through `raft host add`.

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


def verify_box_disks(env):
    """Own box disks are off after onboarding and a later redeploy can enable them."""
    raft = [sys.executable, str(ROOT / "raft.py")]
    deploy = [sys.executable, str(ROOT / "deploy/deploy.py"), "--location", "lab"]
    host = ["ssh", "raft-fresh"]
    doctor = subprocess.check_output([*raft, "doctor"], env=env, text=True)
    assert "own box disks: not enabled (redeploy with --disk-pool SIZE)" in doctor, doctor
    report = json.loads(subprocess.check_output([*raft, "capacity", "--json"], env=env))
    assert report["host"]["boxDiskPool"] is None, report
    assert not any("raft-disks" in warning for warning in report["warnings"]), report
    # --disk fails before anything is created and names the command that enables it.
    boxes = subprocess.check_output([*raft, "list"], env=env, text=True)
    rejected = subprocess.run(
        [*raft, "new", "--disk", "10GiB"], env=env, capture_output=True, text=True
    )
    assert rejected.returncode == 1, rejected
    assert "--location lab --disk-pool SIZE" in rejected.stderr, rejected.stderr
    assert subprocess.check_output([*raft, "list"], env=env, text=True) == boxes
    # 24 GiB holds the 10 GiB cached image volume and one 10 GiB own disk.
    run(*deploy, "--disk-pool", "24GiB", env=env)
    pool = subprocess.check_output(
        [
            *host,
            "sudo incus storage get raft-disks size; sudo incus storage get raft-disks "
            "lvm.use_thinpool; sudo lvs --noheadings -o discards raft-disks/IncusThinPool",
        ],
        env=env,
        text=True,
    ).split()
    assert pool == ["24GiB", "true", "nopassdown"], pool
    allocated = subprocess.check_output(
        [*host, "sudo stat -c '%b %B %s' /var/lib/incus/disks/raft-disks.img"],
        env=env,
        text=True,
    ).split()
    blocks, block_size, length = map(int, allocated)
    assert blocks * block_size >= length, "Own-disk pool file is still sparse"
    box = subprocess.check_output(
        [*raft, "new", "--disk", "10GiB", "--ttl", "600"], env=env, text=True
    ).strip()
    try:
        for _ in range(30):
            if subprocess.run([*raft, "exec", box, "--", "true"], env=env).returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Own-disk box did not become command-ready")
    finally:
        run(*raft, "destroy", box, env=env)
    assert "own box disks: not enabled" not in subprocess.check_output(
        [*raft, "doctor"], env=env, text=True
    )
    # The pool keeps its size; an explicit different size is refused.
    resized = subprocess.run(
        [*deploy, "--disk-pool", "25GiB"], env=env, capture_output=True, text=True
    )
    assert resized.returncode != 0 and "does not resize" in resized.stderr, resized
    run(*deploy, env=env)
    print("Own box disks: off by default, enabled by redeploy, thin and fully allocated")


def verify_firewall(env):
    """Disrupt only this harness's disposable VM to prove the startup dependency."""
    ssh = ["ssh", "raft-fresh"]
    requires = subprocess.check_output(
        [*ssh, "systemctl show incus.service -p Requires -p After"], env=env, text=True
    )
    assert all(
        "raft-network.service" in line.split("=", 1)[1].split() for line in requires.splitlines()
    )
    started = subprocess.check_output(
        [*ssh, "systemctl show incus.service -p ExecMainStartTimestampMonotonic --value"],
        env=env,
        text=True,
    )
    # Deliberately inject an obsolete allowance with no boxes present, then redeploy.
    run(
        *ssh,
        "sudo iptables -N RAFT-VERIFY-UNRELATED && "
        "sudo iptables -A RAFT-VERIFY-UNRELATED -j RETURN && "
        "sudo iptables -I RAFT-FORWARD 1 -d 169.254.0.0/16 -j ACCEPT",
        env=env,
    )
    run(sys.executable, str(ROOT / "deploy/deploy.py"), "--location", "lab", env=env)
    run(
        *ssh,
        "sudo iptables -C RAFT-FORWARD -d 169.254.0.0/16 -j REJECT && "
        "! sudo iptables -C RAFT-FORWARD -d 169.254.0.0/16 -j ACCEPT && "
        "sudo iptables -C RAFT-VERIFY-UNRELATED -j RETURN && "
        "sudo iptables -F RAFT-VERIFY-UNRELATED && sudo iptables -X RAFT-VERIFY-UNRELATED",
        env=env,
    )
    unchanged = subprocess.check_output(
        [*ssh, "systemctl show incus.service -p ExecMainStartTimestampMonotonic --value"],
        env=env,
        text=True,
    )
    assert started == unchanged, "Firewall reload restarted the Incus daemon"
    run(*ssh, "sudo cp /usr/local/lib/raft/network /tmp/raft-network-proof", env=env)
    try:
        run(
            *ssh,
            "sudo systemctl stop raft-expire.timer incus-startup.service incus.socket incus.service raft-network.service",
            env=env,
        )
        run(
            *ssh,
            "printf '#!/bin/sh\\nexit 1\\n' | sudo tee /usr/local/lib/raft/network >/dev/null",
            env=env,
        )
        failed = subprocess.run(
            [*ssh, "sudo systemctl start incus.service"], env=env, capture_output=True, text=True
        )
        assert failed.returncode != 0, "Incus started despite a failed firewall dependency"
        active = subprocess.run(
            [*ssh, "systemctl is-active incus.service"], env=env, capture_output=True, text=True
        )
        assert active.stdout.strip() != "active"
        run(*ssh, "sudo systemctl start incus.socket", env=env)
        failed = subprocess.run(
            [*ssh, "sudo timeout 20 incus query /1.0"], env=env, capture_output=True, text=True
        )
        assert failed.returncode != 0, "Socket activation bypassed the firewall dependency"
        active = subprocess.run(
            [*ssh, "systemctl is-active incus.service"], env=env, capture_output=True, text=True
        )
        assert active.stdout.strip() != "active"
    finally:
        run(
            *ssh,
            "sudo cp /tmp/raft-network-proof /usr/local/lib/raft/network && sudo rm /tmp/raft-network-proof && "
            "sudo systemctl reset-failed incus.service raft-network.service && "
            "sudo systemctl reload-or-restart raft-network.service && "
            "sudo systemctl start incus.socket incus.service incus-startup.service raft-expire.timer",
            env=env,
        )
    print(
        "Repeat deploy removed stale rules without restarting Incus; firewall failure blocked normal and socket startup",
        flush=True,
    )


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
        for key in ["client", "host"]:
            run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(work / key))
        (ssh / "config").write_text(
            f"Host raft-fresh\n  HostName localhost\n  Port 2222\n  User raftci\n"
            f"  IdentityFile {work / 'client'}\n  IdentitiesOnly yes\n"
            f"  UserKnownHostsFile {ssh / 'known_hosts'}\n  StrictHostKeyChecking yes\n"
        )
        (ssh / "known_hosts").write_text("[localhost]:2222 " + (work / "host.pub").read_text())
        # Onboarding starts from a controller with no Raft configuration at all.
        configuration = config / "incus.json"
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
            "ssh_keys": {
                "ed25519_private": (work / "host").read_text(),
                "ed25519_public": (work / "host.pub").read_text(),
            },
            "packages": ["curl", "gnupg", "btrfs-progs"],
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
                run("ssh", "raft-fresh", "sudo cloud-init status --wait --long", env=env)
                onboard = [sys.executable, str(ROOT / "raft.py"), "host", "add", "lab"]
                onboard += ["--ssh", "raft-fresh"]
                preview = subprocess.run(
                    [*onboard, "--dry-run"], env=env, capture_output=True, text=True, check=True
                )
                assert "Dry run: nothing was changed" in preview.stdout, preview.stdout
                assert not config.exists(), "Dry run wrote controller configuration"
                deployed = subprocess.run(
                    ["ssh", "raft-fresh", "command -v incus"], env=env, capture_output=True
                )
                assert deployed.returncode != 0, "Dry run installed Incus"
                # Without a terminal or --yes, onboarding must refuse before changing anything.
                refused = subprocess.run(
                    onboard, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
                )
                assert refused.returncode == 1 and "--yes" in refused.stderr, refused
                run(*onboard, "--image-archive", str(archive), "--yes", env=env)
                with archive.open("rb") as image_archive:
                    digest = hashlib.file_digest(image_archive, "sha256").hexdigest()
                saved = json.loads(configuration.read_text())
                assert saved == {"lab": {"ssh": "raft-fresh", "image": digest}}, saved
                assert configuration.stat().st_mode & 0o777 == 0o600
                leftovers = subprocess.check_output(
                    ["ssh", "raft-fresh", "ls /tmp | grep -c '^raft-image' || true"],
                    env=env,
                    text=True,
                )
                assert leftovers.strip() == "0", "Image staging file left on the host"
                repeated = subprocess.run(
                    [*onboard, "--yes"], env=env, capture_output=True, text=True
                )
                assert repeated.returncode == 1 and "already configured" in repeated.stderr
                print("Guided onboarding: dry run, confirmation, import and smoke test passed")
                verify_firewall(env)
                run(
                    sys.executable,
                    str(ROOT / "deploy/verify-live.py"),
                    "--location",
                    "lab",
                    "--reboot-host",
                    env=env,
                )
                verify_box_disks(env)
                # Oversize only this disposable pool after all boxes have been removed.
                run("ssh", "raft-fresh", "sudo incus storage set raft-data size=61GiB", env=env)
                drift = subprocess.run(
                    [sys.executable, str(ROOT / "deploy/deploy.py"), "--location", "lab"],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert drift.returncode != 0 and "size=60GiB" in drift.stderr
                size = subprocess.check_output(
                    ["ssh", "raft-fresh", "sudo incus storage get raft-data size"],
                    env=env,
                    text=True,
                )
                assert size.strip() == "61GiB", "Deployment silently resized a drifting pool"
                print(
                    "Fresh provisioning, firewall failure gating, redeployment, opt-in own box "
                    "disks, pool size drift rejection and real host reboot passed",
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
