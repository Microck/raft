#!/usr/bin/python3
"""Enforce running deadlines and stopped-box retention, and preview or delete
explicitly selected old boxes."""

import argparse
import json
import re
import subprocess
import sys
import time

# Checked by CHECK_WORKER in raft.py; bump both together. 2 added retention.
PROTOCOL = 2

PREFIX = ["incus", "--project", "raft"]
# Stopped-box retention set by `raft policy LOCATION --delete-stopped-after
# HOURS` on the Incus project, and when it was set. Mirrors RETENTION_KEY in
# raft.py, whose delete_at() reports the same deadline this worker enforces.
RETENTION_KEY = "user.raft.delete-stopped-after"


def run(*argv):
    return subprocess.run([*PREFIX, *argv], check=True, capture_output=True, text=True).stdout


def retention():
    """Return the project's (hours, since) retention policy, or None when it is unset."""
    projects = json.loads(run("project", "list", "--format", "json"))
    config = next(item for item in projects if item["name"] == "raft")["config"]
    hours = config.get(RETENTION_KEY, "")
    since = config.get(RETENTION_KEY + ".since", "")
    if not hours:
        return None
    # Raft writes whole numbers; anything else came from a native Incus edit.
    if not re.fullmatch(r"[1-9][0-9]*", hours) or not re.fullmatch(r"[0-9]+", since):
        raise ValueError(
            f"Invalid retention {RETENTION_KEY}={hours!r} .since={since!r}; deleted no stopped "
            "boxes. Fix it with raft policy LOCATION --delete-stopped-after HOURS or never"
        )
    return int(hours), int(since)


def prune(boxes, days, apply, now):
    """Preview or delete stopped boxes whose recorded stop is older than days."""
    for box in boxes:
        stopped_at = box["config"].get("user.raft.stopped-at")
        if box["status"] != "Stopped" or not stopped_at:
            continue
        if int(stopped_at) > now - days * 86400:
            continue
        if apply:
            run("delete", box["name"])
        print(json.dumps({"box": box["name"], "action": "deleted" if apply else "would-delete"}))


def enforce(box, policy, now):
    """Apply the running deadline, then the stopped-box retention, to one box."""
    config = box["config"]
    name = box["name"]
    deadline = config.get("user.raft.expires")
    if deadline and int(deadline) <= now:
        if box["status"] not in ["Running", "Stopped"]:
            raise RuntimeError(f"Unexpected state for expired box {name}")
        if config.get("user.raft.disposable") == "true":
            run("delete", name, "--force")
            print(f"Deleted expired disposable box {name}", flush=True)
            return
        if box["status"] == "Running":
            run("stop", name, "--timeout", "30")
        run("config", "set", name, f"user.raft.stopped-at={now}")
        run("config", "unset", name, "user.raft.expires")
        print(f"Stopped expired box {name}; disk retained", flush=True)
        return
    # Retention never touches a running box. A native incus start keeps the old
    # stop time, so clear it here and the next stop starts a fresh clock. A start
    # and stop between two worker runs still goes unnoticed.
    if box["status"] != "Stopped":
        if box["status"] == "Running" and config.get("user.raft.stopped-at"):
            run("config", "unset", name, "user.raft.stopped-at")
        return
    stopped_at = config.get("user.raft.stopped-at")
    if not stopped_at:
        # Raft records the stop time on stop and expiry. A box stopped any other
        # way (a guest poweroff, a native incus stop, a recovered backup or a
        # failed start) starts its retention clock now. Its real stop time is
        # unknown, and guessing an earlier one could delete it early.
        run("config", "set", name, f"user.raft.stopped-at={now}")
        print(f"Recorded stop time of box {name}, stopped outside Raft", flush=True)
        return
    if policy is None:
        return
    hours, since = policy
    # The clock starts at the later of the stop and the policy change, so a new or
    # shorter policy never deletes a box sooner than its hours after the change.
    if max(int(stopped_at), since) + hours * 3600 > now:
        return
    run("delete", name)
    stopped = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(stopped_at)))
    message = f"Deleted box {name}, stopped since {stopped}, under the {hours}-hour retention"
    # Retention deletes data without a command, so every deletion goes to the host
    # journal beside the policy change that caused it (journalctl -t raft), whether
    # the timer or raft gc ran the worker.
    subprocess.run(["logger", "-t", "raft", message], check=True)
    print(message, flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--prune-days", type=int)
    cli.add_argument("--yes", action="store_true")
    args = cli.parse_args()
    if args.prune_days is not None and args.prune_days < 1:
        cli.error("Retention must be at least one day")
    if args.yes and args.prune_days is None:
        cli.error("--yes requires --prune-days")
    now = int(time.time())
    # The service, gc and prune all hold the same lifecycle lock. Never select
    # unrelated Incus instances or use creation time as a guessed stop time.
    boxes = [
        box
        for box in json.loads(run("list", "--fast", "--format", "json"))
        if box["config"].get("user.raft") == "true"
    ]
    if args.prune_days is not None:
        prune(boxes, args.prune_days, args.yes, now)
        return
    # An invalid policy must not stop deadline enforcement: apply deadlines, skip
    # retention, and fail afterwards so the service run reports the problem.
    try:
        policy, problem = retention(), None
    except ValueError as error:
        policy, problem = None, str(error)
    for box in boxes:
        enforce(box, policy, now)
    if problem:
        sys.exit(problem)


if __name__ == "__main__":
    main()
