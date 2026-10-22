"""One-shot CI orchestration; guests are disposable containers, not registered runners."""

import argparse
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

GRACE = 30
LEASE_MARGIN = 120

# The caller holds the existing host lifecycle lock around this entire program.
CLEANUP = """import json, subprocess, sys
name, token = sys.argv[1:]
def boxes():
    result = subprocess.run(['incus', '--project', 'raft', 'list', name, '--format', 'json'],
                            check=True, stdout=subprocess.PIPE)
    return [box for box in json.loads(result.stdout) if box['name'] == name]
found = boxes()
if not found:
    sys.exit(0)
config = found[0].get('config', {})
if len(found) != 1 or config.get('user.raft') != 'true' or config.get('user.raft.run') != token:
    sys.exit('Refusing cleanup: box ownership does not match this run')
subprocess.run(['incus', '--project', 'raft', 'delete', name, '--force'], check=True)
if boxes():
    sys.exit('Run cleanup deletion was not confirmed')
"""


class Stopped(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def timeout_seconds(value):
    seconds = int(value)
    if not 1 <= seconds <= 2592000 - LEASE_MARGIN:
        raise argparse.ArgumentTypeError("Timeout must be 1 second to 30 days minus 120 seconds")
    return seconds


def add_options(command):
    command.add_argument(
        "--timeout",
        type=timeout_seconds,
        default=600,
        help="Total setup/upload/command deadline in seconds (default: 600)",
    )
    upload = command.add_mutually_exclusive_group()
    upload.add_argument(
        "--push",
        metavar="DIRECTORY",
        help="Upload Git-tracked and unignored files; exclude .git; reject submodules",
    )
    upload.add_argument(
        "--push-all",
        metavar="DIRECTORY",
        help="Explicitly upload the entire tree, including hidden and ignored files",
    )
    command.add_argument(
        "--cwd", help="Guest working directory (upload: /workspace/project; otherwise /workspace)"
    )
    command.add_argument(
        "--pull",
        nargs=2,
        action="append",
        default=[],
        metavar=("GUEST_DIRECTORY", "NEW_LOCAL_DIRECTORY"),
        help="Collect a directory before cleanup, including after a failed command",
    )
    command.add_argument(
        "command", nargs=argparse.REMAINDER, help="Command argv after --; not a shell string"
    )


def guest_path(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or "\0" in value:
        raise ValueError("Guest paths must be absolute and contain no '..' or NUL")
    return str(path)


def git_files(source):
    """Fail closed on Git errors; do not accidentally upload an ignored credential."""

    def git(*arguments):
        result = subprocess.run(
            ["git", "-C", str(source), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if result.returncode:
            raise ValueError(
                "--push requires a readable Git working tree; use --push-all only "
                "after checking the entire tree for secrets: "
                + result.stderr.decode(errors="replace").strip()
            )
        return result.stdout

    if git("rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise ValueError("--push requires a Git working tree")
    for record in git("ls-files", "--stage", "-z", "--", ".").split(b"\0"):
        if record.startswith(b"160000 "):
            raise ValueError("--push does not copy submodules; stage their contents explicitly")
    paths = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".")
    return {
        os.fsdecode(path)
        for path in paths.split(b"\0")
        if path and ".git" not in PurePosixPath(os.fsdecode(path)).parts
    }


def run(args, api):
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or any("\0" in value for value in command):
        raise ValueError("run requires a command after --, without NUL characters")
    source = (
        Path(args.push or args.push_all).resolve(strict=True)
        if args.push or args.push_all
        else None
    )
    if source is not None and not source.is_dir():
        raise ValueError("Upload source must be a directory")
    cwd = guest_path(args.cwd or ("/workspace/project" if source else "/workspace"))
    pulls = []
    destinations = set()
    for remote, local in args.pull:
        destination = Path(local).absolute()
        destination = destination.parent.resolve(strict=True) / destination.name
        if os.path.lexists(destination) or destination in destinations:
            raise ValueError("Each pull destination must be a new path: " + str(destination))
        destinations.add(destination)
        pulls.append((guest_path(remote), str(destination)))
    args.disposable = True
    args.ttl = args.timeout + LEASE_MARGIN
    token = uuid.uuid4().hex
    allocated = None
    started = False
    status = 1
    phase = "job"
    began = time.monotonic()
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)
    previous = {number: signal.getsignal(number) for number in signals}
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    if old_timer[0]:
        raise RuntimeError("run cannot replace an existing process alarm")

    def stop(number, _frame):
        if number == signal.SIGALRM:
            raise Stopped(124, f"{phase} deadline exceeded")
        raise Stopped(128 + number, f"interrupted by signal {number}")

    def record(location, name):
        nonlocal allocated
        allocated = (location, name)
        print(f"raft run: allocated {location}:{name}", file=sys.stderr, flush=True)

    def issue(error):
        nonlocal status
        print(f"raft run: {error}", file=sys.stderr, flush=True)
        if status == 0:
            status = 1

    try:
        for number in signals:
            signal.signal(number, stop)
        signal.setitimer(signal.ITIMER_REAL, args.timeout)
        try:
            include = git_files(source) if args.push else None
            api.new(args, run_id=token, on_allocate=record)
            location, name = allocated
            while api.execute(location, name, ["true"], capture=True).returncode:
                time.sleep(0.2)
            if source:
                api.transfer_tree(
                    SimpleNamespace(
                        action="upload", source=source, destination=cwd, include=include
                    ),
                    location,
                    name,
                )
            started = True
            result = api.execute(location, name, command, cwd=cwd)
            status = result.returncode if result.returncode >= 0 else 128 - result.returncode
        except Stopped as error:
            status = error.status
            issue(error)
        except KeyboardInterrupt:
            status = 130
            issue("interrupted")
        except Exception as error:
            issue(error)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            # A repeated cancellation cannot interrupt the bounded cleanup grace.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            if allocated is not None:
                location, name = allocated
                try:
                    if started and pulls:
                        phase = "artifact collection"
                        signal.setitimer(signal.ITIMER_REAL, GRACE)
                        try:
                            for remote, local in pulls:
                                try:
                                    api.transfer_tree(
                                        SimpleNamespace(
                                            action="download", source=remote, destination=local
                                        ),
                                        location,
                                        name,
                                    )
                                except Stopped:
                                    raise
                                except Exception as error:
                                    issue(f"could not collect {remote}: {error}")
                        except Stopped as error:
                            issue(error)
                        finally:
                            signal.setitimer(signal.ITIMER_REAL, 0)
                finally:
                    phase = "cleanup"
                    signal.setitimer(signal.ITIMER_REAL, GRACE)
                    try:
                        api.transaction(
                            location, 'exec python3 -c "$3" "$1" "$2"', name, token, CLEANUP
                        )
                        print(f"raft run: cleanup confirmed for {location}:{name}", file=sys.stderr)
                    except Exception as error:
                        issue(
                            f"cleanup not confirmed for {location}:{name}: {error}; inspect this handle. "
                            "The host expiry worker is the backstop, not proof of deletion."
                        )
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for number, handler in previous.items():
            signal.signal(number, handler)
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
    print(
        f"raft run: finished in {time.monotonic() - began:.1f}s (status {status})", file=sys.stderr
    )
    return status
