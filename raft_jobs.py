"""Host-owned, bounded results for guest systemd jobs.

The controller sends this stdlib-only module to the host. Supervisors are
transient host services, so closing the controller cannot lose result capture.
"""

import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path("/var/lib/raft-jobs")
LOG_LIMIT = 1024 * 1024
RECORD_LIMIT = 512
RETENTION = 7 * 86400
TERMINAL = {"completed", "cancelled", "interrupted", "failed"}
STATES = TERMINAL | {"starting", "running"}


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save(directory, record):
    staged = directory / "result.tmp"
    with staged.open("w") as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())
    staged.replace(directory / "result.json")
    sync_directory(directory)


def load(directory):
    path = directory / "result.json"
    try:
        record = json.loads(path.read_text())
        if (
            not isinstance(record, dict)
            or record.get("job") != directory.name
            or not isinstance(record.get("box"), str)
            or not isinstance(record.get("bootId"), str)
            or not isinstance(record.get("state"), str)
            or record["state"] not in STATES
            or (record["state"] in TERMINAL and type(record.get("finishedAt")) is not int)
        ):
            raise ValueError("Invalid job record")
        return record
    except (FileNotFoundError, ValueError) as error:
        raise ValueError(
            f"Detached job not found or its record is unreadable: {path}. "
            "It may have expired; inspect the host record and retained output if present."
        ) from error


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


@contextmanager
def private_store(root, create=False):
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
        sync_directory(root.parent)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        os.close(descriptor)
        raise ValueError("Job store must be owned by the host operator with mode 0700")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def capture(directory, command):
    """Drain all output while retaining only the first LOG_LIMIT bytes on disk."""
    dropped = 0
    kept = 0
    with (directory / "output").open("wb", buffering=0) as log:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        ) as process:
            while chunk := process.stdout.read1(65536):
                remaining = max(0, LOG_LIMIT - kept)
                retained = chunk[:remaining]
                if retained:
                    log.write(retained)
                kept += len(retained)
                dropped += len(chunk) - len(retained)
            status = process.wait()
        os.fsync(log.fileno())
    return status, dropped


def worker(root, name, job):
    directory = root / job
    with private_store(root):
        record = load(directory)
        record.update(state="running", startedAt=int(time.time()))
        save(directory, record)
    command = [
        "incus",
        "--project",
        "raft",
        "exec",
        name,
        "--",
        "systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        # Simple services treat several termination signals as success and discard
        # their exit metadata. Oneshot retains those failures for inspection below.
        "--service-type=oneshot",
        "--unit",
        job,
        "--property=WorkingDirectory=/workspace",
        "--",
        *record["command"],
    ]
    try:
        status, dropped = capture(directory, command)
        # During shutdown the guest can still answer queries and return a payload
        # status. Inspect the manager as well, including for zero-exit handlers.
        inspected = subprocess.run(
            [
                "incus",
                "--project",
                "raft",
                "exec",
                name,
                "--",
                "sh",
                "-c",
                'systemctl show "$1" -p ExecMainCode -p ExecMainStatus -p SubState '
                "&& systemctl show -p SystemState",
                "raft-job-status",
                job,
            ],
            capture_output=True,
            timeout=60,
        )
        properties = dict(
            line.split("=", 1)
            for line in inspected.stdout.decode(errors="replace").splitlines()
            if "=" in line
        )
        confirmed = (
            inspected.returncode == 0
            and properties.get("SystemState") not in {None, "stopping"}
            and (
                status == 0
                or (
                    properties.get("ExecMainCode", "0") != "0"
                    and properties.get("SubState") != "running"
                )
            )
        )
        exit_code = None
        if confirmed:
            exit_code = 0 if status == 0 else int(properties["ExecMainStatus"])
            if status != 0 and properties["ExecMainCode"] != "1":
                exit_code += 128
        record.update(
            state="completed" if confirmed else "interrupted",
            exitCode=exit_code,
            droppedBytes=dropped,
        )
    except subprocess.TimeoutExpired:
        # Only the post-run inspection has a timeout; an unknown result is not success.
        record.update(state="interrupted", exitCode=None, droppedBytes=dropped)
    except Exception as error:
        record.update(state="failed", exitCode=None, error=str(error))
    with private_store(root):
        if (directory / "cancelled").exists():
            record.update(state="cancelled", exitCode=None)
        record.pop("command", None)
        record["finishedAt"] = int(time.time())
        save(directory, record)


def prune(root):
    # The store lock serializes reconciliation with worker result commits.
    now = int(time.time())
    current_boot = boot_id()
    records = []
    for directory in root.iterdir():
        if directory.is_dir():
            try:
                records.append((directory, load(directory)))
            except ValueError:
                # Preserve damaged records and logs for inspection. A direct query
                # still reports the record error, without disabling unrelated jobs.
                continue
    units = [
        "raft-" + record["job"]
        for _, record in records
        if record["state"] not in TERMINAL and record["bootId"] == current_boot
    ]
    states = {}
    if units:
        try:
            response = subprocess.run(
                ["systemctl", "show", "-p", "Id", "-p", "ActiveState", *units],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "Timed out reconciling detached jobs; retry the operation"
            ) from error
        for block in response.stdout.strip().split("\n\n"):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if "Id" in fields:
                states[fields["Id"].removesuffix(".service")] = fields.get("ActiveState")
    for directory, record in records:
        if record["state"] not in TERMINAL:
            alive = record["bootId"] == current_boot and states.get("raft-" + record["job"]) in {
                "active",
                "activating",
                "deactivating",
            }
            if not alive:
                record.update(state="interrupted", exitCode=None, finishedAt=now)
                record.pop("command", None)
                save(directory, record)
        if record["state"] in TERMINAL and now - record["finishedAt"] >= RETENTION:
            shutil.rmtree(directory)


def start(root, name, job, payload, source):
    if len(list(root.iterdir())) >= RECORD_LIMIT:
        raise ValueError("Job store is full; results expire seven days after completion")
    directory = root / job
    directory.mkdir(mode=0o700)
    try:
        record = {
            "job": job,
            "box": name,
            "state": "starting",
            "exitCode": None,
            "startedAt": None,
            "finishedAt": None,
            "droppedBytes": None,
            "bootId": boot_id(),
            "command": payload,
        }
        (directory / "output").touch(mode=0o600)
        save(directory, record)
        sync_directory(root)
    except Exception:
        # No supervisor has been submitted, so a partial allocation is safe to remove.
        shutil.rmtree(directory)
        raise
    try:
        response = subprocess.run(
            [
                "systemd-run",
                "--quiet",
                "--no-block",
                "--collect",
                "--unit",
                "raft-" + job,
                "--property=UMask=0077",
                "--",
                "python3",
                "-c",
                source,
                "worker",
                name,
                job,
            ],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Timed out submitting {job}; inspect its retained status before retrying"
        ) from error
    if response.returncode:
        shutil.rmtree(directory)
        raise RuntimeError(response.stderr.decode(errors="replace").strip())
    print(job)


def main(root=ROOT):
    os.umask(0o077)
    action, name, job = sys.argv[1:4]
    if not re.fullmatch(r"rf-[a-f0-9]{16}", name) or not re.fullmatch(r"rfcmd-[a-f0-9]{32}", job):
        raise ValueError("Invalid workspace or job identity")
    if action == "worker":
        worker(root, name, job)
        return
    if action == "start":
        payload = json.loads(sys.argv[4])
        if (
            not isinstance(payload, list)
            or not payload
            or not all(isinstance(arg, str) and "\0" not in arg for arg in payload)
        ):
            raise ValueError("A detached job requires command arguments without NUL")
        # The caller holds the host lifecycle lock. Probe before taking the
        # shared job-store lock so an unresponsive guest cannot block other jobs.
        try:
            probe = subprocess.run(
                ["incus", "--project", "raft", "exec", name, "--", "true"],
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Workspace did not respond; no job was created") from error
        if probe.returncode:
            raise RuntimeError(
                "Workspace is unavailable; no job was created: "
                + probe.stderr.decode(errors="replace").strip()
            )
    with private_store(root, action == "start"):
        prune(root)
        if action == "start":
            start(root, name, job, payload, sys.argv[5])
            return
        directory = root / job
        record = load(directory)
        if record["box"] != name:
            raise ValueError("Detached job does not belong to this workspace")
        if action == "status":
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in record.items()
                        if key not in {"command", "bootId"}
                    }
                )
            )
        elif action == "logs":
            sys.stdout.buffer.write((directory / "output").read_bytes())
        elif action == "cancel":
            if record["state"] in {"completed", "cancelled"}:
                return
            marker = directory / "cancelled"
            marker.touch(mode=0o600)
            try:
                response = subprocess.run(
                    ["incus", "--project", "raft", "exec", name, "--", "systemctl", "stop", job],
                    capture_output=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    "Timed out stopping the guest job; the cancellation request remains recorded. "
                    "Inspect the job and retry cancel."
                ) from error
            if response.returncode:
                marker.unlink()
                raise RuntimeError(
                    "Could not cancel job; it may still be starting: "
                    + response.stderr.decode(errors="replace").strip()
                )
            if record["state"] in TERMINAL:
                # Reconciliation already confirmed the supervisor is gone, so
                # cancel must finalize the result instead of waiting for a worker.
                record.update(state="cancelled", exitCode=None, finishedAt=int(time.time()))
                save(directory, record)
        else:
            raise ValueError("Unknown job operation")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError:
        sys.exit("Detached job not found or its retained result has expired")
    except (ValueError, RuntimeError, OSError) as error:
        sys.exit(str(error))
