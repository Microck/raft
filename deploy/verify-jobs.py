"""Real process/store checks and optional disposable-host retained-job verification."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import raft_jobs as jobs  # noqa: E402


def stored(job, *, state="completed", finished=None):
    return {
        "job": job,
        "box": "rf-" + "a" * 16,
        "state": state,
        "exitCode": 7,
        "finishedAt": int(time.time()) if finished is None else finished,
        "bootId": "old-boot",
    }


def verify():
    with tempfile.TemporaryDirectory(prefix="raft-jobs-") as directory:
        root = Path(directory)
        with jobs.private_store(root):
            pass
        status, dropped = jobs.capture(
            root,
            [
                sys.executable,
                "-c",
                "import os,sys; os.write(1,b'x'*(2*1024*1024)); os.write(2,b'err'); sys.exit(7)",
            ],
        )
        assert status == 7 and dropped == jobs.LOG_LIMIT + 3
        assert (root / "output").read_bytes() == b"x" * jobs.LOG_LIMIT
        (root / "output").unlink()
        old = root / ("rfcmd-" + "a" * 32)
        old.mkdir()
        jobs.save(old, stored(old.name, finished=int(time.time()) - jobs.RETENTION - 1))
        current = root / ("rfcmd-" + "b" * 32)
        current.mkdir()
        jobs.save(current, stored(current.name))
        interrupted = root / ("rfcmd-" + "c" * 32)
        interrupted.mkdir()
        jobs.save(interrupted, stored(interrupted.name, state="running"))
        jobs.prune(root)
        assert not old.exists() and current.exists()
        assert jobs.load(interrupted)["state"] == "interrupted"
        assert jobs.load(interrupted)["exitCode"] is None
        root.chmod(0o755)
        try:
            with jobs.private_store(root):
                raise AssertionError("World-readable job store accepted")
        except ValueError:
            pass
        finally:
            root.chmod(0o700)
        for index in range(jobs.RECORD_LIMIT - 2):
            (root / f"slot-{index}").mkdir()
        try:
            jobs.start(root, "rf-" + "a" * 16, "rfcmd-" + "d" * 32, ["true"], "")
        except ValueError as error:
            assert "full" in str(error)
        else:
            raise AssertionError("Full store accepted a new job")
    print(
        "Real subprocess log cap, exit code, retention, reboot interruption, permissions and record limit passed"
    )


def native(location):
    spec = importlib.util.spec_from_file_location("live", ROOT / "deploy/verify-live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    box = live.raft(
        "new",
        "--location",
        location,
        "--persistent",
        "--ttl",
        "600",
        "--purpose",
        "Retained job verification",
        "--owner",
        "verify-jobs",
    ).stdout.strip()
    destroyed = False
    try:
        deadline = time.monotonic() + 40
        while live.raft(
            "exec", box, "--", "systemctl", "show", "-p", "Version", check=False
        ).returncode:
            assert time.monotonic() < deadline, "Guest systemd did not become ready"
            time.sleep(0.2)
        completed = live.raft(
            "exec", box, "--detach", "--", "sh", "-c", "printf retained-output; exit 23"
        ).stdout.strip()
        record = live.wait_job(box, completed)
        output = live.raft("logs", box, completed).stdout
        if record["exitCode"] != 23 or record["state"] != "completed":
            unit = live.raft("exec", box, "--", "systemctl", "show", completed, check=False)
            raise AssertionError(
                {"record": record, "output": output, "unit": unit.stdout, "error": unit.stderr}
            )
        assert output == "retained-output"
        active = live.raft(
            "exec", box, "--detach", "--", "sh", "-c", "echo ready; sleep 600"
        ).stdout.strip()
        deadline = time.monotonic() + 20
        while "ready" not in live.raft("logs", box, active).stdout:
            assert time.monotonic() < deadline
            time.sleep(0.2)
        live.raft("cancel", box, active)
        record = live.wait_job(box, active)
        assert record["state"] == "cancelled", (record, live.raft("logs", box, active).stdout)
        interrupted = live.raft("exec", box, "--detach", "--", "sleep", "600").stdout.strip()
        live.raft("stop", box)
        record = live.wait_job(box, interrupted)
        assert record["state"] == "interrupted", (
            record,
            live.raft("logs", box, interrupted).stdout,
        )
        assert live.raft("logs", box, completed).stdout == "retained-output"
        live.raft("destroy", box)
        destroyed = True
        assert json.loads(live.raft("status", box, completed).stdout)["exitCode"] == 23
        assert live.raft("logs", box, completed).stdout == "retained-output"
        wrong = location + ":rf-" + "0" * 16
        assert live.raft("logs", wrong, completed, check=False).returncode == 1
        print(f"{location}: completed, cancelled and interrupted results survive stop and deletion")
    finally:
        if not destroyed:
            live.raft("destroy", box)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--location")
    args = cli.parse_args()
    verify()
    if args.location:
        native(args.location)
