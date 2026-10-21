"""Check advisory capacity and sizing, and optionally real boxes and own box disks."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import box_disk, capacity, gib  # noqa: E402

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("verify-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

GIB = 1024**3
CLI = [sys.executable, str(ROOT / "raft.py")]
# Fits the development image (about 5 GiB of files) with room to fill. The CI
# raft-disks pool holds this box, its snapshot and one larger copy at once.
DISK_GIB = 8


def policy_checks():
    # These are allocation fixtures, not mocked host services. Expected counts
    # describe the documented policy independently of its arithmetic.
    pool = {"totalBytes": 60 * GIB, "freeBytes": 30 * GIB}
    host = {
        "cpus": 8,
        "memoryTotalBytes": 16 * GIB,
        "memoryAvailableBytes": 14 * GIB,
        "hostDiskFreeBytes": 40 * GIB,
        "sharedPool": pool,
        "boxDiskPool": pool,
    }

    def running(cpu, memory):
        config = {"limits.cpu": str(cpu), "limits.memory": memory}
        return {"name": "rf-fixture", "status": "Running", "expanded_config": config}

    stopped = {"status": "Stopped"}
    report = capacity(host, [running(2, "4GiB"), stopped])
    assert report["notice"].startswith("Advisory estimate only")
    assert "enforced" not in json.dumps(report).lower()
    assert report["boxes"] == 2 and report["activeBoxes"] == 1
    assert report["allocated"] == {"cpus": 2, "memoryBytes": 4 * GIB}
    assert report["headroom"] == {"cpus": 5, "memoryBytes": 10 * GIB}
    # Shapes double from the default at 2 GiB per CPU while they fit an empty host.
    shapes = [
        (r["cpu"], r["memoryGiB"], r["fitEmptyHost"], r["fitNow"]) for r in report["estimates"]
    ]
    assert shapes == [(1, 2, 7, 5), (2, 4, 3, 2), (4, 8, 1, 1)], shapes
    # Any whole size counts, including sizes larger than the old 2 CPU / 8 GiB menu.
    odd = capacity(host, [running(3, "12GiB")])
    assert odd["allocated"] == {"cpus": 3, "memoryBytes": 12 * GIB}
    assert odd["headroom"] == {"cpus": 4, "memoryBytes": 2 * GIB}
    over = capacity(host, [running(8, "16GiB")])
    assert over["warnings"] and all(r["fitNow"] == 0 for r in over["estimates"])
    try:
        capacity(host, [running(1, "512MiB")])
    except ValueError as error:
        assert "rf-fixture" in str(error)
    else:
        raise AssertionError("Sizing Raft never writes was counted")
    # Saved boxes never reduce the estimates: there is no box count limit.
    many = capacity(host, [stopped] * 12)
    assert many["boxes"] == 12
    assert many["estimates"] == capacity(host, [])["estimates"]
    frozen = capacity(host, [{**running(2, "4GiB"), "status": "Frozen"}])
    assert frozen["allocated"] == report["allocated"]
    low = capacity({**host, "memoryAvailableBytes": GIB, "hostDiskFreeBytes": GIB}, [])
    assert all(r["fitNow"] == 0 for r in low["estimates"])
    assert len(low["warnings"]) == 2
    legacy = capacity({**host, "boxDiskPool": None}, [])
    assert any("redeploy" in warning for warning in legacy["warnings"])
    for tiny in [{**host, "cpus": 1}, {**host, "memoryTotalBytes": GIB}]:
        estimates = capacity(tiny, [])["estimates"]
        assert [(r["cpu"], r["fitEmptyHost"]) for r in estimates] == [(1, 0)], estimates
    large = capacity({**host, "cpus": 64, "memoryTotalBytes": 128 * GIB}, [])
    assert large["reserve"]["memoryBytes"] == 128 * GIB // 10
    assert [r["cpu"] for r in large["estimates"]] == [1, 2, 4, 8, 16, 32]
    assert large["estimates"][0]["fitEmptyHost"] == 57
    for cpu, memory, expected in [(2, 8, 1), (4, 8, 3), (4, 16, 3), (8, 32, 7)]:
        example = capacity({**host, "cpus": cpu, "memoryTotalBytes": memory * GIB}, [])
        assert example["estimates"][0]["fitEmptyHost"] == expected
    shared = {"expanded_devices": {"root": {"type": "disk", "path": "/", "pool": "raft-data"}}}
    own = {"expanded_devices": {"root": {"pool": "raft-disks", "size": "20GiB"}}}
    assert (box_disk(shared), box_disk(own)) == ("shared", "20GiB")
    print(
        "Capacity advice: arbitrary sizes, no box count, example shapes, stopped/frozen boxes, "
        "low RAM/disk, missing own-disk pool and disk reporting passed",
        flush=True,
    )


def command(*args):
    return subprocess.check_output([*CLI, *args], text=True).strip()


def report(location):
    return json.loads(command("capacity", "--location", location, "--json"))


def listed(box):
    location = box.partition(":")[0]
    for line in command("list", "--location", location).splitlines():
        record = json.loads(line)
        if record["box"] == box:
            return record
    raise AssertionError(f"{box} is missing from raft list")


def live_checks(location):
    baseline = report(location)
    box = command("new", "--location", location, "--cpu", "1", "--memory", "1GiB", "--ttl", "600")
    try:
        active = report(location)
        assert active["boxes"] == baseline["boxes"] + 1
        assert active["activeBoxes"] == baseline["activeBoxes"] + 1
        assert active["allocated"]["cpus"] == baseline["allocated"]["cpus"] + 1
        assert active["allocated"]["memoryBytes"] == baseline["allocated"]["memoryBytes"] + GIB
        assert listed(box)["disk"] == "shared"
        text = command("capacity", "--location", location)
        assert text.startswith("Advisory estimate only") and "CPU  RAM GiB" in text
        assert "enforced" not in text.lower()
        # Limits beyond the whole host fail before Incus would quietly clip them.
        host = active["host"]
        memory = -(-host["memoryTotalBytes"] // GIB) + 1
        for arguments, message in [
            (["--cpu", str(host["cpus"] + 1)], "CPUs, but this host has"),
            (["--memory", f"{memory}GiB"], "RAM, but this host has"),
        ]:
            rejected = live.raft("new", "--location", location, *arguments, check=False)
            assert rejected.returncode == 1 and message in rejected.stderr, rejected
        command("stop", box)
        stopped = report(location)
        assert stopped["boxes"] == active["boxes"]
        assert stopped["activeBoxes"] == baseline["activeBoxes"]
        assert stopped["allocated"] == baseline["allocated"]
        rejected = live.raft("resume", box, "--ttl", "600", "--cpu", "100000", check=False)
        assert rejected.returncode == 1 and "this host has" in rejected.stderr, rejected
        command("resume", box, "--ttl", "600")
        resumed = report(location)
        assert resumed["activeBoxes"] == active["activeBoxes"]
        assert resumed["allocated"] == active["allocated"]
    finally:
        command("destroy", box)
    final = report(location)
    assert final["boxes"] == baseline["boxes"]
    assert final["activeBoxes"] == baseline["activeBoxes"]
    assert final["allocated"] == baseline["allocated"]
    print(f"{location}: real capacity reporting and oversized-request refusal passed", flush=True)


def disk_size(box):
    """Return the box filesystem's total and available bytes as seen inside the guest."""
    output = live.execute(box, "df", "-B1", "--output=size,avail", "/")
    total, available = output.splitlines()[-1].split()
    return int(total), int(available)


def fits_disk(total, size):
    # ext4 keeps a little of each volume for its own metadata.
    return (gib(size) - 1) * GIB < total <= gib(size) * GIB


def disk_checks(location):
    """Prove an own disk is a hard cap, also for Docker, and survives copies and backups."""
    size = f"{DISK_GIB}GiB"
    fixtures = []
    before = report(location)["host"]
    assert before["boxDiskPool"] is not None, "Own-disk checks need a raft-disks pool"
    saved = len(command("list", "--location", location).splitlines())
    try:
        # A disk too small for the image fails at creation and leaves nothing behind.
        rejected = live.raft("new", "--location", location, "--disk", "1GiB", check=False)
        if rejected.returncode == 0:
            fixtures.append(rejected.stdout.strip())
        assert rejected.returncode == 1, rejected
        assert len(command("list", "--location", location).splitlines()) == saved
        assert report(location)["host"]["boxDiskPool"] == before["boxDiskPool"]

        box = command(
            "new", "--location", location, "--disk", size, "--ttl", "1800", "--purpose", "Own disk"
        )
        fixtures.append(box)
        live.wait_running(box)
        assert listed(box)["disk"] == size
        root = json.loads(command("info", box))["expanded_devices"]["root"]
        assert (root["pool"], root["size"]) == ("raft-disks", size), root
        total, _ = disk_size(box)
        assert fits_disk(total, size), total
        usage = json.loads(command("usage", box))
        assert usage["filesystemScope"] == f"own {size} box disk", usage
        allocated = report(location)["host"]
        # Thick volumes take their whole size at creation.
        reserved = before["boxDiskPool"]["freeBytes"] - allocated["boxDiskPool"]["freeBytes"]
        assert reserved == DISK_GIB * GIB, reserved

        # Fill the disk from the guest: the write stops at the cap, and neither the
        # own-disk pool nor the shared pool gives up more space.
        filled = live.raft(
            "exec", box, "--", "dd", "if=/dev/zero", "of=/workspace/fill", "bs=1M", check=False
        )
        assert filled.returncode != 0 and "No space left" in filled.stderr, filled
        written = int(live.execute(box, "stat", "-c", "%s", "/workspace/fill"))
        # Guest root is not host root, so ext4's reserved blocks stay out of reach.
        assert written > GIB and disk_size(box)[1] < 64 * 1024**2, (written, disk_size(box))
        full = report(location)["host"]
        assert full["boxDiskPool"] == allocated["boxDiskPool"]
        assert full["sharedPool"]["freeBytes"] > allocated["sharedPool"]["freeBytes"] - written // 2
        live.execute(box, "rm", "/workspace/fill")

        # Docker layers live inside the same filesystem, so a container hits the same cap.
        docker = live.raft(
            "exec",
            box,
            "--",
            "docker",
            "run",
            "--rm",
            "alpine:3.22",
            "dd",
            "if=/dev/zero",
            "of=/fill",
            "bs=1M",
            check=False,
        )
        assert docker.returncode != 0, docker
        assert "No space left" in docker.stdout + docker.stderr, docker
        assert disk_size(box)[0] == total
        assert report(location)["host"]["boxDiskPool"] == allocated["boxDiskPool"]
        print(f"{location}: {size} own disk capped guest and Docker writes", flush=True)

        command("stop", box)
        command("snapshot", box, "sized")
        # Forks and templates keep the source disk; --disk on a template sets a new size.
        for arguments, expected in [
            (["fork", box, "--ttl", "600"], size),
            (["new", "--from", box + "/sized", "--ttl", "600"], size),
            (
                ["new", "--from", box + "/sized", "--disk", f"{DISK_GIB + 2}GiB"],
                f"{DISK_GIB + 2}GiB",
            ),
        ]:
            copy = command(*arguments)
            fixtures.append(copy)
            assert listed(copy)["disk"] == expected, (arguments, listed(copy))
            live.wait_running(copy)
            assert fits_disk(disk_size(copy)[0], expected), disk_size(copy)
            command("destroy", copy)
            fixtures.remove(copy)

        with tempfile.TemporaryDirectory(prefix="raft-disk-") as work:
            archive = Path(work) / "own-disk.tar.gz"
            command("backup", box, str(archive))
            command("destroy", box)
            fixtures.remove(box)
            recovered = command("recover", str(archive), "--location", location)
        fixtures.append(recovered)
        assert listed(recovered)["disk"] == size
        assert "sized" in command("snapshots", recovered)
        command("resume", recovered, "--ttl", "600")
        assert disk_size(recovered)[0] == total
        print(
            f"{location}: own disk kept by fork, template, snapshot and backup/recover", flush=True
        )
    finally:
        for fixture in reversed(fixtures):
            command("destroy", fixture)
    assert report(location)["host"]["boxDiskPool"] == before["boxDiskPool"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", help="Also test a real host with its own fixtures")
    args = parser.parse_args()
    policy_checks()
    if args.location:
        live_checks(args.location)
        disk_checks(args.location)
