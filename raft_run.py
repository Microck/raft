"""One-shot CI orchestration; guests are disposable containers, not registered runners."""

import argparse
import os
from pathlib import Path, PurePosixPath
import signal
import re
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

GRACE = 30
LEASE_MARGIN = 120

# Host-only state. /run is cleared on reboot, when queued SSH processes also die.
# Never put root-created markers in world-writable /run/lock or follow symlinks.
CANCELLATIONS = """import os, re, stat, sys
CANCEL_ROOT = "/run/raft-ci-cancelled"
def cancel_directory(create=False):
    if create:
        try:
            os.mkdir(CANCEL_ROOT, 0o700)
        except FileExistsError:
            pass
    try:
        directory = os.open(CANCEL_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if create:
            raise
        return None
    metadata = os.fstat(directory)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        os.close(directory)
        raise RuntimeError('Unsafe CI cancellation directory; refusing to use it')
    return directory

def cancellation(token, record=False):
    if not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('Invalid CI run identity')
    directory = cancel_directory(record)
    if directory is None:
        return False
    try:
        if record:
            try:
                marker = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            except FileExistsError:
                pass
            else:
                os.close(marker)
        try:
            os.stat(token, dir_fd=directory, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    finally:
        os.close(directory)
"""

CHECK_CANCELLED = (
    CANCELLATIONS
    + """
if cancellation(sys.argv[1]):
    sys.exit('CI run was cancelled before creation')
"""
)


def creation_guard(token):
    if not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("Invalid CI run identity")
    return shlex.join(["python3", "-c", CHECK_CANCELLED, token]) + "\n"


# The caller holds the existing host lifecycle lock around this entire program.
CLEANUP = (
    CANCELLATIONS
    + """import json, subprocess, sys
name, token, creation = sys.argv[1:]
if creation not in ("pending", "acknowledged"):
    sys.exit("Invalid CI creation state")
if creation == "pending":
    cancellation(token, record=True)
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
)


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
    creation_acknowledged = False
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
            creation_acknowledged = True
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
                            location,
                            'exec python3 -c "$4" "$1" "$2" "$3"',
                            name,
                            token,
                            "acknowledged" if creation_acknowledged else "pending",
                            CLEANUP,
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
