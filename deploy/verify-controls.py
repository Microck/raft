"""Verify portable recovery between real hosts using a Debian fixture or full image."""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from raft import ADMISSION, address, incus, inventory, parse_handle, transaction  # noqa: E402
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "verify_live", Path(__file__).with_name("verify-live.py")
)
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def wait_address(box):
    # Incus Running precedes DHCP readiness. Use the canonical managed-NIC
    # lookup, not hostname -I, which can include guest Docker bridge addresses.
    location, name = parse_handle(box)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            return address(location, name)
        except ValueError:
            time.sleep(0.2)
    raise RuntimeError("Resumed workspace has no managed IPv4 address after 30 seconds")


def verify_admission(location, directory):
    fixtures = []
    saved = len(inventory(location))
    if saved >= 4:
        raise RuntimeError("Admission test needs at least one free slot on its target")
    try:
        for _ in range(4 - saved):
            box = live.raft(
                "new", "--location", location, "--memory", "1GiB", "--ttl", "600"
            ).stdout.strip()
            fixtures.append(box)
            live.wait_running(box)
            live.raft("stop", box)
        rejected = live.raft("new", "--location", location, check=False)
        if rejected.returncode == 0:
            fixtures.append(rejected.stdout.strip())
        assert rejected.returncode != 0 and "already holds four boxes" in rejected.stderr
        rejected = live.raft("fork", fixtures[0], "--ttl", "600", check=False)
        if rejected.returncode == 0:
            fixtures.append(rejected.stdout.strip())
        assert rejected.returncode != 0 and "already holds four boxes" in rejected.stderr
        with tempfile.TemporaryDirectory(dir=directory, prefix="raft-admission-") as work:
            # Admission must reject before native archive parsing, without creating a box.
            archive = Path(work) / "rejected.tar.gz"
            archive.write_bytes(b"not-an-incus-archive")
            rejected = live.raft("recover", str(archive), "--location", location, check=False)
            if rejected.returncode == 0:
                fixtures.append(rejected.stdout.strip())
            assert rejected.returncode != 0 and "already holds four boxes" in rejected.stderr
        assert len(inventory(location)) == 4
        report = json.loads(live.raft("limits", "--location", location, "--json").stdout)
        assert report["savedBoxes"] == 4 and report["savedSlots"] == 0
        assert all(size["newBoxes"] == 0 for size in report["recommendations"])
        print(
            f"{location}: stopped boxes count toward admission; new/fork/recover reject a fifth box",
            flush=True,
        )
    finally:
        for box in reversed(fixtures):
            live.raft("destroy", box)


def verify(source, target, directory, development_image=False):
    fixtures = []
    name = "rf-" + uuid.uuid4().hex[:16]
    box = source + ":" + name
    try:
        if development_image:
            box = live.raft("new", "--location", source, "--ttl", "1800").stdout.strip()
            fixtures.append(box)
            live.wait_running(box)
        else:
            transaction(
                source,
                ADMISSION + 'incus --project raft launch images:debian/13 "$1" '
                "-c user.raft=true -c limits.cpu=1 -c limits.memory=1GiB; "
                'incus --project raft config set "$1" user.raft.expires="$(( $(date +%s) + 600 ))"',
                name,
            )
            fixtures.append(box)
            incus(source, "exec", name, "--", "mkdir", "-p", "/workspace")
            live.execute(
                box,
                "bash",
                "-ec",
                "apt-get update -qq; apt-get install -y --no-install-recommends python3",
            )
        inventories = None
        if development_image:
            inventories = live.execute(
                box, "sha256sum", "/opt/raft/packages.tsv", "/opt/raft/npm-packages.json"
            )
        print(f"{source}: fixture ready", flush=True)
        live.execute(
            box,
            "bash",
            "-ec",
            "printf raft-backup-proof > /workspace/proof; chmod 640 /workspace/proof",
        )
        job = live.raft(
            "exec",
            box,
            "--detach",
            "--",
            "bash",
            "-ec",
            "sleep 600 & echo $! > /workspace/child-pid; wait",
        ).stdout.strip()
        deadline = time.monotonic() + 10
        while live.raft(
            "exec", box, "--", "test", "-f", "/workspace/child-pid", check=False
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("Detached job did not start")
            time.sleep(0.2)
        child = live.execute(box, "cat", "/workspace/child-pid")
        assert "ActiveState=active" in live.raft("status", box, job).stdout
        live.raft("extend", box, "--ttl", "1200")
        extended = json.loads(live.raft("info", box).stdout)
        assert (
            int(extended["config"]["user.raft.expires"])
            > int(live.execute(box, "date", "+%s")) + 1100
        )
        assert "ActiveState=active" in live.raft("status", box, job).stdout
        # Exercise journal retention without changing any host service or other box.
        live.execute(box, "journalctl", "--rotate", "--vacuum-size=1")
        assert "ActiveState=active" in live.raft("status", box, job).stdout
        live.raft("cancel", box, job)
        assert "ActiveState=inactive" in live.raft("status", box, job).stdout
        assert live.raft("logs", box, job).returncode == 0
        assert (
            live.raft("exec", box, "--", "test", "-e", "/proc/" + child, check=False).returncode
            != 0
        )
        for command in ["status", "logs", "cancel"]:
            missing = live.raft(command, box, "rfcmd-" + "0" * 32, check=False)
            assert missing.returncode == 1 and "Detached job not found" in missing.stderr
        usage = json.loads(live.raft("usage", box).stdout)
        assert 0 < usage["memoryBytes"] < int(usage["memoryLimit"])
        assert usage["filesystemScope"] == "shared Raft pool, not per-box usage"
        with tempfile.TemporaryDirectory(dir=directory, prefix="raft-controls-") as work:
            archive = Path(work) / "workspace with spaces.tar.gz"
            assert live.raft("backup", box, str(archive), check=False).returncode != 0
            assert not archive.exists()
            live.raft("stop", box)
            uploaded = Path(work) / "stopped upload.bin"
            downloaded = Path(work) / "stopped download.bin"
            uploaded.write_bytes(bytes(range(256)))
            live.raft("upload", box, str(uploaded), "/workspace/stopped file.bin")
            live.raft("download", box, "/workspace/stopped file.bin", str(downloaded))
            assert downloaded.read_bytes() == uploaded.read_bytes()
            assert live.raft("extend", box, "--ttl", "600", check=False).returncode != 0
            live.raft("snapshot", box, "checkpoint")
            print(f"{source}: exporting stopped workspace and snapshot", flush=True)
            live.raft("backup", box, str(archive))
            size = archive.stat().st_size
            assert size > 0 and archive.stat().st_mode & 0o077 == 0
            print(f"{source}: archive completed ({size} bytes)", flush=True)
            assert live.raft("backup", box, str(archive), check=False).returncode != 0
            assert archive.stat().st_size == size
            restored = live.raft("recover", str(archive), "--location", target).stdout.strip()
            fixtures.append(restored)
            print(f"{target}: recovered stopped workspace", flush=True)
            metadata = json.loads(live.raft("info", restored).stdout)
            assert metadata["status"] == "Stopped"
            assert not metadata["config"].get("user.raft.expires")
            assert "checkpoint" in live.raft("snapshots", restored).stdout
            live.raft("resume", restored, "--ttl", "600")
            if development_image:
                live.wait_running(restored)
                assert (
                    live.execute(
                        restored,
                        "sha256sum",
                        "/opt/raft/packages.tsv",
                        "/opt/raft/npm-packages.json",
                    )
                    == inventories
                )
                import subprocess

                subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("verify-tools.py")), restored],
                    check=True,
                )
            recovered_config = json.loads(live.raft("info", restored).stdout)["config"]
            source_config = json.loads(live.raft("info", box).stdout)["config"]
            assert recovered_config["volatile.eth0.hwaddr"] != source_config["volatile.eth0.hwaddr"]
            assert live.execute(restored, "cat", "/workspace/proof") == "raft-backup-proof"
            assert live.execute(restored, "stat", "-c", "%a", "/workspace/proof") == "640"
            live.execute(restored, "bash", "-ec", "printf changed > /workspace/proof")
            live.raft("stop", restored)
            live.raft("restore", restored, "checkpoint")
            live.raft("resume", restored, "--ttl", "600")
            assert live.execute(restored, "cat", "/workspace/proof") == "raft-backup-proof"
            restored_config = json.loads(live.raft("info", restored).stdout)["config"]
            assert restored_config["volatile.eth0.hwaddr"] != source_config["volatile.eth0.hwaddr"]
            if source == target:
                live.raft("resume", box, "--ttl", "600")
                original_ip = wait_address(box)
                recovered_ip = wait_address(restored)
                assert original_ip != recovered_ip
                assert live.execute(box, "cat", "/workspace/proof") == "raft-backup-proof"
            print(
                f"{source} -> {target}: cancellation, usage, portable recovery and snapshots passed ({size} bytes)",
                flush=True,
            )
    finally:
        for fixture in reversed(fixtures):
            live.raft("destroy", fixture)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--directory", default=None, help="Controller directory with room for the archive"
    )
    parser.add_argument(
        "--development-image",
        action="store_true",
        help="Export the complete configured development image rather than a small fixture",
    )
    args = parser.parse_args()
    verify_admission(args.target, args.directory)
    verify(args.source, args.target, args.directory, args.development_image)
