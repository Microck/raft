"""Self-hosted persistent Linux workspaces powered by Incus."""

import argparse
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
import uuid

import raft_files

CONFIG = Path.home() / ".config/raft/incus.json"
PROJECT = "raft"
SAVED_BOX_LIMIT = 4

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
    'filesystemScope': 'shared Raft pool, not per-box usage',
}))
"""


def configuration():
    inventory = json.loads(CONFIG.read_text())
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Configure at least one host in ~/.config/raft/incus.json")
    for location, entry in inventory.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", location):
            raise ValueError("Location names must use letters, digits, underscores or hyphens")
        if not isinstance(entry, dict) or not isinstance(entry.get("ssh"), str) or not entry["ssh"]:
            raise ValueError("Each configured location needs a nonempty ssh target")
    return inventory


def settings(location):
    inventory = configuration()
    if location not in inventory:
        raise ValueError(f"Unknown location: {location}")
    return inventory[location]


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


ADMISSION = f"""count=$(incus --project raft list --fast --format json | python3 -c 'import json,sys; print(sum(x["config"].get("user.raft")=="true" for x in json.load(sys.stdin)))')
test "$count" -lt {SAVED_BOX_LIMIT} || {{ echo 'Location already holds four boxes' >&2; exit 1; }}
"""


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
pool = json.loads(subprocess.check_output(['incus', 'query', '/1.0/storage-pools/raft-data/resources']))
print(json.dumps({
    'cpus': len(os.sched_getaffinity(0)),
    'memoryTotalBytes': int(memory['MemTotal:']) * 1024,
    'memoryAvailableBytes': int(memory['MemAvailable:']) * 1024,
    'hostDiskFreeBytes': disk.free,
    'poolTotalBytes': pool['space']['total'],
    'poolFreeBytes': pool['space']['total'] - pool['space']['used'],
}))
"""


def capacity(host, boxes):
    """Conservative allocation advice, not an exclusive CPU or memory reservation."""
    gib = 1024**3
    active = [box for box in boxes if box["status"] != "Stopped"]
    cpu_allocated = 0
    memory_allocated = 0
    for box in active:
        limits = box["expanded_config"]
        if limits["limits.cpu"] not in {"1", "2"} or limits["limits.memory"] not in {
            "1GiB",
            "2GiB",
            "4GiB",
        }:
            raise ValueError("Active box has unsupported sizing; inspect its Incus configuration")
        cpu_allocated += int(limits["limits.cpu"])
        memory_allocated += int(limits["limits.memory"][0]) * gib
    memory_reserve = max(2 * gib, host["memoryTotalBytes"] // 10)
    cpu_budget = max(0, host["cpus"] - 1)
    memory_budget = max(0, host["memoryTotalBytes"] - memory_reserve)
    cpu_remaining = max(0, cpu_budget - cpu_allocated)
    memory_remaining = max(
        0,
        min(
            memory_budget - memory_allocated,
            host["memoryAvailableBytes"] - memory_reserve,
        ),
    )
    slots = max(0, SAVED_BOX_LIMIT - len(boxes))
    recommendations = []
    for cpu in [1, 2]:
        for memory in [1, 2, 4]:
            total = min(SAVED_BOX_LIMIT, cpu_budget // cpu, memory_budget // (memory * gib))
            additional = min(
                max(0, SAVED_BOX_LIMIT - len(active)),
                cpu_remaining // cpu,
                memory_remaining // (memory * gib),
            )
            recommendations.append(
                {
                    "cpu": cpu,
                    "memoryGiB": memory,
                    "totalRunning": total,
                    "additionalRunning": additional,
                    "newBoxes": min(slots, additional),
                }
            )
    warnings = []
    if cpu_allocated > cpu_budget or memory_allocated > memory_budget:
        warnings.append("Active configured limits exceed the recommended host budget")
    if host["memoryAvailableBytes"] < memory_reserve:
        warnings.append(
            "Available host RAM is below the reserve; stop boxes or reduce other workloads"
        )
    if host["hostDiskFreeBytes"] < 5 * gib or host["poolFreeBytes"] < 5 * gib:
        warnings.append(
            "Host or shared-pool free disk is below 5 GiB; check image and workspace growth"
        )
    return {
        "host": host,
        "savedBoxLimit": SAVED_BOX_LIMIT,
        "savedBoxes": len(boxes),
        "activeBoxes": len(active),
        "savedSlots": slots,
        "allocated": {"cpus": cpu_allocated, "memoryBytes": memory_allocated},
        "reserve": {"cpus": 1, "memoryBytes": memory_reserve},
        "recommendations": recommendations,
        "warnings": warnings,
    }


def host_limits(location):
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
    return {"location": location, **capacity(json.loads(response.stdout), inventory(location))}


def print_limits(report):
    gib = 1024**3
    host = report["host"]
    print(
        f"{report['location']}: {host['cpus']} CPUs, "
        f"{host['memoryTotalBytes'] / gib:.1f} GiB RAM "
        f"({host['memoryAvailableBytes'] / gib:.1f} GiB available)"
    )
    print(
        f"Saved boxes: {report['savedBoxes']}/{report['savedBoxLimit']} enforced; "
        f"active: {report['activeBoxes']}; free saved slots: {report['savedSlots']}"
    )
    print(
        f"Active limits: {report['allocated']['cpus']} CPUs, "
        f"{report['allocated']['memoryBytes'] / gib:.1f} GiB; "
        f"host reserve: 1 CPU, {report['reserve']['memoryBytes'] / gib:.1f} GiB"
    )
    print(
        f"Disk free: {host['poolFreeBytes'] / gib:.1f} GiB shared pool, "
        f"{host['hostDiskFreeBytes'] / gib:.1f} GiB host"
    )
    print("CPU  RAM GiB  Total running  More running  New boxes")
    for size in report["recommendations"]:
        print(
            f"{size['cpu']:>3}  {size['memoryGiB']:>7}  {size['totalRunning']:>13}  "
            f"{size['additionalRunning']:>12}  {size['newBoxes']:>9}"
        )
    print("Advisory CPU/RAM counts; total assumes an empty host. Disk growth is not included.")
    for warning in report["warnings"]:
        print("Warning: " + warning)


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
        "ssh",
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


def new(args):
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
    name = "rf-" + uuid.uuid4().hex[:16]
    # Initialize before starting, so a failed start still has its intended policy.
    transaction(
        args.location,
        (CHECK_WORKER if args.disposable else "")
        + ADMISSION
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
        "volatile.eth0.hwaddr=",
        *sizing(args),
    )
    print(f"{args.location}:{name}")


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
            ADMISSION + 'incus --project raft import "$1" "$2" --storage raft-data '
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


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="action", required=True)
    create = commands.add_parser("new")
    create.add_argument("--location", default=None)
    create.add_argument("--ttl", type=ttl, default=600)
    create.add_argument("--cpu", type=int, choices=[1, 2], default=1)
    create.add_argument("--memory", choices=["1GiB", "2GiB", "4GiB"], default="2GiB")
    create.add_argument("--disposable", action="store_true", help="Delete on stop or expiry")
    create.add_argument("--from", dest="template", metavar="LOCATION:BOX/SNAPSHOT")
    listing = commands.add_parser("list")
    listing.add_argument("--location")
    limits = commands.add_parser("limits", help="Inspect host capacity and recommended box counts")
    limits.add_argument("--location")
    limits.add_argument("--json", action="store_true")
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
            command.add_argument("--cpu", type=int, choices=[1, 2])
            command.add_argument("--memory", choices=["1GiB", "2GiB", "4GiB"])
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
        new(args)
        return 0
    if args.action in ["list", "limits", "doctor", "gc", "prune"]:
        if args.action == "prune" and args.older_than < 1:
            raise ValueError("Retention must be at least one day")
        locations = configuration()
        if getattr(args, "location", None):
            locations = [args.location]
        for location in locations:
            if args.action == "limits":
                report = host_limits(location)
                if args.json:
                    print(json.dumps(report))
                else:
                    print_limits(report)
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
                print(location + ": " + incus(location, "version").strip())
                response = subprocess.run(
                    [
                        "ssh",
                        "-T",
                        settings(location)["ssh"],
                        "systemctl is-active incus raft-expire.timer raft-network.service && df -h /",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                print(response.stdout.strip())
                print(incus(location, "storage", "info", "raft-data").strip())
                print(
                    f"{len(inventory(location))} saved boxes; system containers (shared host kernel)"
                )
            else:
                for box in inventory(location):
                    print(
                        json.dumps(
                            {
                                "box": location + ":" + box["name"],
                                "state": box["status"],
                                "stopAt": box["config"].get("user.raft.expires", ""),
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
        print(response.stdout.decode().strip())
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
            REQUIRE_STOPPED + 'name="$1"; lifetime="$2"; shift 2; '
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
            + ADMISSION
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
