#!/usr/bin/env python3
"""Self-hosted persistent Linux workspaces powered by Incus."""

import argparse
import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import tarfile
import threading
import time
import urllib.error
import urllib.request
import uuid

import raft_files
import raft_run
import raft_output

__version__ = "0.7.0"

CONFIG = Path.home() / ".config/raft/incus.json"
# Controller defaults for new live apart from the location inventory, so location
# names can never collide with setting names.
DEFAULTS_CONFIG = Path.home() / ".config/raft/config.json"
PROJECT = "raft"
LOCATION_NAME = r"[a-zA-Z0-9][a-zA-Z0-9_-]*"
GIB = 1024**3
# Boxes use the shared Btrfs pool unless `new --disk` gives them their own disk:
# a fixed-size thin logical volume in this LVM pool. Deployment always creates the
# shared pool; the own-disk pool exists only on hosts deployed with --disk-pool.
SHARED_POOL = "raft-data"
BOX_DISK_POOL = "raft-disks"
# Size of a box created from the image without --cpu, --memory or a config.json
# default. Boxes created from a snapshot keep the snapshot's CPU, RAM and disk
# unless a flag overrides it; config.json defaults never do.
DEFAULT_CPU = 1
DEFAULT_MEMORY = "2GiB"
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


def edit_json(path, load, change):
    """Apply change(document) to a controller JSON file and save it.

    load() reads and validates the current file. An empty document removes the file.
    """
    # main() sets umask 077, so a new configuration directory is private.
    path.parent.mkdir(parents=True, exist_ok=True)
    # Hold a controller lock across read, edit and replace so concurrent edits
    # cannot drop each other. Readers need no lock because replacement is atomic.
    with (path.parent / f".{path.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Onboarding can start on a controller that has no configuration yet.
        document = load() if path.exists() else {}
        change(document)
        if not document:
            # Removing the last entry restores the unconfigured state.
            path.unlink(missing_ok=True)
            return
        # Key order is preserved because the first location is the default for new.
        # Replace atomically so an interrupted write cannot truncate the file;
        # NamedTemporaryFile creates the staged file with mode 0600.
        staged = tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".raft-", delete=False)
        try:
            with staged:
                json.dump(document, staged, indent=2)
                staged.write("\n")
            os.replace(staged.name, path)
        finally:
            # A no-op after a successful replace; removes the file after a failure.
            Path(staged.name).unlink(missing_ok=True)


def edit_configuration(change):
    """Apply change(inventory) to the location inventory and save it."""
    edit_json(CONFIG, configuration, change)


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


def disk_size(value):
    """Parse new --disk: an own-disk size like 20GiB, or shared for the shared pool."""
    return value if value == "shared" else gib_size(value)


def gib(size):
    return int(size.removesuffix("GiB"))


def box_disk(box):
    """Return a box's own disk size, e.g. 20GiB, or "shared" for the shared pool."""
    root = box["expanded_devices"].get("root", {})
    return root.get("size", "") if root.get("pool") == BOX_DISK_POOL else "shared"


# Box note policy. A host can require note fields for every box it holds; the
# policy lives on the host, in the Incus project key user.raft.require (for
# example "purpose,owner"), so every controller reads the same rule. Each
# command that creates a box or changes its note checks the policy twice: once
# from the controller for an actionable message before any work, and again in
# the host transaction that writes the note, under the host lock. This is a
# guardrail for agents: SSH and sudo on the host bypass it, and controllers
# older than this check never run it.
POLICY_KEY = "user.raft.require"
# Stopped-box retention, the second host policy: the host expiry worker deletes
# raft boxes that have been stopped for this many hours. Its .since key holds
# when the policy was last set, in epoch seconds: the retention clock starts at
# the later of a box's stop and that time, so setting or shortening a policy
# never deletes a box sooner than the given hours after the change. Mirrors
# RETENTION_KEY in deploy/raft-expire.py, which enforces it.
RETENTION_KEY = "user.raft.delete-stopped-after"
RETENTION_SINCE_KEY = RETENTION_KEY + ".since"

# Host half of the check, run with the required fields and the note the box
# will carry. Kept short; the controller builds the detailed message.
CHECK_NOTE_CODE = """import json, sys
required = [field for field in sys.argv[1].split(",") if field]
note = json.loads(sys.argv[2]) if sys.argv[2] else {}
missing = [field for field in required if not note.get(field)]
if missing:
    raise SystemExit(
        "This host requires box note fields " + ", ".join(required)
        + " (Incus project key user.raft.require); missing " + ", ".join(missing)
        + ". Nothing was changed. Give the note with "
        + " ".join("--" + field for field in required) + " and retry"
    )
"""
# Read the policy with errexit active: inside a command substitution used as an
# argument, a failed read would pass an empty policy and let every note through.
READ_NOTE_POLICY = f"require=$(incus project get raft {POLICY_KEY})\n"
# Callers set $note to the exact value they write to user.raft.note.
CHECK_NOTE = "python3 -c " + shlex.quote(CHECK_NOTE_CODE) + ' "$require" "$note"'


def host_policy(location):
    """Return a location's host policies and their last recorded changes, as raft config shows them."""
    # `incus query` rejects the --project flag that remote() always passes.
    projects = json.loads(incus(location, "project", "list", "--format", "json"))
    config = next(item for item in projects if item["name"] == PROJECT)["config"]
    return parse_host_policy(location, config)


def parse_host_policy(location, config):
    """Validate native policy values before displaying or enforcing them."""
    stored = config.get(POLICY_KEY, "")
    required = [field for field in stored.split(",") if field]
    unknown = [field for field in required if field not in BOX_NOTE_FIELDS]
    if unknown:
        raise ValueError(
            f"{location}: host policy {POLICY_KEY}={stored!r} names unknown note fields; "
            f"fix it with raft policy {location} --require FIELDS or --require none"
        )
    hours = config.get(RETENTION_KEY, "")
    since = config.get(RETENTION_SINCE_KEY, "")
    # Raft writes both as whole numbers; anything else came from a native Incus edit.
    if hours and not (re.fullmatch(r"[1-9][0-9]*", hours) and re.fullmatch(r"[0-9]+", since)):
        raise ValueError(
            f"{location}: host policy {RETENTION_KEY}={hours!r} ({RETENTION_SINCE_KEY}={since!r}) "
            f"is not a whole number of hours; fix it with raft policy {location} "
            "--delete-stopped-after HOURS or --delete-stopped-after never"
        )
    return {
        "require": required,
        "requireChanged": config.get(POLICY_KEY + ".changed", ""),
        "deleteStoppedAfterHours": int(hours) if hours else None,
        "deleteStoppedAfterSince": int(since) if hours else None,
        "deleteStoppedAfterChanged": config.get(RETENTION_KEY + ".changed", ""),
    }


def delete_at(box, policy):
    """When the host worker deletes a stopped box under the retention policy, or None.

    Must match the deletion rule in deploy/raft-expire.py. A stopped box without
    a recorded stop time has no deadline until the worker records one.
    """
    hours = policy["deleteStoppedAfterHours"]
    stopped = box["config"].get("user.raft.stopped-at")
    if box["status"] != "Stopped" or hours is None or not stopped:
        return None
    return max(int(stopped), policy["deleteStoppedAfterSince"]) + hours * 3600


def note_problem(location, required, note, command, hint=""):
    """Describe how note falls short of the required fields, or return None."""
    missing = [field for field in required if not (note or {}).get(field)]
    if not missing:
        return None
    flags = " and ".join("--" + field for field in missing)
    usage = " ".join(
        f'--{field} "..."' if field in required else f'[--{field} "..."]'
        for field in BOX_NOTE_FIELDS
    )
    return (
        f"location {location} requires {flags} (host policy).{hint} "
        f"Add {'it' if len(missing) == 1 else 'them'}: raft {command} {usage}"
    )


def require_note(location, note, command, hint=""):
    """Fail before any host change when note lacks a field the location requires."""
    problem = note_problem(location, host_policy(location)["require"], note, command, hint)
    if problem:
        raise ValueError(problem)


def note_fields(text):
    """Parse --require FIELDS into BOX_NOTE_FIELDS order; none means no required fields."""
    if text == "none":
        return []
    fields = [field.strip() for field in text.split(",") if field.strip()]
    unknown = [field for field in fields if field not in BOX_NOTE_FIELDS]
    if unknown or not fields:
        raise ValueError(
            "Give note fields from " + ", ".join(BOX_NOTE_FIELDS) + ", e.g. --require purpose,owner"
        )
    if "purpose" not in fields:
        # Every box note has a purpose, so a policy without it would be misleading.
        raise ValueError("Box notes always need purpose; include it, e.g. --require purpose,owner")
    return [field for field in BOX_NOTE_FIELDS if field in fields]


def retention_hours(text):
    """Parse --delete-stopped-after HOURS: a positive whole number, or never for no retention."""
    if text == "never":
        return None
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError(
            "Give a positive whole number of hours, e.g. --delete-stopped-after 48, or never"
        )
    return int(text)


def set_policy(location, key, value, journal, check=""):
    """Set one host policy key to value, empty to clear it, and record the change.

    Returns the previous value. journal names the policy in the host journal entry;
    check is shell that must pass first, under the same lock.
    """
    # Who and when is recorded on the host beside the policy and in its journal.
    # The controller identity is self-reported; SUDO_USER is the SSH account.
    # Retention also records when it was set, for the worker's clock (see RETENTION_KEY).
    since = f' {RETENTION_SINCE_KEY}="$([ -z "$1" ] || date +%s)"' if key == RETENTION_KEY else ""
    return transaction(
        location,
        check + f"before=$(incus project get raft {key})\n"
        'changed="$(date -u +%Y-%m-%dT%H:%M:%SZ) by ${SUDO_USER:-root} from $2"\n'
        f'incus project set raft {key}="$1" {key}.changed="$changed"{since}\n'
        f"logger -t raft \"{journal}: '$before' -> '$1' ($changed)\"\n"
        "printf '%s\\n' \"$before\"",
        value,
        f"{getpass.getuser()}@{socket.gethostname()}",
    ).strip()


def change_policy(args):
    """Apply raft policy: each given option sets or clears its own policy independently."""
    if args.require is None and args.delete_stopped_after is None:
        raise ValueError("Give --require FIELDS|none, --delete-stopped-after HOURS|never, or both")
    settings(args.location)
    # Parse every option before the first host change. Retention goes first: its
    # worker check is the one step that can still refuse.
    fields = None if args.require is None else note_fields(args.require)
    hours = (
        None if args.delete_stopped_after is None else retention_hours(args.delete_stopped_after)
    )
    if args.delete_stopped_after is not None:
        value = "" if hours is None else str(hours)
        before = set_policy(
            args.location,
            RETENTION_KEY,
            value,
            "Stopped-box retention: hours",
            # Only a current worker enforces retention; clearing is safe on any worker.
            check=CHECK_WORKER if hours else "",
        )

        def shown(stored):
            return "never" if not stored else stored + (" hour" if stored == "1" else " hours")

        print(f"{args.location}: delete stopped boxes after {shown(before)} -> {shown(value)}")
    if fields is not None:
        value = ",".join(fields)
        before = set_policy(args.location, POLICY_KEY, value, "Box note policy: required fields")
        print(f"{args.location}: required note fields {before or 'none'} -> {value or 'none'}")


def receive(command, destination, *, stdin=None):
    """Write streamed bytes in Python: SSH can exit zero after a local write error."""
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(
            command, stdin=stdin, stdout=subprocess.PIPE, stderr=errors
        ) as process:
            try:
                shutil.copyfileobj(process.stdout, destination)
                returncode = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
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


def host_problems(facts, disk_pool=None):
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
    # Only a fresh deployment creates the 60 GiB shared pool, and the own-disk
    # pool when --disk-pool asks for it; deploy.py enforces the same minimum.
    disk = int(facts.get("disk") or 0) * 1024
    needed = 80 + (gib(disk_pool) if disk_pool else 0)
    if facts.get("deployed") != "yes" and disk < needed * GIB:
        problems.append(
            f"Fresh hosts need at least {needed} GiB free disk"
            + (f" (80 GiB plus the {disk_pool} own-disk pool)" if disk_pool else "")
            + f"; found {disk / GIB:.1f} GiB"
            + (". A smaller --disk-pool needs less" if disk_pool else "")
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
            # Explicit, so a configured disk default never moves the smoke box off the shared pool.
            disk="shared",
            disposable=True,
            template=None,
            # A full note satisfies any note policy left on a reused deployment.
            purpose="raft host add smoke test",
            owner="raft host add",
            next="Deleted by the smoke test",
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
    problems = host_problems(facts, args.disk_pool)
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
        + (
            f"- create the {args.disk_pool} thin LVM pool raft-disks for own box disks if it is "
            "missing, and allocate its file on the host disk in full\n"
            if args.disk_pool
            else ""
        )
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


# These probes retain --fast so stopped/stopping guests never need live counters.
HOST_BOXES = (
    "project = "
    + repr(PROJECT)
    + "\n"
    + """
print(json.dumps(json.loads(subprocess.run(['incus', '--project', project, 'list', '--fast', '--format', 'json'],
                     check=True, capture_output=True).stdout)))
"""
)
HOST_INVENTORY = (
    "project = "
    + repr(PROJECT)
    + "\n"
    + """import json, subprocess
projects = json.loads(subprocess.run(['incus', '--project', project, 'project', 'list', '--format', 'json'],
                                    check=True, capture_output=True).stdout)
boxes = json.loads(subprocess.run(['incus', '--project', project, 'list', '--fast', '--format', 'json'],
                                 check=True, capture_output=True).stdout)
print(json.dumps({'policy': next(p for p in projects if p['name'] == project)['config'], 'boxes': boxes}))
"""
)


REQUIRE_STOPPED = """state=$(incus --project raft list "$1" --fast --format json | python3 -c 'import json,sys; print(next(x["status"] for x in json.load(sys.stdin) if x["name"] == sys.argv[1] and x["config"].get("user.raft") == "true"))' "$1")
test "$state" = Stopped || { echo 'Stop the source box before resume, snapshot, restore, fork or backup' >&2; exit 1; }
"""


# Refuses a host whose expiry worker speaks another PROTOCOL than this CLI. Bump
# PROTOCOL here and in deploy/raft-expire.py together whenever the CLI starts to
# rely on new worker behavior; protocol 2 added stopped-box retention.
CHECK_WORKER = (
    "python3 -c "
    + shlex.quote(
        "import runpy; "
        "worker = runpy.run_path('/usr/local/lib/raft/expire-worker'); "
        "\nif worker.get('PROTOCOL') != 2:\n"
        " raise SystemExit('Host expiry worker differs from this CLI; deploy the current Raft checkout first')"
    )
    + "\n"
)


# Host-side accounting for the own-disk pool, shared by capacity reports and the
# reservation check. It runs as root on the host, where python3 and lvs exist.
#
# raft-disks is a thin LVM pool, so forks, templates and snapshots of own-disk
# boxes are thin snapshots that share blocks instead of copies. Thin volumes can
# promise more space than the pool holds, and a full thin pool fails every volume
# in it at once. Raft never lets that happen: a thin volume can never hold more
# data than its virtual size, so while the virtual sizes of all thin volumes in
# the pool (boxes, their snapshots, the cached image and anything else) add up to
# no more than the pool's data space, the pool cannot fill. Every Raft operation
# that adds a thin volume to raft-disks checks that sum first, under the host lock.
BOX_DISK_ACCOUNTING = (
    "POOL = "
    + repr(BOX_DISK_POOL)
    + """
import json, subprocess, sys
GIB = 1024**3

def query(path):
    return json.loads(subprocess.run(['incus', 'query', path], check=True, capture_output=True).stdout)

def size_bytes(value):
    # Raft sizes own disks in whole GiB; anything else came from a native Incus edit.
    if not value.endswith('GiB') or not value[:-3].isdigit():
        sys.exit(f'Unexpected own-disk size {value!r}; inspect it with incus')
    return int(value[:-3]) * GIB

def box_disk_pool():
    # Own box disks are opt-in per host, so a missing pool is a normal state.
    if '/1.0/storage-pools/' + POOL not in query('/1.0/storage-pools'):
        return None
    config = query('/1.0/storage-pools/' + POOL)['config']
    thinpool = config['lvm.thinpool_name']
    columns = 'lv_name,lv_size,pool_lv,data_percent,metadata_percent'
    report = subprocess.run(['lvs', '--reportformat', 'json', '--units', 'b', '--nosuffix', '-o', columns, config['lvm.vg_name']], check=True, capture_output=True)
    volumes = json.loads(report.stdout)['report'][0]['lv']
    pool = next(volume for volume in volumes if volume['lv_name'] == thinpool)
    # LVM reports usage only while the thin pool is active; reservations never need it.
    active = pool['data_percent'] != ''
    total = int(pool['lv_size'])
    return {
        'totalBytes': total,
        'reservedBytes': sum(int(volume['lv_size']) for volume in volumes if volume['pool_lv'] == thinpool),
        'usedBytes': int(total * float(pool['data_percent']) / 100) if active else None,
        'metadataUsedPercent': float(pool['metadata_percent']) if active else None,
        # Incus caches the image as a thin volume of the pool's volume.size, or
        # its 10 GiB default, and starts each own-disk box as a snapshot of it.
        'imageVolumeBytes': size_bytes(config.get('volume.size', '10GiB')),
    }

def reserve(location, pool, added, origin=''):
    # Exit with the reason unless thin volumes of the added sizes fit in full.
    # origin, when given, is a sentence saying where the requested size came from.
    suffix = '. ' + origin if origin else ''
    if added and pool is None:
        sys.exit(
            f'{location} has no {POOL} pool, so own box disks are not enabled there; nothing was '
            f'created. Enable them with python3 deploy/deploy.py --location {location} '
            '--disk-pool SIZE' + suffix
        )
    if added and pool['reservedBytes'] + sum(added) > pool['totalBytes']:
        sys.exit(
            f'{location}: own box disks would need {sum(added) / GIB:.1f} GiB more of {POOL}, '
            f'which has {(pool["totalBytes"] - pool["reservedBytes"]) / GIB:.1f} of '
            f'{pool["totalBytes"] / GIB:.1f} GiB unreserved; nothing was created. Destroy '
            'own-disk boxes or snapshots you no longer need, or use a smaller disk or the '
            'shared pool' + suffix
        )
"""
)

HOST_RESOURCES = (
    BOX_DISK_ACCOUNTING
    + """
import os, shutil
from pathlib import Path
memory = dict(line.split()[:2] for line in Path('/proc/meminfo').read_text().splitlines())
disk = shutil.disk_usage('/var/lib/incus')
space = query('/1.0/storage-pools/raft-data/resources')['space']
own = box_disk_pool()
if own is not None:
    del own['imageVolumeBytes']
print(json.dumps({
    'cpus': len(os.sched_getaffinity(0)),
    'memoryTotalBytes': int(memory['MemTotal:']) * 1024,
    'memoryAvailableBytes': int(memory['MemAvailable:']) * 1024,
    'hostDiskFreeBytes': disk.free,
    'sharedPool': {'totalBytes': space['total'], 'freeBytes': space['total'] - space['used']},
    'boxDiskPool': own,
}))
"""
)

# Reservation check run inside lifecycle transactions before Incus adds a thin
# volume to raft-disks. Arguments: location (for messages), the requested own-disk
# size in GiB (0 for none), the source: image:FINGERPRINT, BOX or BOX/SNAPSHOT,
# and the origin sentence for refusals (empty when the size is a flag).
# A new volume starts as a thin snapshot of its source and is then resized to the
# requested size, so it needs the larger of the two. Copies of shared-pool boxes
# without --disk never touch raft-disks and need nothing.
RESERVE_BOX_DISK = (
    BOX_DISK_ACCOUNTING
    + """
location, requested, source, origin = sys.argv[1], int(sys.argv[2]) * GIB, sys.argv[3], sys.argv[4]
pool = box_disk_pool()
added = []
if source.startswith('image:'):
    if requested:
        image = pool['imageVolumeBytes'] if pool else 0
        added.append(max(requested, image))
        # Incus caches the image in the pool the first time an own-disk box needs it.
        volume = f'/1.0/storage-pools/{POOL}/volumes/image/{source[6:]}'
        if pool and subprocess.run(['incus', 'query', volume], capture_output=True).returncode:
            added.append(image)
else:
    path = '/1.0/instances/' + source.replace('/', '/snapshots/') + '?project=raft'
    root = query(path)['expanded_devices']['root']
    if root.get('pool') == POOL:
        added.append(max(requested, size_bytes(root['size'])))
    elif requested:
        added.append(requested)
reserve(location, pool, added, origin)
"""
)

# Prints the pool `raft recover` imports into, from backup/index.yaml, the first
# entry of every Incus export. Own-disk backups return to raft-disks with their
# recorded size after the same reservation check: Incus restores the box and each
# of its snapshots as a thin volume of that size. Other backups use the shared pool.
RECOVERY_POOL = (
    BOX_DISK_ACCOUNTING
    + "SHARED_POOL = "
    + repr(SHARED_POOL)
    + """
import tarfile, yaml
archive, location = sys.argv[1], sys.argv[2]
with tarfile.open(archive, 'r|gz') as backup:
    entry = backup.next()
    if entry is None or entry.name != 'backup/index.yaml':
        sys.exit('Not an Incus instance backup: backup/index.yaml is not its first entry')
    index = yaml.safe_load(backup.extractfile(entry))
if index.get('pool') != POOL:
    print(SHARED_POOL)
    sys.exit()
size = size_bytes(index['config']['container']['expanded_devices']['root']['size'])
reserve(location, box_disk_pool(), [size] * (1 + len(index.get('snapshots') or [])))
print(POOL)
"""
)


def reserve_box_disk(location, disk, source, origin=""):
    """Shell line that refuses an own-disk volume raft-disks could not hold in full."""
    size = str(gib(disk) if disk else 0)
    return shlex.join(["python3", "-c", RESERVE_BOX_DISK, location, size, source, origin]) + "\n"


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
    # LVM sizes thin pool metadata for every data block mapped, and reservations
    # keep mappings within that, so this should never fire. It stays because full
    # metadata stops every own-disk box and needs offline repair.
    own = host["boxDiskPool"]
    if own is not None and (own["metadataUsedPercent"] or 0) >= 80:
        warnings.append(
            f"{BOX_DISK_POOL} thin pool metadata is {own['metadataUsedPercent']:.0f}% full; "
            "inspect it with lvs before creating more own-disk boxes"
        )
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


def host_read(location, script):
    """Run a read-only host probe without taking the lifecycle/expiry lock."""
    response = subprocess.run(
        [
            "ssh",
            "-n",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            settings(location)["ssh"],
            shlex.join(["sudo", "-n", "python3", "-c", script]),
        ],
        capture_output=True,
        text=True,
    )
    if response.returncode:
        raise RuntimeError(f"{location}: {response.stderr.strip()}")
    return response.stdout


def host_inventory(location):
    # Keep both native reads on the host: network latency costs more than JSON
    # decoding. Do not lock read-only inventory behind a long backup or creation.
    report = json.loads(host_read(location, HOST_INVENTORY))
    policy = parse_host_policy(location, report["policy"])
    boxes = [box for box in report["boxes"] if box["config"].get("user.raft") == "true"]
    return policy, boxes


def host_capacity(location):
    resources, boxes = host_read(location, HOST_RESOURCES + HOST_BOXES).splitlines()
    boxes = [box for box in json.loads(boxes) if box["config"].get("user.raft") == "true"]
    return {
        "location": location,
        "note": settings(location).get("note", ""),
        **capacity(json.loads(resources), boxes),
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
    print(
        f"Disk free: {host['sharedPool']['freeBytes'] / GIB:.1f} of "
        f"{host['sharedPool']['totalBytes'] / GIB:.1f} GiB shared pool, "
        f"{host['hostDiskFreeBytes'] / GIB:.1f} GiB host"
    )
    own = host["boxDiskPool"]
    if own is None:
        print("Own box disks: not enabled (redeploy with --disk-pool SIZE)")
    else:
        print(
            f"Own box disks: {(own['totalBytes'] - own['reservedBytes']) / GIB:.1f} of "
            f"{own['totalBytes'] / GIB:.1f} GiB unreserved"
        )
    print("CPU  RAM GiB  Fit on empty host  Fit now")
    for size in report["estimates"]:
        print(
            f"{size['cpu']:>3}  {size['memoryGiB']:>7}  {size['fitEmptyHost']:>17}  "
            f"{size['fitNow']:>7}"
        )
    print(
        "Counts assume every box uses its full CPU and RAM at once. Boxes share host CPUs, "
        "so more can run when they are not all busy. Any whole CPU count and GiB size works."
    )
    for warning in report["warnings"]:
        print("Warning: " + warning)


def doctor(location):
    # Health checks are sequential on the host, not separate SSH handshakes.
    # Only the worker compatibility check takes the lifecycle lock, as before.
    script = "import json, subprocess, sys\nworker_check = " + repr(CHECK_WORKER) + "\n"
    script += (
        f"project = {PROJECT!r}\nshared_pool = {SHARED_POOL!r}\nbox_disk_pool = {BOX_DISK_POOL!r}\n"
    )
    script += """
def checked(*command):
    response = subprocess.run(command, capture_output=True, text=True)
    if response.returncode:
        sys.exit(response.stderr.strip() or response.stdout.strip())
    return response.stdout.strip()
print(checked('incus', '--project', project, 'version'))
checked('flock', '/run/lock/raft-incus.lock', 'sh', '-ec', worker_check)
print(checked('systemctl', 'is-active', 'incus', 'raft-expire.timer', 'raft-network.service'))
print(checked('df', '-h', '/'))
print(checked('incus', '--project', project, 'storage', 'info', shared_pool))
pools = checked('incus', '--project', project, 'storage', 'list', '--format', 'csv', '-c', 'n').split()
if box_disk_pool in pools:
    print(checked('incus', '--project', project, 'storage', 'info', box_disk_pool))
else:
    print('own box disks: not enabled (redeploy with --disk-pool SIZE)')
boxes = json.loads(checked('incus', '--project', project, 'list', '--fast', '--format', 'json'))
print(str(sum(box['config'].get('user.raft') == 'true' for box in boxes))
      + ' saved boxes; system containers (shared host kernel)')
"""
    print(location + ": ", end="", flush=True)
    print(host_read(location, script), end="")


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


def new(args, *, run_id=None, on_allocate=None):
    box_fields = box_note(args)
    # Sizes are filled here only for boxes from the image; a box from a snapshot
    # keeps the snapshot's CPU, RAM and disk unless a flag overrides them.
    from_config = apply_new_defaults(args)
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
    require_note(args.location, box_fields, "new")
    name = "rf-" + uuid.uuid4().hex[:16]
    if on_allocate is not None:
        on_allocate(args.location, name)
    # Initialize before starting, so a failed start still has its intended policy.
    transaction(
        args.location,
        (raft_run.creation_guard(run_id) if run_id else "")
        + (CHECK_WORKER if args.disposable else "")
        + fits_host(args)
        # Plain boxes from the image stay off raft-disks and skip the check.
        + (
            reserve_box_disk(
                args.location,
                args.disk,
                source if args.template else "image:" + source,
                # A refusal of a configured disk says where the size came from.
                disk_origin(args.disk) if "disk" in from_config else "",
            )
            if args.disk or args.template
            else ""
        )
        + 'lifetime="$1"; name="$2"; note="$3"; shift 3\n'
        + READ_NOTE_POLICY
        + CHECK_NOTE
        # Always set the note, so a box created from a snapshot never inherits the
        # source note. The host checks the same value it writes.
        + '\n"$@" -c user.raft.note="$note"'
        + (' -c user.raft.expires="$(( $(date +%s) + lifetime ))"' if run_id else "")
        + '; incus --project raft start "$name"; '
        + (
            ""
            if run_id
            else 'incus --project raft config set "$name" '
            'user.raft.expires="$(( $(date +%s) + lifetime ))"'
        ),
        str(args.ttl),
        name,
        json.dumps(box_fields) if box_fields else "",
        "incus",
        "--project",
        PROJECT,
        "copy" if args.template else "init",
        source,
        name,
        "-c",
        "user.raft=true",
        *([] if run_id else ["-c", "user.raft.expires="]),
        *(["-c", f"user.raft.run={run_id}"] if run_id else []),
        "-c",
        "user.raft.stopped-at=",
        "-c",
        f"user.raft.disposable={'true' if args.disposable else 'false'}",
        "-c",
        "volatile.eth0.hwaddr=",
        *sizing(args),
        # Without --disk, init uses the profile's shared pool and copy keeps the
        # snapshot's disk: the shared pool or an own disk of the same size. Copies
        # within either pool are snapshots that share the source's blocks.
        *(own_disk(args.disk) if args.disk else []),
    )
    return f"{args.location}:{name}"


def execute(location, name, command, *, capture=False, cwd="/workspace"):
    return remote(location, ["exec", name, "--cwd", cwd, "--", *command], capture=capture)


def transfer_tree(args, location, name):
    worker = Path(raft_files.__file__).read_text()
    with tempfile.TemporaryFile() as archive:
        if args.action == "upload":
            raft_files.pack_directory(args.source, archive, include=getattr(args, "include", None))
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
    box_fields = box_note(args)
    host = settings(args.location)["ssh"]
    if box_fields:
        # Without a new note the archived one applies; the host checks it after import.
        require_note(args.location, box_fields, f"recover {args.source} --location {args.location}")
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
        # The archive carries the source note, known only after import. A rejected
        # note deletes the imported box inside the same locked transaction.
        transaction(
            args.location,
            # The reservation check runs first and creates nothing when it refuses.
            'pool=$(python3 -c "$3" "$1" "$4")\n'
            + READ_NOTE_POLICY
            + 'incus --project raft import "$1" "$2" --storage "$pool" '
            "-c user.raft=true -c user.raft.expires= -c user.raft.disposable=false -c user.raft.stopped-at= -c volatile.eth0.hwaddr=\n"
            'if [ -n "$5" ]; then incus --project raft config set "$2" user.raft.note="$5"; fi\n'
            'note=$(incus --project raft config get "$2" user.raft.note)\n'
            + CHECK_NOTE
            + ' || { incus --project raft delete "$2" --force; exit 1; }',
            staged,
            name,
            RECOVERY_POOL,
            args.location,
            json.dumps(box_fields) if box_fields else "",
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


def location_name(value):
    if not re.fullmatch(LOCATION_NAME, value):
        raise ValueError("Location names must use letters, digits, underscores or hyphens")
    return value


def boolean(value):
    """Parse a true or false setting, e.g. raft config set disposable true."""
    if value not in {"true", "false"}:
        raise argparse.ArgumentTypeError("Use true or false")
    return value == "true"


def shown_default(value):
    """Print a default as it is typed: true and false, not Python's True and False."""
    return json.dumps(value) if isinstance(value, bool) else value


# Options of new that ~/.config/raft/config.json can default, with their built-in
# defaults and value checks. Values go through str() first, so a configured value
# is accepted exactly when the same text passed as the flag would be. Adding a
# defaultable option means adding one entry here and keeping its flag default None.
NEW_DEFAULTS = {
    # None means the first configured location.
    "location": (None, location_name),
    "ttl": (600, ttl),
    "cpu": (DEFAULT_CPU, cpu_count),
    "memory": (DEFAULT_MEMORY, gib_size),
    "disk": ("shared", disk_size),
    # new --disposable and --persistent override it. Unlike the sizes it applies
    # to new --from too: a snapshot carries no lifecycle, new always sets one.
    "disposable": (False, boolean),
}
# A box from a snapshot keeps the snapshot's size, so these defaults, configured
# or built-in, apply only to boxes created from the image.
SIZE_DEFAULTS = {"cpu", "memory", "disk"}


def default_value(key, value):
    """Validate one configured default; errors name the file and the key."""
    if key not in NEW_DEFAULTS:
        raise ValueError(
            f"{DEFAULTS_CONFIG}: unknown key defaults.{key}; "
            "supported keys: " + ", ".join(NEW_DEFAULTS)
        )
    try:
        # JSON true and false read back as the true and false text the CLI accepts.
        return NEW_DEFAULTS[key][1](str(shown_default(value)))
    except (ValueError, argparse.ArgumentTypeError) as error:
        raise ValueError(
            f"{DEFAULTS_CONFIG}: defaults.{key}={value!r} is invalid: {error}"
        ) from None


def configured_defaults(unchecked=None):
    """Return the validated defaults from config.json; a missing file means none.

    The unchecked key is returned as stored, so `raft config set/unset KEY` can
    repair a file whose only problem is that key.
    """
    if not DEFAULTS_CONFIG.exists():
        return {}
    try:
        document = json.loads(DEFAULTS_CONFIG.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{DEFAULTS_CONFIG}: invalid JSON: {error}") from None
    if (
        not isinstance(document, dict)
        or set(document) - {"defaults"}
        or not isinstance(document.get("defaults", {}), dict)
    ):
        raise ValueError(f'{DEFAULTS_CONFIG}: expected {{"defaults": {{"KEY": VALUE, ...}}}}')
    return {
        key: value if key == unchecked else default_value(key, value)
        for key, value in document.get("defaults", {}).items()
    }


def apply_new_defaults(args):
    """Fill omitted new options: explicit flag, then config.json, then the built-in default.

    Returns the keys whose value came from config.json. With --from only explicit
    flags change the size: the snapshot's CPU, RAM and disk beat any default.
    """
    configured = configured_defaults()
    used = set()
    for key, (builtin, _) in NEW_DEFAULTS.items():
        # getattr's default lets internal callers such as smoke_test omit options.
        if key == "location" or getattr(args, key, None) is not None:
            continue
        if args.template and key in SIZE_DEFAULTS:
            continue
        if key in configured:
            used.add(key)
        setattr(args, key, configured.get(key, builtin))
    if args.disk == "shared":
        if args.template:
            raise ValueError(
                "--from keeps the snapshot's disk; leave out --disk, or give --disk SIZE "
                "for an own disk of that size"
            )
        # The rest of new() spells the shared pool as no own disk.
        args.disk = None
    # A --from template fixes the location, so only plain creation takes a default.
    if args.location is None and not args.template:
        if "location" not in configured:
            args.location = next(iter(configuration()))
        elif configured["location"] in configuration():
            args.location = configured["location"]
            used.add("location")
        else:
            raise ValueError(
                f"{DEFAULTS_CONFIG}: defaults.location names unknown location "
                f"{configured['location']}; run raft config set location NAME or "
                "raft config unset location"
            )
    return used


def effective_defaults():
    """Return each defaultable option's effective value and where it comes from."""
    configured = configured_defaults()
    report = {}
    for key, (builtin, _) in NEW_DEFAULTS.items():
        if key in configured:
            report[key] = {"value": configured[key], "source": "config"}
        else:
            value = next(iter(configuration())) if key == "location" else builtin
            report[key] = {"value": value, "source": "built-in"}
    return report


def disk_origin(size):
    """Explain a reservation refusal for an own disk that config.json chose, not a flag."""
    return (
        f"The {size} own disk comes from defaults.disk in {DEFAULTS_CONFIG}; "
        "give --disk shared or --disk SIZE, or run raft config unset disk"
    )


def set_default(key, value):
    """Set (value) or remove (None) one default in config.json and print the change."""
    if value is not None:
        value = default_value(key, value)
        if key == "location":
            settings(value)
    previous = {}

    def change(document):
        defaults = document.setdefault("defaults", {})
        previous.update(defaults)
        if value is None:
            defaults.pop(key, None)
        else:
            defaults[key] = value
        if not defaults:
            document.pop("defaults")

    edit_json(DEFAULTS_CONFIG, lambda: {"defaults": configured_defaults(key)}, change)
    before = shown_default(previous.get(key, "built-in default"))
    print(
        f"defaults.{key}: {before} -> {'built-in default' if value is None else shown_default(value)}"
    )


# GitHub releases are the one source of version truth. The release workflow
# publishes npm before it creates the GitHub release, so every version this API
# reports is already installable from both supported channels.
RELEASES_API = "https://api.github.com/repos/Microck/raft/releases"
# Matches the user agent deploy/deploy.py sends for release downloads.
USER_AGENT = "OpenAI File Downloader, XaiImageApiFetch/1.0"
# self-update --check exit status when a newer release exists, as dnf check-update uses.
UPDATE_AVAILABLE = 100
UPDATE_CHECK_INTERVAL = 24 * 3600
UPDATE_CHECK_TIMEOUT = 2


def parse_version(text):
    """Return an X.Y.Z release version, optionally v-prefixed, as a comparable tuple."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        raise ValueError(f"Not a release version: {text}")
    return tuple(int(part) for part in match.groups())


def release_version(value):
    """Parser type for self-update --version; returns the version without its v."""
    parse_version(value)
    return value.removeprefix("v")


def fetch(url, timeout):
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    return urllib.request.urlopen(request, timeout=timeout)


def release(api, version=None, timeout=30):
    """Return GitHub metadata for the latest release, or for release vVERSION."""
    try:
        with fetch(f"{api}/tags/v{version}" if version else api + "/latest", timeout) as response:
            metadata = json.load(response)
    except urllib.error.HTTPError as error:
        if version and error.code == 404:
            raise ValueError(f"No published Raft release v{version}") from error
        raise
    metadata["version"] = metadata["tag_name"].removeprefix("v")
    parse_version(metadata["version"])
    return metadata


def update_message(latest):
    return f"raft {latest} is available (you have {__version__}). Run: raft self-update"


def update_cache():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "raft/update.json"


def save_update_check():
    """Record a release check. A torn write only makes the next command check again."""
    path = update_cache()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checkedAt": time.time()}))
    except OSError:
        pass


def update_check(action, api=RELEASES_API):
    """Start the passive release check for a command; the returned function prints its notice.

    At most once per UPDATE_CHECK_INTERVAL, a background thread asks GitHub for
    the latest release while the command runs, and only that command can print
    the notice, so a controller sees it at most once per interval. The notice is
    one stderr line, so stdout stays parseable. It never installs anything, and
    network, thread or cache failures stay silent. RAFT_NO_UPDATE_CHECK=1
    disables it.
    """
    if action == "self-update" or os.environ.get("RAFT_NO_UPDATE_CHECK"):
        return lambda: None
    try:
        checked = float(json.loads(update_cache().read_text())["checkedAt"])
    except (OSError, ValueError, KeyError, TypeError):
        checked = 0
    # A timestamp in the future, e.g. after a clock change, counts as stale.
    if 0 <= time.time() - checked < UPDATE_CHECK_INTERVAL:
        return lambda: None
    # Record the attempt first: an offline controller then waits for at most one
    # timeout per interval instead of one per command.
    save_update_check()
    found = {}

    def check():
        try:
            found["latest"] = release(api, timeout=UPDATE_CHECK_TIMEOUT)["version"]
        except Exception:
            # A best-effort notice must not print tracebacks or fail commands.
            pass

    thread = threading.Thread(target=check, daemon=True)
    try:
        thread.start()
    except RuntimeError:
        # The runtime could not create the thread; skip this interval's notice.
        return lambda: None

    def notice():
        # Commands usually outlast the request; otherwise wait a bounded time.
        thread.join(UPDATE_CHECK_TIMEOUT)
        latest = found.get("latest")
        if latest is not None and parse_version(latest) > parse_version(__version__):
            print(update_message(latest), file=sys.stderr)

    return notice


def install_channel(source, prefix):
    """Return (channel, root) for the installation holding source, the resolved raft.py.

    self-update manages npm global installs and uv tools. Any other layout raises
    ValueError with instructions, so an update never moves to another channel.
    """
    package = source.parent
    if (package / ".git").exists():
        raise ValueError(
            f"raft runs from the source checkout {package}; update it with "
            "`git fetch --tags && git checkout vX.Y.Z` and `uv sync --locked`"
        )
    # A global install puts the package at PREFIX/lib/node_modules/@microck/raft and
    # links PREFIX/bin/raft to raft.py. Project-local node_modules have no such link.
    if package.parts[-4:] == ("lib", "node_modules", "@microck", "raft"):
        npm_prefix = package.parents[3]
        if (npm_prefix / "bin/raft").resolve() == source:
            return "npm", npm_prefix
    # uv names each tool environment after its package and leaves a receipt in it.
    # Editable installs keep raft.py outside the environment and are excluded.
    if prefix.name == "raft" and (prefix / "uv-receipt.toml").exists():
        if source.is_relative_to(prefix):
            return "uv", prefix
    raise ValueError(
        f"self-update manages npm global installs and uv tools; {source} is neither. "
        "Reinstall with the tool you used, from a release verified against its SHA256SUMS: "
        "https://github.com/Microck/raft/releases"
    )


def verified_wheel(metadata, directory):
    """Download the release wheel and check it against the release SHA256SUMS."""
    name = f"raft-{metadata['version']}-py3-none-any.whl"
    assets = {asset["name"]: asset["browser_download_url"] for asset in metadata["assets"]}
    for asset in [name, "SHA256SUMS"]:
        if asset not in assets:
            raise ValueError(
                f"Release {metadata['tag_name']} has no {asset}; nothing was installed"
            )
        with fetch(assets[asset], 60) as response, (directory / asset).open("wb") as file:
            shutil.copyfileobj(response, file)
    expected = [
        line.split()[0]
        for line in (directory / "SHA256SUMS").read_text().splitlines()
        if line.split()[1:] == [name]
    ]
    with (directory / name).open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if expected != [digest]:
        raise ValueError(f"{name} does not match the release SHA256SUMS; nothing was installed")
    return directory / name


def self_update(args, api=RELEASES_API):
    """Install the latest or the requested release through the channel that installed raft."""
    if args.check:
        latest = release(api)["version"]
        save_update_check()
        if parse_version(latest) > parse_version(__version__):
            print(update_message(latest))
            return UPDATE_AVAILABLE
        print(f"raft {__version__} is up to date (latest release: {latest})")
        return 0
    if args.version is not None and parse_version(args.version) == parse_version(__version__):
        print(f"raft {__version__} is already installed")
        return 0
    # Refuse unmanaged layouts before any download.
    channel, root = install_channel(Path(__file__).resolve(), Path(sys.prefix).resolve())
    tool = shutil.which(channel)
    if tool is None:
        raise ValueError(f"{channel} installed raft but is not on PATH")
    metadata = release(api, args.version)
    target = metadata["version"]
    # Without --version, never downgrade a build that is newer than the latest release.
    if args.version is None and parse_version(target) <= parse_version(__version__):
        print(f"raft {__version__} is up to date (latest release: {target})")
        return 0
    # Installer output goes to stderr; stdout carries only the result below.
    if channel == "npm":
        # An explicit prefix keeps the update in this install even when another
        # Node.js installation provides npm. The registry checks package integrity.
        subprocess.run(
            [tool, "install", "--global", "--prefix", str(root), f"@microck/raft@{target}"],
            stdout=sys.stderr,
            check=True,
        )
    else:
        tools = subprocess.run([tool, "tool", "dir"], capture_output=True, text=True, check=True)
        if Path(tools.stdout.strip()).resolve() != root.parent:
            raise ValueError(
                f"uv now installs tools in {tools.stdout.strip()}, not {root.parent}; "
                "set UV_TOOL_DIR to the original directory, then retry"
            )
        with tempfile.TemporaryDirectory(prefix="raft-update-") as work:
            wheel = verified_wheel(metadata, Path(work))
            print(f"Verified {wheel.name} against the release SHA256SUMS", file=sys.stderr)
            subprocess.run(
                [tool, "tool", "install", "--force", str(wheel)], stdout=sys.stderr, check=True
            )
    installed = subprocess.run(
        [str(root / "bin/raft"), "--version"], capture_output=True, text=True, check=True
    )
    if installed.stdout.strip() != target:
        raise RuntimeError(f"{channel} installed {installed.stdout.strip()}, not {target}")
    print(f"raft {__version__} -> {target}")
    print("Release notes: " + metadata["html_url"])
    print(
        "Hosts were not changed. Run `raft doctor`: if a host's worker differs from this CLI, "
        f"redeploy that host from release {metadata['tag_name']}. "
        f"`raft host add` now imports {metadata['tag_name']} images by default."
    )
    return 0


def note_options(command):
    """Add the box note template flags shared by new, note, fork and recover."""
    command.add_argument(
        "--purpose", help="Box note: what the box is for, e.g. 'Integration tests for PR 42'"
    )
    command.add_argument(
        "--owner", help="Box note: who uses the box, e.g. 'codex session in ~/work/app'"
    )
    command.add_argument(
        "--next", help="Box note: what happens next, e.g. 'Destroy after the tests pass'"
    )


def creation_options(command, *, lifecycle=True):
    command.add_argument("--location", default=None)
    # Omitted options take ~/.config/raft/config.json defaults, then NEW_DEFAULTS.
    # With --from, every omitted size comes from the snapshot instead of a default.
    if lifecycle:
        command.add_argument(
            "--ttl", type=ttl, help="Lifetime in seconds, 60 to 2592000 (default: config, else 600)"
        )
    command.add_argument(
        "--cpu",
        type=cpu_count,
        help=f"CPU limit (default: config, else {DEFAULT_CPU}; with --from, the snapshot's)",
    )
    command.add_argument(
        "--memory",
        type=gib_size,
        help="RAM limit, e.g. 16GiB "
        f"(default: config, else {DEFAULT_MEMORY}; with --from, the snapshot's)",
    )
    command.add_argument(
        "--disk",
        type=disk_size,
        metavar="SIZE",
        help="Give the box its own disk of SIZE, e.g. 20GiB, that it cannot grow past, "
        "or shared for the shared pool "
        "(default: config, else shared; with --from, the snapshot's disk)",
    )
    if lifecycle:
        policy = command.add_mutually_exclusive_group()
        policy.add_argument(
            "--disposable",
            dest="disposable",
            action="store_const",
            const=True,
            help="Delete on stop or expiry (default: config, else persistent)",
        )
        policy.add_argument(
            "--persistent",
            dest="disposable",
            action="store_const",
            const=False,
            help="Keep the disk on stop or expiry, despite a disposable default",
        )
    command.add_argument(
        "--from",
        dest="template",
        metavar="LOCATION:BOX/SNAPSHOT",
        help="Copy a named snapshot on its source host, including filesystem secrets",
    )
    note_options(command)


def parser():
    cli = raft_output.Parser(description=__doc__)
    cli.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit JSONL data, byte-stream, error and exit events; use before the command",
    )
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="action", required=True)
    complete = commands.add_parser("completions", help="Print parser-driven shell completion setup")
    complete.add_argument("shell", choices=["bash", "zsh", "fish"])
    query = commands.add_parser("_complete")
    query.add_argument("words", nargs=argparse.REMAINDER)
    create = commands.add_parser(
        "new",
        help="Create a workspace and print its location-qualified handle",
        description="Create from the pinned image or a same-host named snapshot. "
        "Prints the handle before guest services are necessarily ready. "
        "Inspect config for defaults and required notes, and capacity for host resources. "
        "Disposable boxes are deleted on stop or expiry; persistent disks remain subject to host retention.",
    )
    creation_options(create)
    run = commands.add_parser("run", help="Run a command in a temporary CI box and clean up")
    creation_options(run, lifecycle=False)
    raft_run.add_options(run)
    commands.add_parser("locations", help="List configured locations and their notes")
    defaults = commands.add_parser(
        "config", help="Show defaults for new and each location's note policy, or change defaults"
    )
    defaults.add_argument("--location", help="Read policy only from this configured host")
    default_commands = defaults.add_subparsers(dest="config_action")
    default_set = default_commands.add_parser("set", help="Set a default for new")
    default_set.add_argument("key", choices=list(NEW_DEFAULTS))
    default_set.add_argument("value")
    default_unset = default_commands.add_parser("unset", help="Restore a built-in default")
    default_unset.add_argument("key", choices=list(NEW_DEFAULTS))
    policy = commands.add_parser(
        "policy",
        help="Set a location's required box note fields and stopped-box retention",
    )
    policy.add_argument("location")
    policy.add_argument(
        "--require",
        metavar="FIELDS|none",
        help="Note fields every box must carry, e.g. purpose,owner; none stops requiring them",
    )
    policy.add_argument(
        "--delete-stopped-after",
        metavar="HOURS|never",
        help="Delete boxes stopped for HOURS, e.g. 48; never keeps them",
    )
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
        help="Enable own box disks: set aside SIZE of host disk for them, e.g. 40GiB "
        "(default: not enabled)",
    )
    listing = commands.add_parser(
        "list",
        help="List boxes as JSONL, including ownership notes and retention deadlines",
        description="List boxes as JSONL. Read note before using a box; deleteAt is its stopped-box deletion deadline, or empty when none is due.",
    )
    listing.add_argument("--location")
    report = commands.add_parser("capacity", help=ADVISORY, description=ADVISORY)
    report.add_argument("--location")
    report.add_argument("--json", action="store_true")
    doctor_command = commands.add_parser("doctor", help="Check host services, worker and storage")
    doctor_command.add_argument("--location", help="Inspect only this configured host")
    gc = commands.add_parser(
        "gc", help="Enforce deadlines and retention, including disposable deletion"
    )
    gc.add_argument("--location")
    prune = commands.add_parser("prune", help="Preview deletion of old stopped boxes")
    prune.add_argument("--location")
    prune.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    prune.add_argument("--yes", action="store_true", help="Delete the matching boxes")
    recovery = commands.add_parser("recover", help="Import a portable backup as a new stopped box")
    recovery.add_argument("source")
    recovery.add_argument("--location", required=True)
    note_options(recovery)
    update = commands.add_parser(
        "self-update", help="Upgrade this CLI to the latest or a chosen release"
    )
    target = update.add_mutually_exclusive_group()
    target.add_argument(
        "--check",
        action="store_true",
        help=f"Only report whether a newer release exists; exit {UPDATE_AVAILABLE} if one does",
    )
    target.add_argument(
        "--version",
        type=release_version,
        metavar="X.Y.Z",
        help="Install this release instead of the latest, e.g. to roll back",
    )
    for action, description in {
        "info": "Read native box metadata as JSON without starting guest processes.",
        "stop": "Stop processes in a persistent box and retain its disk. Disposable boxes and their snapshots are deleted. Inspect info before stopping.",
        "resume": "Start a stopped persistent box with a new lifetime. Optional CPU and memory replace only those limits. Process memory is not restored.",
        "extend": "Reset the deadline of a running box from the host clock without restarting it. Use resume for stopped boxes.",
        "destroy": "Delete a box and all its snapshots, then confirm absence. Only destroy boxes owned by this task unless explicitly authorized.",
        "ssh": "Open a root shell through host SSH and Incus exec. This does not expose a guest SSH port.",
        "exec": "Run literal command arguments in /workspace and retain the exit status. Use an explicit shell for pipelines or expansions. Detached execution returns a job ID for status, logs and cancel.",
        "upload": "Copy a local file to an absolute guest path. Recursive transfers require a running guest and publish a new directory without merging or overwriting; escaping links and special files are rejected.",
        "download": "Copy an absolute guest path to a local file. Recursive transfers require a running guest and a new destination directory; the parent must exist. Failed downloads do not replace existing files.",
        "snapshot": "Save a named disk snapshot of a stopped persistent box. Snapshots disappear when their box is deleted, including by retention.",
        "snapshots": "List named snapshots through native Incus info.",
        "snapshot-delete": "Delete one named snapshot permanently without deleting its box.",
        "restore": "Replace disk contents from a named snapshot of a stopped box. Keep current instance configuration, resource limits and MAC address. Requires an explicitly requested rollback.",
        "fork": "Copy a stopped box on the same host, including files, secrets and note. Defaults to persistent. CPU and memory retain source limits unless overridden. Update the copy note for its new task.",
        "forward": "Open a foreground SSH tunnel bound to controller 127.0.0.1. Guest service must listen on its managed NIC or 0.0.0.0. Ctrl+C closes the tunnel.",
        "desktop": "Start the full guest desktop and open its private foreground tunnel. Closing the tunnel leaves the desktop service running until explicitly stopped or the box stops.",
        "logs": "Print journal output for a detached job in this running box.",
        "status": "Print systemd state and exit status for a detached job in this running box.",
        "cancel": "Stop a detached job and its child processes. Journal history remains available while retained by the guest.",
        "backup": "Export a stopped persistent box and its snapshots to a new private archive. Includes credentials. The host lifecycle lock delays expiry while exporting.",
        "usage": "Report running guest cgroup CPU and memory counters and filesystem space as JSON. filesystemScope distinguishes shared storage from an own disk.",
    }.items():
        command = commands.add_parser(
            action, help=description.split(". ")[0], description=description
        )
        command.add_argument("box", help="Exact LOCATION:BOX handle returned by creation")
        if action in ["resume", "fork", "extend"]:
            command.add_argument(
                "--ttl", type=ttl, required=True, help="Lifetime in seconds, 60 to 2592000"
            )
        if action in ["resume", "fork"]:
            command.add_argument("--cpu", type=cpu_count)
            command.add_argument("--memory", type=gib_size)
        if action == "fork":
            command.add_argument("--disposable", action="store_true")
            note_options(command)
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
    if getattr(args, "json_output", False) and args.action in {"ssh", "forward", "desktop"}:
        raise ValueError("JSON mode does not support interactive shells or foreground tunnels")
    if args.action == "completions":
        print(raft_output.completions(args.shell), end="")
        return 0
    if args.action == "run":
        return raft_run.run(args, sys.modules[__name__])
    if args.action == "self-update":
        return self_update(args)
    if args.action == "recover":
        recover(args)
        return 0
    if args.action == "new":
        print(new(args))
        return 0
    if args.action == "host":
        host_add(args)
        return 0
    if args.action == "config":
        if args.config_action is None:
            report = {"file": str(DEFAULTS_CONFIG), "defaults": effective_defaults(), "policy": {}}
            locations = [args.location] if args.location else configuration()
            for location in locations:
                try:
                    report["policy"][location] = host_policy(location)
                except RuntimeError as error:
                    raise RuntimeError(f"{location}: {error}") from error
            raft_output.data(report, indent=2)
        else:
            if args.location:
                raise ValueError(
                    "--location selects a host policy report; defaults are controller-wide"
                )
            set_default(args.key, args.value if args.config_action == "set" else None)
        return 0
    if args.action == "policy":
        change_policy(args)
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
        require_note(
            location,
            fields,
            f"note {args.target}",
            " Clearing would leave the box without them." if args.clear else "",
        )
        # Each call replaces the whole note; an empty value removes the key.
        transaction(
            location,
            'note="$2"\n'
            + READ_NOTE_POLICY
            + CHECK_NOTE
            + '\nincus --project raft config set "$1" user.raft.note="$note"',
            name,
            "" if args.clear else json.dumps(fields),
        )
        return 0
    if args.action == "locations":
        for index, (location, entry) in enumerate(configuration().items()):
            record = {"location": location, "default": index == 0, "note": entry.get("note", "")}
            raft_output.data(record)
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
                if args.json or args.json_output:
                    raft_output.data(report)
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
                        raft_output.data(record)
                else:
                    print(output, end="")
            elif args.action == "doctor":
                doctor(location)
            else:
                policy, boxes = host_inventory(location)
                for box in boxes:
                    deletion = delete_at(box, policy)
                    raft_output.data(
                        {
                            "box": location + ":" + box["name"],
                            "state": box["status"],
                            "stopAt": box["config"].get("user.raft.expires", ""),
                            "deleteAt": "" if deletion is None else str(deletion),
                            "disk": box_disk(box),
                            "note": read_box_note(box),
                        }
                    )
        return 0
    # Only fork accepts note flags here; check them before contacting the host.
    fields = box_note(args)
    location, name = parse_handle(args.box)
    box = instance(location, name)
    if args.action == "info":
        raft_output.data(box, indent=2)
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
        raft_output.data(usage)
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
                REQUIRE_STOPPED
                + reserve_box_disk(location, None, name)
                + 'incus --project raft snapshot create "$1" "$2"',
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
        # On raft-disks Incus swaps in a thin snapshot of the snapshot for the box's
        # volume, so a restore needs no reservation: it writes no data, and the
        # reserved total is unchanged once the old volume is removed.
        transaction(
            location,
            REQUIRE_STOPPED + 'incus --project raft snapshot restore "$1" "$2" --diskonly',
            name,
            args.name,
        )
    elif args.action == "fork":
        # Note flags replace the whole note, as with raft note; without them the
        # fork keeps the source note, which must then satisfy the host policy.
        require_note(
            location,
            fields or read_box_note(box),
            f"fork {args.box} --ttl {args.ttl}",
            "" if fields else " Forks keep the source note unless you give a new one.",
        )
        child = "rf-" + uuid.uuid4().hex[:16]
        transaction(
            location,
            (CHECK_WORKER if args.disposable else "")
            + REQUIRE_STOPPED
            + fits_host(args)
            + reserve_box_disk(location, None, name)
            + 'name="$2"; lifetime="$3"; source="$1"; note="$4"; shift 4\n'
            # Read the source note under the lock, so the check sees what the copy keeps.
            'if [ -z "$note" ]; then note=$(incus --project raft config get "$source" user.raft.note); fi\n'
            + READ_NOTE_POLICY
            + CHECK_NOTE
            + '\nincus --project raft copy "$source" "$name" --instance-only "$@" -c user.raft.note="$note"; '
            'incus --project raft start "$name"; '
            'incus --project raft config set "$name" user.raft.expires="$(( $(date +%s) + lifetime ))"',
            name,
            child,
            str(args.ttl),
            json.dumps(fields) if fields else "",
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
    # Only root options select this mode. A payload's --json is its own argument.
    root_options = []
    for argument in sys.argv[1:]:
        if not argument.startswith("-") or argument == "--":
            break
        root_options.append(argument)
    output = raft_output.JsonOutput().start() if "--json" in root_options else None
    status = 1
    notice = None
    try:
        cli = parser()
        args = cli.parse_args()
        if args.action == "_complete":
            words = args.words[1:] if args.words[:1] == ["--"] else args.words
            print("\n".join(raft_output.candidates(cli, words)))
            status = 0
            return status
        notice = (
            None
            if output or args.action in {"completions", "_complete"}
            else update_check(args.action, RELEASES_API)
        )
        status = dispatch(args)
    except raft_output.UsageError as error:
        raft_output.error("usage", error)
        status = 2
    except (
        OSError,
        ValueError,
        RuntimeError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as error:
        raft_output.error("operation", error)
        status = 1
    except SystemExit as error:
        status = error.code if isinstance(error.code, int) else 1
    except KeyboardInterrupt:
        status = 130
    finally:
        if notice and status != 130:
            notice()
        if output:
            output.finish(status)
    return status


if __name__ == "__main__":
    sys.exit(main())
