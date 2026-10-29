"""Check advisory capacity and sizing, and optionally real boxes and own box disks."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import BOX_DISK_POOL, box_disk, capacity, gib, settings  # noqa: E402

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("verify-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)

GIB = 1024**3
CLI = [sys.executable, str(ROOT / "raft.py")]
# Fits the development image (about 5 GiB of files) with room to fill. It matches
# the 10 GiB volume Incus caches the image in, so creation is a pure thin snapshot.
DISK_GIB = 10
IMAGE_VOLUME_GIB = 10


def policy_checks():
    # These are allocation fixtures, not mocked host services. Expected counts
    # describe the documented policy independently of its arithmetic.
    own = {
        "totalBytes": 40 * GIB,
        "reservedBytes": 20 * GIB,
        "usedBytes": 6 * GIB,
        "metadataUsedPercent": 3.0,
    }
    host = {
        "cpus": 8,
        "memoryTotalBytes": 16 * GIB,
        "memoryAvailableBytes": 14 * GIB,
        "hostDiskFreeBytes": 40 * GIB,
        "sharedPool": {"totalBytes": 60 * GIB, "freeBytes": 30 * GIB},
        "boxDiskPool": own,
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
    # Own box disks are opt-in: a host without their pool is healthy.
    assert capacity({**host, "boxDiskPool": None}, [])["warnings"] == []
    # An inactive thin pool reports no usage; that is not a warning either.
    idle = {**own, "usedBytes": None, "metadataUsedPercent": None}
    assert capacity({**host, "boxDiskPool": idle}, [])["warnings"] == []
    full = capacity({**host, "boxDiskPool": {**own, "metadataUsedPercent": 85.0}}, [])
    assert len(full["warnings"]) == 1 and "metadata" in full["warnings"][0], full["warnings"]
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
        "low RAM/disk, optional own-disk pool, thin metadata and disk reporting passed",
        flush=True,
    )


def command(*args):
    # The private controller HOME keeps this controller's defaults for new out.
    return subprocess.check_output([*CLI, *args], text=True, env=live.CONTROLLER_ENV).strip()


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


def host(location, script):
    return subprocess.check_output(
        ["ssh", "-T", "-o", "BatchMode=yes", settings(location)["ssh"], script], text=True
    )


def own_pool(location):
    return report(location)["host"]["boxDiskPool"]


def thin_origin(box):
    """Return the logical volume a box's own disk was snapshotted from, or "" for none.

    Incus names a box's volume containers_<project>_<name> with each "-" doubled and
    a snapshot's volume <box volume>-<snapshot>; the volume group is named after the
    pool. These are Incus internals, read here only to prove copies are snapshots.
    """
    location, _, name = box.partition(":")
    output = host(location, f"sudo lvs --reportformat json -o lv_name,origin {BOX_DISK_POOL}")
    volumes = json.loads(output)["report"][0]["lv"]
    return next(v["origin"] for v in volumes if v["lv_name"] == volume_name(name))


def volume_name(name, snapshot=None):
    return f"containers_raft_{name.replace('-', '--')}" + (f"-{snapshot}" if snapshot else "")


def disk_checks(location):
    """Prove own disks are hard caps, that copies are thin snapshots, and that the
    pool is never promised more than it holds."""
    size = f"{DISK_GIB}GiB"
    fixtures = []
    before = own_pool(location)
    assert before is not None, "Own-disk checks need a raft-disks pool"
    image = settings(location)["image"]
    cached = host(
        location,
        f"sudo incus storage volume show {BOX_DISK_POOL} image/{image} >/dev/null 2>&1 "
        "&& echo yes || echo no",
    ).strip()
    saved = len(command("list", "--location", location).splitlines())
    try:
        box = command(
            "new", "--location", location, "--disk", size, "--ttl", "1800", "--purpose", "Own disk"
        )
        fixtures.append(box)
        live.wait_running(box)
        assert listed(box)["disk"] == size
        root = json.loads(command("info", box))["expanded_devices"]["root"]
        assert (root["pool"], root["size"]) == (BOX_DISK_POOL, size), root
        total, _ = disk_size(box)
        assert fits_disk(total, size), total
        usage = json.loads(command("usage", box))
        assert usage["filesystemScope"] == f"own {size} box disk", usage
        # The box reserves its full size; the first own-disk box also caches the image.
        created_host = report(location)["host"]
        created = created_host["boxDiskPool"]
        image_gib = 0 if cached == "yes" else IMAGE_VOLUME_GIB
        added = created["reservedBytes"] - before["reservedBytes"]
        assert added == (DISK_GIB + image_gib) * GIB, added
        assert thin_origin(box) == f"images_{image}", thin_origin(box)

        # Refused requests leave no box and no reservation behind: a disk too small
        # for the image fails in Incus, and a disk larger than the unreserved space
        # fails the reservation check before Incus runs.
        unreserved = created["totalBytes"] - created["reservedBytes"]
        for disk, message in [
            ("1GiB", ""),
            (f"{unreserved // GIB + 1}GiB", "GiB unreserved; nothing was created"),
        ]:
            rejected = live.raft("new", "--location", location, "--disk", disk, check=False)
            if rejected.returncode == 0:
                fixtures.append(rejected.stdout.strip())
            assert rejected.returncode == 1 and message in rejected.stderr, rejected
            assert len(command("list", "--location", location).splitlines()) == saved + 1
            assert own_pool(location)["reservedBytes"] == created["reservedBytes"]

        # Fill the disk from the guest: the write stops at the cap, and neither the
        # own-disk pool's reservations nor the shared pool give up more space.
        filled = live.raft(
            "exec", box, "--", "dd", "if=/dev/zero", "of=/workspace/fill", "bs=1M", check=False
        )
        assert filled.returncode != 0 and "No space left" in filled.stderr, filled
        written = int(live.execute(box, "stat", "-c", "%s", "/workspace/fill"))
        # Guest root is not host root, so ext4's reserved blocks stay out of reach.
        assert written > GIB and disk_size(box)[1] < 64 * 1024**2, (written, disk_size(box))
        full = report(location)["host"]
        assert full["boxDiskPool"]["reservedBytes"] == created["reservedBytes"]
        assert full["boxDiskPool"]["usedBytes"] <= full["boxDiskPool"]["totalBytes"]
        shared = created_host["sharedPool"]["freeBytes"]
        assert full["sharedPool"]["freeBytes"] > shared - written // 2
        live.execute(box, "rm", "/workspace/fill")

        # Docker layers live inside the same filesystem, so a container hits the same cap.
        # Docker keeps its own state on that filesystem too: once it is full the daemon
        # can drop the client connection while dd still runs, so the container runs
        # detached and the cap is proven from outside. The guest filesystem fills up
        # and never grows past its size.
        live.execute(box, "docker", "pull", "-q", "alpine:3.22")
        live.execute(
            box,
            "docker",
            "run",
            "--detach",
            "--log-driver",
            "none",
            "--name",
            "raft-fill",
            "alpine:3.22",
            "dd",
            "if=/dev/zero",
            "of=/fill",
            "bs=1M",
        )
        deadline = time.monotonic() + 300
        while disk_size(box)[1] >= 64 * 1024**2:
            assert time.monotonic() < deadline, disk_size(box)
            time.sleep(2)
        assert disk_size(box)[0] == total, disk_size(box)
        # Removing the container frees the space; restart Docker in case it lost state.
        removed = live.raft("exec", box, "--", "docker", "rm", "-f", "raft-fill", check=False)
        restarted = live.raft("exec", box, "--", "systemctl", "restart", "docker", check=False)
        if restarted.returncode:
            print(
                "Docker removal after disk exhaustion:", removed.stdout, removed.stderr, flush=True
            )
            journal = live.raft(
                "exec",
                box,
                "--",
                "journalctl",
                "--no-pager",
                "-n",
                "100",
                "-u",
                "docker",
                check=False,
            )
            print(journal.stdout, journal.stderr, flush=True)
            raise RuntimeError("Docker restart after disk exhaustion failed: " + restarted.stderr)
        live.raft("exec", box, "--", "docker", "rm", "-f", "raft-fill", check=False)
        assert disk_size(box)[1] > GIB, disk_size(box)
        assert own_pool(location)["reservedBytes"] == created["reservedBytes"]
        print(f"{location}: {size} own disk capped guest and Docker writes", flush=True)

        command("stop", box)
        command("snapshot", box, "sized")
        snapshotted = own_pool(location)["reservedBytes"]
        assert snapshotted - created["reservedBytes"] == DISK_GIB * GIB
        name = box.partition(":")[2]
        # Forks and templates keep the source disk; --disk on a template sets a new
        # size. Each copy reserves its full size but is a thin snapshot of its source.
        for arguments, expected, origin in [
            (["fork", box, "--ttl", "600"], size, volume_name(name)),
            (
                ["new", "--from", box + "/sized", "--ttl", "600"],
                size,
                volume_name(name, "sized"),
            ),
            (
                ["new", "--from", box + "/sized", "--disk", f"{DISK_GIB + 2}GiB"],
                f"{DISK_GIB + 2}GiB",
                volume_name(name, "sized"),
            ),
        ]:
            copy = command(*arguments)
            fixtures.append(copy)
            assert listed(copy)["disk"] == expected, (arguments, listed(copy))
            assert thin_origin(copy) == origin, (arguments, thin_origin(copy))
            reserved = own_pool(location)["reservedBytes"] - snapshotted
            assert reserved == gib(expected) * GIB, (arguments, reserved)
            live.wait_running(copy)
            assert fits_disk(disk_size(copy)[0], expected), disk_size(copy)
            command("destroy", copy)
            fixtures.remove(copy)

        # A template copy of a running source writes almost nothing to the pool,
        # where a file copy would write the image's 5 GiB again. The running source
        # keeps the thin pool active, so LVM reports its usage on both sides.
        command("resume", box, "--ttl", "600")
        live.wait_running(box)
        running = own_pool(location)["usedBytes"]
        copy = command("new", "--from", box + "/sized", "--ttl", "600")
        fixtures.append(copy)
        written = own_pool(location)["usedBytes"] - running
        assert written < GIB, written
        command("destroy", copy)
        fixtures.remove(copy)
        command("stop", box)
        print(
            f"{location}: forks and templates of own disks are thin snapshots "
            f"({written / 1024**2:.0f} MiB written by a template copy)",
            flush=True,
        )

        with tempfile.TemporaryDirectory(prefix="raft-disk-") as work:
            archive = Path(work) / "own-disk.tar.gz"
            command("backup", box, str(archive))
            command("destroy", box)
            fixtures.remove(box)
            recovered = command("recover", str(archive), "--location", location)
        fixtures.append(recovered)
        assert listed(recovered)["disk"] == size
        assert "sized" in command("snapshots", recovered)
        # The recovered box and its snapshot each reserve the full size again.
        reserved = own_pool(location)["reservedBytes"] - before["reservedBytes"]
        assert reserved == (2 * DISK_GIB + image_gib) * GIB, reserved
        command("resume", recovered, "--ttl", "600")
        assert disk_size(recovered)[0] == total
        print(
            f"{location}: own disk kept by fork, template, snapshot and backup/recover", flush=True
        )
    finally:
        for fixture in reversed(fixtures):
            command("destroy", fixture)
    # Only the cached image stays reserved after every own-disk box is gone.
    final = own_pool(location)["reservedBytes"] - before["reservedBytes"]
    assert final == image_gib * GIB, final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", help="Also test a real host with its own fixtures")
    args = parser.parse_args()
    policy_checks()
    if args.location:
        live_checks(args.location)
        disk_checks(args.location)
