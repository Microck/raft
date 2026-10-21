#!/usr/bin/env python3
"""Self-hosted persistent Linux workspaces powered by Incus."""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import uuid

import raft_files

__version__ = "0.4.0"

CONFIG = Path.home() / ".config/raft/incus.json"
PROJECT = "raft"
LOCATION_NAME = r"[a-zA-Z0-9][a-zA-Z0-9_-]*"
GIB = 1024**3
# Boxes use the shared Btrfs pool unless `new --disk` gives them their own disk:
# a fixed-size logical volume in this LVM pool. Deployment creates both pools.
SHARED_POOL = "raft-data"
BOX_DISK_POOL = "raft-disks"
# Size of the own-disk pool on hosts deployed without --disk-pool. Its space is
# allocated on the host disk up front, so fresh hosts need it on top of 80 GiB.
DEFAULT_DISK_POOL = "40GiB"
# Box notes follow one template so any agent can tell why a box exists, who uses
# it and what happens to it next. Each value is free text; purpose is required.
BOX_NOTE_FIELDS = ["purpose", "owner", "next"]

USAGE = """import json, shutil
from pathlib import Path
group = Path('/sys/fs/cgroup')
disk = shutil.disk_usage('/workspace')
print(json.dumps({
    'memoryBytes': int((group / 'memory.current').read_text()),
    'memoryLimit': (group / 'memory.max').read_text().strip(),
    'cpuTime': dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines()),
    'effectiveCpus': (group / 'cpuset.cpus.effective').read_text().strip(),
    'filesystemUsedBytes': disk.used,
    'filesystemAvailableBytes': disk.free,
}))
"""


def configuration():
    inventory = json.loads(CONFIG.read_text())
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Configure at least one host in ~/.config/raft/incus.json")
    for location, entry in inventory.items():
        if not re.fullmatch(LOCATION_NAME, location):
            raise ValueError("Location names must use letters, digits, underscores or hyphens")
        if not isinstance(entry, dict) or not isinstance(entry.get("ssh"), str) or not entry["ssh"]:
            raise ValueError("Each configured location needs a nonempty ssh target")
        if not isinstance(entry.get("note", ""), str):
            raise ValueError("Location notes must be strings")
    return inventory


def settings(location):
    inventory = configuration()
    if location not in inventory:
        raise ValueError(f"Unknown location: {location}")
    return inventory[location]


def edit_configuration(change):
    """Apply change(inventory) to the controller configuration and save it."""
    # main() sets umask 077, so a new configuration directory is private.
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # Hold a controller lock across read, edit and replace so concurrent edits
    # cannot drop each other. Readers need no lock because replacement is atomic.
    with (CONFIG.parent / ".incus.json.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Onboarding can start on a controller that has no configuration yet.
        inventory = configuration() if CONFIG.exists() else {}
        change(inventory)
        if not inventory:
            # Removing the only location restores the unconfigured state.
            CONFIG.unlink(missing_ok=True)
            return
        # Key order is preserved because the first location is the default for new.
        # Replace atomically so an interrupted write cannot truncate the inventory;
        # NamedTemporaryFile creates the staged file with mode 0600.
        staged = tempfile.NamedTemporaryFile("w", dir=CONFIG.parent, prefix=".raft-", delete=False)
        try:
            with staged:
                json.dump(inventory, staged, indent=2)
                staged.write("\n")
            os.replace(staged.name, CONFIG)
        finally:
            # A no-op after a successful replace; removes the file after a failure.
            Path(staged.name).unlink(missing_ok=True)


def set_note(location, text):
    """Record operator guidance for a location. Raft shows notes but never enforces them."""
    if text is not None and not text.strip():
        raise ValueError("Use --clear to remove a note")

    def change(inventory):
        if location not in inventory:
            raise ValueError(f"Unknown location: {location}")
        if text is None:
            inventory[location].pop("note", None)
        else:
            inventory[location]["note"] = text.strip()

    edit_configuration(change)


def box_note(args):
    """Build a box note from --purpose, --owner and --next; None when none is given."""
    note = {}
    for field in BOX_NOTE_FIELDS:
        value = getattr(args, field, None)
        if value is not None:
            if not value.strip():
                raise ValueError(f"--{field} cannot be empty")
            note[field] = value.strip()
    if note and "purpose" not in note:
        raise ValueError("Box notes need --purpose")
    return note or None


def read_box_note(box):
    """Return a box's note as a dict, or None. Raft writes it as JSON in user.raft.note."""
    stored = box["config"].get("user.raft.note")
    return json.loads(stored) if stored else None


def cpu_count(value):
    """Parse --cpu: any positive whole number of CPUs."""
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("CPU count must be a positive whole number")
    return int(value)


def gib_size(value):
    """Parse --memory, --disk and --disk-pool. Whole GiB like 16GiB is the one accepted form."""
    if not re.fullmatch(r"[1-9][0-9]*GiB", value):
        raise argparse.ArgumentTypeError("Use a whole number of GiB, e.g. 16GiB")
    return value


def gib(size):
    return int(size.removesuffix("GiB"))


def box_disk(box):
    """Return a box's own disk size, e.g. 20GiB, or "shared" for the shared pool."""
    root = box["expanded_devices"].get("root", {})
    return root.get("size", "") if root.get("pool") == BOX_DISK_POOL else "shared"


def receive(command, destination, *, stdin=None):
    """Write streamed bytes in Python: SSH can exit zero after a local write error."""
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(
            command, stdin=stdin, stdout=subprocess.PIPE, stderr=errors
        ) as process:
            try:
                shutil.copyfileobj(process.stdout, destination)
            except BaseException:
                process.kill()
                process.wait()
                raise
            returncode = process.wait()
        errors.seek(0)
        return subprocess.CompletedProcess(command, returncode, None, errors.read())


def remote(location, argv, *, capture=True, stdin=None, stdout=None, tty=False, locked=False):
    command = ["sudo", "-n"]
    if locked:
        command += ["flock", "/run/lock/raft-incus.lock"]
    command += ["incus", "--project", PROJECT, *argv]
    connection = [
        "ssh",
        *(["-tt"] if tty else ["-T"]),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        settings(location)["ssh"],
        shlex.join(command),
    ]
    if stdout is not None:
        return receive(connection, stdout, stdin=stdin)
    return subprocess.run(
        connection,
        stdin=stdin,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )


def incus(location, *argv, locked=False):
    response = remote(location, argv, locked=locked)
    if response.returncode:
        raise RuntimeError(response.stderr.decode(errors="replace").strip())
    return response.stdout.decode()


# Read-only probe. It runs through the login shell of any SSH target, including
# macOS, so each fact degrades to an empty value instead of failing the script.
# Sizes are printed in KiB because mawk formats large products in exponent form.
HOST_FACTS = """echo "kernel=$(uname -s)"
if [ -r /etc/os-release ]; then . /etc/os-release; echo "os=${ID:-}"; echo "version=${VERSION_ID:-}"; fi
echo "arch=$(dpkg --print-architecture 2>/dev/null)"
echo "cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null)"
echo "memory=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null)"
directory=/var/lib/incus; [ -d "$directory" ] || directory=/var/lib
echo "disk=$(df -Pk "$directory" 2>/dev/null | awk 'NR == 2 {print $4}')"
if sudo -n true 2>/dev/null; then echo sudo=yes; else echo sudo=no; fi
if command -v python3 >/dev/null 2>&1; then echo python3=yes; else echo python3=no; fi
if sudo -n incus project list --format csv -c n 2>/dev/null | grep -qx raft; then echo deployed=yes; else echo deployed=no; fi
"""


def host_problems(facts, disk_pool=DEFAULT_DISK_POOL):
    """Return reasons a probed SSH target cannot become a Raft host; empty means supported."""
    kernel = facts.get("kernel") or "an unknown system"
    if kernel != "Linux":
        return [
            f"The target runs {kernel}, not Linux. Raft hosts need Ubuntu 22.04 or 24.04 on "
            "ARM64 or AMD64. On macOS, run an Ubuntu VM and onboard the VM's SSH target"
        ]
    problems = []
    release = f"{facts.get('os', '')} {facts.get('version', '')}".strip() or "unknown"
    if facts.get("os") != "ubuntu" or facts.get("version") not in {"22.04", "24.04"}:
        problems.append(f"Raft hosts need Ubuntu 22.04 or 24.04; found {release}")
    if facts.get("arch") not in {"arm64", "amd64"}:
        problems.append(f"Raft hosts need ARM64 or AMD64; found {facts.get('arch') or 'unknown'}")
    if facts.get("sudo") != "yes":
        problems.append("Configure passwordless sudo for the SSH user; `sudo -n true` must succeed")
    if facts.get("python3") != "yes":
        problems.append("Install python3 on the host")
    cpus = int(facts.get("cpus") or 0)
    if cpus < 2:
        problems.append(f"Raft hosts need at least 2 CPUs; found {cpus}")
    # MemTotal excludes firmware and kernel reservations: 8 GiB machines report
    # about 7.7 GiB. The threshold accepts them and rejects 6 GiB machines.
    memory = int(facts.get("memory") or 0) * 1024
    if memory < 7.5 * GIB:
        problems.append(f"Raft hosts need at least 8 GiB RAM; found {memory / GIB:.1f} GiB")
    # Only a fresh deployment creates the 60 GiB shared pool and the own-disk
    # pool; deploy.py enforces the same minimum.
    disk = int(facts.get("disk") or 0) * 1024
    needed = 80 + gib(disk_pool)
    if facts.get("deployed") != "yes" and disk < needed * GIB:
        problems.append(
            f"Fresh hosts need at least {needed} GiB free disk (80 GiB plus the {disk_pool} "
            f"own-disk pool); found {disk / GIB:.1f} GiB. A smaller --disk-pool needs less"
        )
    return problems


def deploy_module(filename):
    """Load a host deployment script shipped beside the CLI."""
    path = Path(__file__).resolve().parent / "deploy" / filename
    if not path.exists():
        raise ValueError(
            "Host onboarding needs the deployment files in the npm package or a source "
            "checkout; the Python wheel contains only the CLI"
        )
    spec = importlib.util.spec_from_file_location("raft_" + path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke_test(location):
    """Create a disposable box, wait until it runs a command, then delete it."""
    handle = new(
        argparse.Namespace(
            location=location,
            ttl=600,
            cpu=1,
            memory="2GiB",
            disk=None,
            disposable=True,
            template=None,
        )
    )
    print("Smoke test box: " + handle, flush=True)
    name = handle.partition(":")[2]
    try:
        # new returns once Incus starts the box, before every guest service is ready.
        for _ in range(30):
            if execute(location, name, ["true"], capture=True).returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("Smoke test box did not become command-ready: " + handle)
    finally:
        incus(location, "delete", name, "--force", locked=True)
    print("Smoke test create/exec/destroy passed", flush=True)


def host_add(args):
    """Check, provision and register a new host location, then prove it works."""
    if not re.fullmatch(LOCATION_NAME, args.location):
        raise ValueError("Location names must use letters, digits, underscores or hyphens")
    if CONFIG.exists() and args.location in configuration():
        raise ValueError(f"{args.location} is already configured; check it with raft doctor")
    archive = Path(args.image_archive).resolve() if args.image_archive else None
    if archive is not None and not archive.is_file():
        raise ValueError(f"Image archive not found: {archive}")
    deployer = deploy_module("deploy.py")
    probe = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            args.ssh,
            shlex.join(["sh", "-c", HOST_FACTS]),
        ],
        capture_output=True,
        text=True,
    )
    if probe.returncode:
        raise RuntimeError(
            f"Cannot reach {args.ssh} with key-based SSH: {probe.stderr.strip()}\n"
            f"Verify the host key interactively, then check `ssh -o BatchMode=yes {args.ssh} true`"
        )
    facts = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
    problems = host_problems(facts, args.disk_pool or DEFAULT_DISK_POOL)
    if problems:
        raise ValueError(
            f"{args.ssh} cannot host Raft boxes; nothing was changed:\n- " + "\n- ".join(problems)
        )
    architecture = facts["arch"]
    if args.build:
        image = f"build a native {architecture} image from Debian 13 on the host"
    elif archive is not None:
        image = f"upload and import {archive}"
    else:
        image = f"download raft-dev-{architecture}.tar.gz from release {args.image_release} and import it"
    disk_pool = args.disk_pool or DEFAULT_DISK_POOL
    print(
        f"Plan for {args.location} ({args.ssh}: Ubuntu {facts['version']} {architecture}, "
        f"{facts['cpus']} CPUs, {int(facts['memory']) * 1024 / GIB:.1f} GiB RAM)\n"
        "- install Incus 6.0 LTS from the Zabbly apt repository, btrfs-progs, lvm2 and nftables "
        "if missing\n"
        + (
            "- reuse and verify the existing raft project, raft-data pool and rfbr0 bridge\n"
            if facts["deployed"] == "yes"
            else "- create Incus project raft, 60 GiB Btrfs pool raft-data and bridge rfbr0 "
            "(10.232.0.1/24 with NAT)\n"
        )
        + f"- create the {disk_pool} thick LVM pool raft-disks for own box disks if it is "
        "missing, and allocate its file on the host disk in full\n"
        + "- install raft-network.service (rfbr0 isolation rules) and raft-expire.timer\n"
        f"- {image}; images are checked by SHA-256 and architecture\n"
        f"- add {args.location} with the image fingerprint to {CONFIG}\n"
        "- run doctor and a disposable create/exec/destroy smoke test\n"
        "SSH configuration, other firewall rules, IP addresses and account passwords are not changed.",
        flush=True,
    )
    if args.dry_run:
        print("Dry run: nothing was changed")
        return
    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError("Review the plan, then rerun with --yes to apply it")
        if input("Apply this plan? [y/N] ").strip().lower() not in {"y", "yes"}:
            raise ValueError("Onboarding cancelled; nothing was changed")
    deployer.deploy(args.location, args.ssh, args.disk_pool)
    if args.build:
        # A fresh alias avoids refusing to build beside an earlier raft-dev image.
        alias = "raft-dev-" + uuid.uuid4().hex[:8]
        fingerprint = deploy_module("build-image.py").build(args.ssh, alias)
    elif archive is not None:
        fingerprint = deployer.import_archive(args.ssh, archive, architecture)
    else:
        fingerprint = deployer.import_release(args.ssh, args.image_release, architecture)

    def register(inventory):
        if args.location in inventory:
            raise ValueError(f"{args.location} was configured while onboarding ran")
        inventory[args.location] = {"ssh": args.ssh, "image": fingerprint}

    edit_configuration(register)
    try:
        doctor(args.location)
        smoke_test(args.location)
    except BaseException:
        # Never leave a plausible-looking but unusable location behind.
        edit_configuration(lambda inventory: inventory.pop(args.location, None))
        print(
            f"Removed {args.location} from {CONFIG}. Host changes remain; a rerun reuses them.",
            file=sys.stderr,
        )
        raise
    print(f"{args.location} is ready: raft new --location {args.location}")


def parse_handle(value):
    location, separator, name = value.partition(":")
    if not separator or not re.fullmatch(r"rf-[a-f0-9]{16}", name):
        raise ValueError("Use the location-qualified handle returned by new")
    settings(location)
    return location, name


def instance(location, name):
    box = json.loads(incus(location, "list", name, "--fast", "--format", "json"))
    box = next((item for item in box if item["name"] == name), None)
    if box is None or box["config"].get("user.raft") != "true":
        raise ValueError("Raft box not found")
    return box


def inventory(location):
    return [
        box
        for box in json.loads(incus(location, "list", "--fast", "--format", "json"))
        if box["config"].get("user.raft") == "true"
    ]


REQUIRE_STOPPED = """state=$(incus --project raft list "$1" --fast --format json | python3 -c 'import json,sys; print(next(x["status"] for x in json.load(sys.stdin) if x["name"] == sys.argv[1] and x["config"].get("user.raft") == "true"))' "$1")
test "$state" = Stopped || { echo 'Stop the source box before resume, snapshot, restore, fork or backup' >&2; exit 1; }
"""


CHECK_WORKER = (
    "python3 -c "
    + shlex.quote(
        "import runpy; "
        "worker = runpy.run_path('/usr/local/lib/raft/expire-worker'); "
        "\nif worker.get('PROTOCOL') != 1:\n"
        " raise SystemExit('Host expiry worker differs from this CLI; deploy the current Raft checkout first')"
    )
    + "\n"
)


HOST_RESOURCES = """import json, os, shutil, subprocess
from pathlib import Path
memory = dict(line.split()[:2] for line in Path('/proc/meminfo').read_text().splitlines())
disk = shutil.disk_usage('/var/lib/incus')
def pool(name):
    # Hosts not redeployed since own box disks were added have no raft-disks pool.
    response = subprocess.run(['incus', 'query', f'/1.0/storage-pools/{name}/resources'], capture_output=True)
    if response.returncode:
        return None
    space = json.loads(response.stdout)['space']
    return {'totalBytes': space['total'], 'freeBytes': space['total'] - space['used']}
print(json.dumps({
    'cpus': len(os.sched_getaffinity(0)),
    'memoryTotalBytes': int(memory['MemTotal:']) * 1024,
    'memoryAvailableBytes': int(memory['MemAvailable:']) * 1024,
    'hostDiskFreeBytes': disk.free,
    'sharedPool': pool('raft-data'),
    'boxDiskPool': pool('raft-disks'),
}))
"""

# Agents read capacity output to plan work. Earlier wording that looked like a cap
# made them skip boxes they needed, so every report leads with this sentence.
ADVISORY = (
    "Advisory estimate only. Raft enforces no box count or size budget; "
    "create the boxes your task needs."
)


def capacity(host, boxes):
    """Advisory planning numbers, not a reservation, admission rule or limit."""
    active = [box for box in boxes if box["status"] != "Stopped"]
    cpu_allocated = 0
    memory_allocated = 0
    for box in active:
        limits = box["expanded_config"]
        # Raft writes whole CPU counts and whole GiB; other forms come from native Incus edits.
        if not re.fullmatch(r"[1-9][0-9]*", limits["limits.cpu"]) or not re.fullmatch(
            r"[1-9][0-9]*GiB", limits["limits.memory"]
        ):
            raise ValueError(
                f"Active box {box['name']} has sizing Raft did not set; inspect its Incus configuration"
            )
        cpu_allocated += int(limits["limits.cpu"])
        memory_allocated += gib(limits["limits.memory"]) * GIB
    memory_reserve = max(2 * GIB, host["memoryTotalBytes"] // 10)
    cpu_budget = max(0, host["cpus"] - 1)
    memory_budget = max(0, host["memoryTotalBytes"] - memory_reserve)
    cpu_headroom = max(0, cpu_budget - cpu_allocated)
    memory_headroom = max(
        0,
        min(
            memory_budget - memory_allocated,
            host["memoryAvailableBytes"] - memory_reserve,
        ),
    )
    # Example shapes double from the 1 CPU / 2 GiB default at 2 GiB per CPU and
    # stop at the largest that fits an empty host. Boxes may use any other size;
    # headroom lets callers work out counts for those.
    estimates = []
    cpu = 1
    while cpu == 1 or (cpu <= cpu_budget and 2 * cpu * GIB <= memory_budget):
        memory = 2 * cpu * GIB
        estimates.append(
            {
                "cpu": cpu,
                "memoryGiB": 2 * cpu,
                "fitEmptyHost": min(cpu_budget // cpu, memory_budget // memory),
                "fitNow": min(cpu_headroom // cpu, memory_headroom // memory),
            }
        )
        cpu *= 2
    warnings = []
    if cpu_allocated > cpu_budget or memory_allocated > memory_budget:
        warnings.append(
            "Active boxes may use more CPU or RAM than the host budget; under load they share it"
        )
    if host["memoryAvailableBytes"] < memory_reserve:
        warnings.append(
            "Available host RAM is below the reserve; stop idle boxes or reduce other workloads"
        )
    if host["hostDiskFreeBytes"] < 5 * GIB or host["sharedPool"]["freeBytes"] < 5 * GIB:
        warnings.append(
            "Host or shared-pool free disk is below 5 GiB; check image and workspace growth"
        )
    if host["boxDiskPool"] is None:
        warnings.append("No raft-disks pool; redeploy this host before using new --disk")
    return {
        "notice": ADVISORY,
        "host": host,
        "boxes": len(boxes),
        "activeBoxes": len(active),
        "allocated": {"cpus": cpu_allocated, "memoryBytes": memory_allocated},
        "reserve": {"cpus": 1, "memoryBytes": memory_reserve},
        "headroom": {"cpus": cpu_headroom, "memoryBytes": memory_headroom},
        "estimates": estimates,
        "warnings": warnings,
    }


def host_capacity(location):
    response = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            settings(location)["ssh"],
            shlex.join(["sudo", "-n", "python3", "-c", HOST_RESOURCES]),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        "location": location,
        "note": settings(location).get("note", ""),
        **capacity(json.loads(response.stdout), inventory(location)),
    }


def print_capacity(report):
    host = report["host"]
    print(report["notice"])
    print(
        f"{report['location']}: {host['cpus']} CPUs, "
        f"{host['memoryTotalBytes'] / GIB:.1f} GiB RAM "
        f"({host['memoryAvailableBytes'] / GIB:.1f} GiB available)"
    )
    if report["note"]:
        print("Note: " + report["note"])
    print(f"Boxes: {report['boxes']} saved, {report['activeBoxes']} active")
    print(
        f"Allocated to active boxes: {report['allocated']['cpus']} CPUs, "
        f"{report['allocated']['memoryBytes'] / GIB:.1f} GiB; "
        f"host reserve: 1 CPU, {report['reserve']['memoryBytes'] / GIB:.1f} GiB; "
        f"headroom: {report['headroom']['cpus']} CPUs, "
        f"{report['headroom']['memoryBytes'] / GIB:.1f} GiB"
    )
    disk = [
        f"{host['sharedPool']['freeBytes'] / GIB:.1f} of "
        f"{host['sharedPool']['totalBytes'] / GIB:.1f} GiB shared pool"
    ]
    if host["boxDiskPool"] is not None:
        disk.append(
            f"{host['boxDiskPool']['freeBytes'] / GIB:.1f} of "
            f"{host['boxDiskPool']['totalBytes'] / GIB:.1f} GiB for own box disks"
        )
    print(f"Disk free: {', '.join(disk)}, {host['hostDiskFreeBytes'] / GIB:.1f} GiB host")
    print("CPU  RAM GiB  Fit on empty host  Fit now")
    for size in report["estimates"]:
        print(
            f"{size['cpu']:>3}  {size['memoryGiB']:>7}  {size['fitEmptyHost']:>17}  "
            f"{size['fitNow']:>7}"
        )
    print(
        "Counts assume every box uses its full CPU and RAM; idle boxes use less. "
        "Any whole CPU count and GiB size works."
    )
    for warning in report["warnings"]:
        print("Warning: " + warning)


def doctor(location):
    print(location + ": " + incus(location, "version").strip())
    try:
        transaction(location, CHECK_WORKER)
    except RuntimeError as error:
        raise RuntimeError(f"{location}: {error}") from error
    response = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            settings(location)["ssh"],
            "systemctl is-active incus raft-expire.timer raft-network.service && df -h /",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    print(response.stdout.strip())
    print(incus(location, "storage", "info", SHARED_POOL).strip())
    try:
        print(incus(location, "storage", "info", BOX_DISK_POOL).strip())
    except RuntimeError as error:
        raise RuntimeError(
            f"{location}: no {BOX_DISK_POOL} pool for own box disks ({error}); "
            "deploy the current Raft checkout first"
        ) from error
    print(f"{len(inventory(location))} saved boxes; system containers (shared host kernel)")


def transaction(location, script, *arguments, stdout=None):
    # All lifecycle steps in a compound operation share the host expiry lock.
    command = [
        "sudo",
        "-n",
        "flock",
        "/run/lock/raft-incus.lock",
        "sh",
        "-c",
        "set -eu\n" + script,
        "raft",
        *arguments,
    ]
    connection = [
        # Lifecycle transactions take arguments, never interactive or piped input.
        "ssh",
        "-n",
        "-T",
        "-o",
        "BatchMode=yes",
        settings(location)["ssh"],
        shlex.join(command),
    ]
    if stdout is not None:
        response = receive(connection, stdout)
    else:
        response = subprocess.run(connection, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if response.returncode:
        raise RuntimeError(response.stderr.decode().strip())
    return response.stdout.decode() if response.stdout is not None else ""


def snapshot_name(value):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Invalid snapshot name")
    return value


def sizing(args):
    values = []
    for field in ["cpu", "memory"]:
        value = getattr(args, field, None)
        if value is not None:
            values += ["-c", f"limits.{field}={value}"]
    return values


def fits_host(args):
    """Shell checks that refuse a CPU or RAM limit larger than the whole host.

    Incus accepts such limits and quietly gives the box less, so an agent would
    plan around resources it never gets. MemTotal is rounded up to whole GiB:
    an 8 GiB machine reports about 7.7 GiB and must still accept 8GiB. The
    parsers already restrict both values to whole numbers, so they embed safely.
    """
    checks = ""
    if args.cpu is not None:
        checks += (
            f'test {args.cpu} -le "$(nproc)" || '
            f'{{ echo "Requested {args.cpu} CPUs, but this host has $(nproc)" >&2; exit 1; }}\n'
        )
    if args.memory is not None:
        checks += (
            "total=$(awk '/^MemTotal:/ {print int(($2 + 1048575) / 1048576)}' /proc/meminfo)\n"
            f'test {gib(args.memory)} -le "$total" || '
            f'{{ echo "Requested {args.memory} RAM, but this host has $total GiB" >&2; exit 1; }}\n'
        )
    return checks


def own_disk(size):
    """Incus device overrides that give a box its own fixed-size disk in raft-disks.

    The full root device is spelled out because `incus copy` of a snapshot whose
    root comes from the profile adds an override as a whole new device.
    """
    return [
        "-d",
        "root,type=disk",
        "-d",
        "root,path=/",
        "-d",
        f"root,pool={BOX_DISK_POOL}",
        "-d",
        f"root,size={size}",
    ]


def new(args):
    box_fields = box_note(args)
    source = None
    if args.template:
        handle, separator, snapshot = args.template.partition("/")
        if not separator:
            raise ValueError("Use --from LOCATION:BOX/SNAPSHOT")
        location, original = parse_handle(handle)
        snapshot_name(snapshot)
        if args.location is not None and args.location != location:
            raise ValueError("Snapshot templates must stay on their source host")
        args.location = location
        instance(location, original)
        source = original + "/" + snapshot
    if args.location is None:
        args.location = next(iter(configuration()))
    if source is None:
        source = settings(args.location).get("image", "")
        if not isinstance(source, str) or not re.fullmatch(r"[a-f0-9]{64}", source):
            raise ValueError(
                "Build an image and configure its full immutable fingerprint before creating boxes"
            )
    note = settings(args.location).get("note")
    if note:
        # Stderr keeps stdout limited to the handle for scripts.
        print(f"{args.location} note: {note}", file=sys.stderr)
    name = "rf-" + uuid.uuid4().hex[:16]
    # Initialize before starting, so a failed start still has its intended policy.
    transaction(
        args.location,
        (CHECK_WORKER if args.disposable else "")
        + fits_host(args)
        + (
            f"incus storage show {BOX_DISK_POOL} >/dev/null 2>&1 || {{ echo 'This location "
            f"has no {BOX_DISK_POOL} pool for own box disks; deploy the current Raft "
            "checkout first' >&2; exit 1; }\n"
            if args.disk
            else ""
        )
        + 'lifetime="$1"; name="$2"; shift 2; "$@"; '
        'incus --project raft start "$name"; '
        'incus --project raft config set "$name" '
        'user.raft.expires="$(( $(date +%s) + lifetime ))"',
        str(args.ttl),
        name,
        "incus",
        "--project",
        PROJECT,
        "copy" if args.template else "init",
        source,
        name,
        "-c",
        "user.raft=true",
        "-c",
        "user.raft.expires=",
        "-c",
        "user.raft.stopped-at=",
        "-c",
        f"user.raft.disposable={'true' if args.disposable else 'false'}",
        "-c",
        # Always set, so a box created from a snapshot never inherits the source note.
        "user.raft.note=" + (json.dumps(box_fields) if box_fields else ""),
        "-c",
        "volatile.eth0.hwaddr=",
        *sizing(args),
        # Without --disk, init uses the profile's shared pool and copy keeps the
        # snapshot's disk: the shared pool or an own disk of the same size.
        *(own_disk(args.disk) if args.disk else []),
    )
    return f"{args.location}:{name}"


def execute(location, name, command, *, capture=False):
    return remote(location, ["exec", name, "--cwd", "/workspace", "--", *command], capture=capture)


def transfer_tree(args, location, name):
    worker = Path(raft_files.__file__).read_text()
    with tempfile.TemporaryFile() as archive:
        if args.action == "upload":
            raft_files.pack_directory(args.source, archive)
            size = archive.tell()
            archive.seek(0)
            response = remote(
                location,
                [
                    "exec",
                    name,
                    "--",
                    "python3",
                    "-c",
                    worker,
                    "unpack",
                    args.destination,
                    "--size",
                    str(size),
                ],
                stdin=archive,
            )
        else:
            response = remote(
                location,
                ["exec", name, "--", "python3", "-c", worker, "pack", args.source],
                stdout=archive,
            )
            if response.returncode == 0:
                size = archive.tell()
                archive.seek(0)
                raft_files.unpack_directory(archive, args.destination, size)
        if response.returncode:
            raise RuntimeError(response.stderr.decode(errors="replace").strip())
    return 0


def transfer(args, location, name):
    if args.recursive:
        return transfer_tree(args, location, name)
    # Stream file descriptors rather than buffering large artifacts in memory.
    if args.action == "upload":
        with Path(args.source).open("rb") as source:
            response = remote(
                location, ["file", "push", "-", name + args.destination], stdin=source
            )
    else:
        destination = Path(args.destination).resolve()
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".raft-", delete=False
        ) as target:
            temporary = Path(target.name)
            try:
                response = remote(
                    location, ["file", "pull", name + args.source, "-"], stdout=target
                )
                if response.returncode == 0:
                    target.flush()
                    os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    if response.returncode:
        raise RuntimeError(response.stderr.decode().strip())
    return 0


def backup(args, location, name):
    destination = Path(args.destination).resolve()
    if destination.exists():
        raise ValueError("Backup destination already exists; choose a new path")
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".raft-") as archive:
        # Some network filesystems ignore creation modes; enforce privacy before writing.
        os.fchmod(archive.fileno(), 0o600)
        if os.fstat(archive.fileno()).st_mode & 0o077:
            raise ValueError("Backup storage must support private file permissions")
        # Check stopped state under the same lock as export, so resume cannot race it.
        transaction(
            location,
            REQUIRE_STOPPED + 'exec incus --project raft export "$1" - --compression gzip',
            name,
            stdout=archive,
        )
        archive.flush()
        os.fsync(archive.fileno())
        # Link refuses a destination created during export; never replace another backup.
        os.link(archive.name, destination)
    print(str(destination))


def recover(args):
    host = settings(args.location)["ssh"]
    name = "rf-" + uuid.uuid4().hex[:16]
    response = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", host, "mktemp /tmp/raft-recovery.XXXXXXXX"],
        capture_output=True,
        check=True,
    )
    staged = response.stdout.decode().strip()
    if not re.fullmatch(r"/tmp/raft-recovery\.[a-zA-Z0-9]{8}", staged):
        raise RuntimeError("Host returned an invalid recovery staging path")
    print("Recovery handle: " + args.location + ":" + name, file=sys.stderr, flush=True)
    try:
        # Send through SSH stdin, avoiding SCP remote-path interpretation.
        with Path(args.source).open("rb") as archive:
            subprocess.run(
                [
                    "ssh",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    host,
                    shlex.join(["sh", "-c", 'umask 077; cat > "$1"', "raft", staged]),
                ],
                stdin=archive,
                check=True,
            )
        transaction(
            args.location,
            # Raft backups record their pool as the first archive entry. Own-disk boxes
            # return to raft-disks with their recorded size; anything else lands in the
            # shared pool, as before own disks existed.
            "pool=$(tar -xzOf \"$1\" --occurrence=1 backup/index.yaml | sed -n 's/^pool: //p')\n"
            f'test "$pool" = {BOX_DISK_POOL} || pool={SHARED_POOL}\n'
            'incus --project raft import "$1" "$2" --storage "$pool" '
            "-c user.raft=true -c user.raft.expires= -c user.raft.disposable=false -c user.raft.stopped-at= -c volatile.eth0.hwaddr=",
            staged,
            name,
        )
        if instance(args.location, name)["status"] != "Stopped":
            raise RuntimeError("Recovered box unexpectedly running: " + args.location + ":" + name)
        print(args.location + ":" + name)
    finally:
        subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                host,
                shlex.join(["sudo", "-n", "rm", "-f", "--", staged]),
            ],
            check=True,
        )


def address(location, name):
    # Use the managed NIC, never Docker bridges or volatile process counters.
    response = execute(location, name, ["ip", "-json", "addr", "show", "dev", "eth0"], capture=True)
    if response.returncode:
        raise RuntimeError(response.stderr.decode().strip())
    interface = json.loads(response.stdout)[0]
    for candidate in interface["addr_info"]:
        if candidate["family"] == "inet" and candidate["scope"] == "global":
            return candidate["local"]
    raise ValueError("Running box has no eth0 IPv4 address yet")


def forward(location, name, port, local_port):
    ip = address(location, name)
    print(f"http://127.0.0.1:{local_port} (Ctrl+C closes the private tunnel)", flush=True)
    return os.execvp(
        "ssh",
        [
            "ssh",
            "-N",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-L",
            f"127.0.0.1:{local_port}:{ip}:{port}",
            settings(location)["ssh"],
        ],
    )


def ttl(value):
    seconds = int(value)
    if not 60 <= seconds <= 2592000:
        raise argparse.ArgumentTypeError("TTL must be 60 seconds to 30 days")
    return seconds


def note_options(command):
    """Add the box note template flags shared by new and note."""
    command.add_argument(
        "--purpose", help="Box note: what the box is for, e.g. 'Integration tests for PR 42'"
    )
    command.add_argument(
        "--owner", help="Box note: who uses the box, e.g. 'codex session in ~/work/app'"
    )
    command.add_argument(
        "--next", help="Box note: what happens next, e.g. 'Destroy after the tests pass'"
    )


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="action", required=True)
    create = commands.add_parser("new")
    create.add_argument("--location", default=None)
    create.add_argument("--ttl", type=ttl, default=600)
    create.add_argument("--cpu", type=cpu_count, default=1, help="CPU limit (default: 1)")
    create.add_argument(
        "--memory", type=gib_size, default="2GiB", help="RAM limit, e.g. 16GiB (default: 2GiB)"
    )
    create.add_argument(
        "--disk",
        type=gib_size,
        metavar="SIZE",
        help="Give the box its own disk of SIZE, e.g. 20GiB, that it cannot grow past "
        "(default: the shared pool; --from keeps the snapshot's disk)",
    )
    create.add_argument("--disposable", action="store_true", help="Delete on stop or expiry")
    create.add_argument("--from", dest="template", metavar="LOCATION:BOX/SNAPSHOT")
    note_options(create)
    commands.add_parser("locations", help="List configured locations and their notes")
    note = commands.add_parser("note", help="Set or clear the note of a location or a box")
    note.add_argument("target", metavar="LOCATION|BOX", help="A location name or a box handle")
    note.add_argument("text", nargs="?", help="Location guidance, e.g. 'Preferred for builds'")
    note.add_argument("--clear", action="store_true", help="Remove the note")
    note_options(note)
    host = commands.add_parser("host", help="Onboard workspace hosts")
    host_commands = host.add_subparsers(dest="host_action", required=True)
    onboard = host_commands.add_parser(
        "add", help="Check, provision, register and smoke-test a new host location"
    )
    onboard.add_argument("location")
    onboard.add_argument(
        "--ssh", required=True, help="Key-based SSH target: user@host or an ~/.ssh/config alias"
    )
    source = onboard.add_mutually_exclusive_group()
    source.add_argument(
        "--image-release",
        default="v" + __version__,
        metavar="TAG",
        help="Release whose checksummed image to download (default: this CLI's version)",
    )
    source.add_argument("--image-archive", metavar="PATH", help="Import a local image archive")
    source.add_argument("--build", action="store_true", help="Build the image on the host")
    onboard.add_argument(
        "--dry-run", action="store_true", help="Check the host and print the plan; change nothing"
    )
    onboard.add_argument("--yes", action="store_true", help="Apply the plan without prompting")
    onboard.add_argument(
        "--disk-pool",
        type=gib_size,
        metavar="SIZE",
        help=f"Host disk set aside for own box disks (default when created: {DEFAULT_DISK_POOL})",
    )
    listing = commands.add_parser("list")
    listing.add_argument("--location")
    report = commands.add_parser("capacity", help=ADVISORY, description=ADVISORY)
    report.add_argument("--location")
    report.add_argument("--json", action="store_true")
    commands.add_parser("doctor")
    gc = commands.add_parser("gc", help="Enforce deadlines, including disposable deletion")
    gc.add_argument("--location")
    prune = commands.add_parser("prune", help="Preview deletion of old stopped boxes")
    prune.add_argument("--location")
    prune.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    prune.add_argument("--yes", action="store_true", help="Delete the matching boxes")
    recovery = commands.add_parser("recover", help="Import a portable backup as a new stopped box")
    recovery.add_argument("source")
    recovery.add_argument("--location", required=True)
    for action in [
        "info",
        "stop",
        "resume",
        "extend",
        "destroy",
        "ssh",
        "exec",
        "upload",
        "download",
        "snapshot",
        "snapshots",
        "snapshot-delete",
        "restore",
        "fork",
        "forward",
        "desktop",
        "logs",
        "status",
        "cancel",
        "backup",
        "usage",
    ]:
        command = commands.add_parser(action)
        command.add_argument("box")
        if action in ["resume", "fork", "extend"]:
            command.add_argument("--ttl", type=ttl, required=True)
        if action in ["resume", "fork"]:
            command.add_argument("--cpu", type=cpu_count)
            command.add_argument("--memory", type=gib_size)
        if action == "fork":
            command.add_argument("--disposable", action="store_true")
        if action == "exec":
            command.add_argument("--detach", action="store_true")
            command.add_argument("command", nargs="+")
        if action in ["upload", "download"]:
            command.add_argument("source")
            command.add_argument("destination")
            command.add_argument("--recursive", action="store_true")
        if action in ["snapshot", "restore", "snapshot-delete"]:
            command.add_argument("name")
        if action in ["logs", "status", "cancel"]:
            command.add_argument("job")
        if action == "backup":
            command.add_argument("destination")
        if action == "forward":
            command.add_argument("--remote", type=int, required=True)
            command.add_argument("--local", type=int, required=True)
        if action == "desktop":
            command.add_argument("--local", type=int, default=6080)
    return cli


def dispatch(args):
    if args.action == "recover":
        recover(args)
        return 0
    if args.action == "new":
        print(new(args))
        return 0
    if args.action == "host":
        host_add(args)
        return 0
    if args.action == "note":
        fields = box_note(args)
        # Location names cannot contain ":", so a colon always means a box handle.
        if ":" not in args.target:
            if fields is not None:
                raise ValueError("--purpose, --owner and --next describe boxes; pass a box handle")
            if args.clear == (args.text is not None):
                raise ValueError("Give the location note TEXT or --clear")
            set_note(args.target, None if args.clear else args.text)
            return 0
        if args.text is not None:
            raise ValueError("Describe boxes with --purpose, --owner and --next")
        if args.clear == (fields is not None):
            raise ValueError("Give --purpose (with optional --owner and --next) or --clear")
        location, name = parse_handle(args.target)
        instance(location, name)
        # Each call replaces the whole note; an empty value removes the key.
        value = "" if args.clear else json.dumps(fields)
        incus(location, "config", "set", name, "user.raft.note=" + value, locked=True)
        return 0
    if args.action == "locations":
        for index, (location, entry) in enumerate(configuration().items()):
            record = {"location": location, "default": index == 0, "note": entry.get("note", "")}
            print(json.dumps(record))
        return 0
    if args.action in ["list", "capacity", "doctor", "gc", "prune"]:
        if args.action == "prune" and args.older_than < 1:
            raise ValueError("Retention must be at least one day")
        locations = configuration()
        if getattr(args, "location", None):
            locations = [args.location]
        for location in locations:
            if args.action == "capacity":
                report = host_capacity(location)
                if args.json:
                    print(json.dumps(report))
                else:
                    print_capacity(report)
            elif args.action in ["gc", "prune"]:
                worker = ["sudo", "-n", "/usr/local/lib/raft/expire"]
                if args.action == "prune":
                    worker += ["--prune-days", str(args.older_than)]
                    if args.yes:
                        worker += ["--yes"]
                if args.action == "prune":
                    output = transaction(
                        location,
                        CHECK_WORKER + 'exec /usr/local/lib/raft/expire-worker "$@"',
                        *worker[3:],
                    )
                else:
                    response = subprocess.run(
                        [
                            "ssh",
                            "-T",
                            "-o",
                            "BatchMode=yes",
                            settings(location)["ssh"],
                            shlex.join(worker),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if response.returncode:
                        raise RuntimeError(response.stderr.strip())
                    output = response.stdout
                if args.action == "prune":
                    for line in output.splitlines():
                        record = json.loads(line)
                        record["box"] = location + ":" + record["box"]
                        print(json.dumps(record))
                else:
                    print(output, end="")
            elif args.action == "doctor":
                doctor(location)
            else:
                for box in inventory(location):
                    print(
                        json.dumps(
                            {
                                "box": location + ":" + box["name"],
                                "state": box["status"],
                                "stopAt": box["config"].get("user.raft.expires", ""),
                                "disk": box_disk(box),
                                "note": read_box_note(box),
                            }
                        )
                    )
        return 0
    location, name = parse_handle(args.box)
    box = instance(location, name)
    if args.action == "info":
        print(json.dumps(box, indent=2))
    elif args.action == "backup":
        backup(args, location, name)
    elif args.action == "usage":
        # Resource counters require a running guest; info remains metadata-only.
        response = execute(location, name, ["python3", "-c", USAGE], capture=True)
        if response.returncode:
            raise RuntimeError(response.stderr.decode().strip())
        usage = json.loads(response.stdout)
        disk = box_disk(box)
        usage["filesystemScope"] = (
            "shared Raft pool, not per-box usage" if disk == "shared" else f"own {disk} box disk"
        )
        print(json.dumps(usage))
    elif args.action == "destroy":
        incus(location, "delete", name, "--force", locked=True)
        if any(item["name"] == name for item in inventory(location)):
            raise RuntimeError("Deletion was not confirmed")
        print("Destroyed " + args.box)
    elif args.action == "stop":
        transaction(
            location,
            'disposable=$(incus --project raft config get "$1" user.raft.disposable); '
            'if [ "$disposable" = true ]; then incus --project raft delete "$1" --force; '
            'else incus --project raft stop "$1" --timeout 30; '
            'incus --project raft config unset "$1" user.raft.expires; '
            'incus --project raft config set "$1" user.raft.stopped-at="$(date +%s)"; fi',
            name,
        )
    elif args.action == "resume":
        overrides = []
        for field in ["cpu", "memory"]:
            value = getattr(args, field)
            if value is not None:
                overrides.append(f"limits.{field}={value}")
        transaction(
            location,
            REQUIRE_STOPPED + fits_host(args) + 'name="$1"; lifetime="$2"; shift 2; '
            'if [ "$#" -gt 0 ]; then incus --project raft config set "$name" "$@"; fi; '
            'incus --project raft start "$name"; '
            'incus --project raft config set "$name" user.raft.stopped-at=; '
            'incus --project raft config set "$name" user.raft.expires="$(( $(date +%s) + lifetime ))"',
            name,
            str(args.ttl),
            *overrides,
        )
    elif args.action == "extend":
        transaction(
            location,
            'test "$(incus --project raft list "$1" --fast --format json | '
            'python3 -c \'import json,sys; print(next(x["status"] for x in json.load(sys.stdin) '
            'if x["name"] == sys.argv[1]))\' "$1")" = Running '
            '|| { echo "Extend requires a running box; use resume for a stopped box" >&2; exit 1; }; '
            'incus --project raft config set "$1" user.raft.expires="$(( $(date +%s) + $2 ))"',
            name,
            str(args.ttl),
        )
    elif args.action == "ssh":
        return remote(
            location,
            ["exec", name, "--cwd", "/workspace", "--", "/bin/bash", "-l"],
            capture=False,
            tty=True,
        ).returncode
    elif args.action == "exec":
        payload = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not payload:
            raise ValueError("Supply a command after --")
        if args.detach:
            job = "rfcmd-" + uuid.uuid4().hex
            response = execute(
                location,
                name,
                [
                    "systemd-run",
                    "--quiet",
                    "--unit",
                    job,
                    "--property=WorkingDirectory=/workspace",
                    "--property=RemainAfterExit=yes",
                    "--",
                    *payload,
                ],
                capture=True,
            )
            if response.returncode:
                raise RuntimeError(response.stderr.decode().strip())
            print(job)
        else:
            return execute(location, name, payload).returncode
    elif args.action in ["upload", "download"]:
        guest_path = args.destination if args.action == "upload" else args.source
        if not guest_path.startswith("/"):
            raise ValueError("Guest file path must be absolute")
        return transfer(args, location, name)
    elif args.action == "snapshot":
        snapshot_name(args.name)
        print(
            transaction(
                location,
                REQUIRE_STOPPED + 'incus --project raft snapshot create "$1" "$2"',
                name,
                args.name,
            ).strip()
        )
    elif args.action == "snapshot-delete":
        snapshot_name(args.name)
        incus(location, "snapshot", "delete", name, args.name, locked=True)
    elif args.action == "snapshots":
        print(incus(location, "info", name).strip())
    elif args.action == "restore":
        snapshot_name(args.name)
        # Imported/forked snapshots carry source configuration. Restore files,
        # keeping the destination's network identity and explicitly chosen limits.
        transaction(
            location,
            REQUIRE_STOPPED + 'incus --project raft snapshot restore "$1" "$2" --diskonly',
            name,
            args.name,
        )
    elif args.action == "fork":
        child = "rf-" + uuid.uuid4().hex[:16]
        transaction(
            location,
            (CHECK_WORKER if args.disposable else "")
            + REQUIRE_STOPPED
            + fits_host(args)
            + 'name="$2"; lifetime="$3"; '
            'source="$1"; shift 3; incus --project raft copy "$source" "$name" --instance-only "$@"; '
            'incus --project raft start "$name"; '
            'incus --project raft config set "$name" user.raft.expires="$(( $(date +%s) + lifetime ))"',
            name,
            child,
            str(args.ttl),
            "-c",
            "user.raft.expires=",
            "-c",
            "user.raft.stopped-at=",
            "-c",
            f"user.raft.disposable={'true' if args.disposable else 'false'}",
            "-c",
            "volatile.eth0.hwaddr=",
            *sizing(args),
        )
        print(location + ":" + child)
    elif args.action in ["logs", "status", "cancel"]:
        if not re.fullmatch(r"rfcmd-[a-f0-9]{32}", args.job):
            raise ValueError("Use the job ID returned by exec --detach")
        loaded = execute(
            location,
            name,
            ["systemctl", "show", args.job, "-p", "LoadState", "--value"],
            capture=True,
        )
        if loaded.returncode or loaded.stdout.strip() != b"loaded":
            # Cancelled transient units unload, but their journal keeps history.
            # Live units remain recognizable even after old logs are rotated away.
            recorded = execute(
                location,
                name,
                ["journalctl", "--no-pager", "--quiet", "-o", "json", "-n", "1", "-u", args.job],
                capture=True,
            )
            if recorded.returncode or not recorded.stdout.strip():
                raise ValueError("Detached job not found in this running box")
        if args.action == "cancel":
            return execute(location, name, ["systemctl", "stop", args.job]).returncode
        command = (
            ["journalctl", "--no-pager", "-o", "cat", "-u", args.job]
            if args.action == "logs"
            else [
                "systemctl",
                "show",
                args.job,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "ExecMainStatus",
            ]
        )
        return execute(location, name, command).returncode
    elif args.action in ["forward", "desktop"]:
        port = args.remote if args.action == "forward" else 6080
        if not 1 <= port <= 65535 or not 1 <= args.local <= 65535:
            raise ValueError("Ports must be between 1 and 65535")
        if args.action == "desktop":
            response = execute(location, name, ["systemctl", "start", "raft-desktop"], capture=True)
            if response.returncode:
                raise RuntimeError(response.stderr.decode().strip())
        return forward(location, name, port, args.local)
    return 0


def main():
    if sys.version_info < (3, 11, 8):
        print("raft: Python 3.11.8 or newer is required", file=sys.stderr)
        return 1
    os.umask(0o077)
    try:
        return dispatch(parser().parse_args())
    except (
        OSError,
        ValueError,
        RuntimeError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"raft: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
