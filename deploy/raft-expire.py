#!/usr/bin/python3
"""Enforce running deadlines and preview or delete explicitly selected old boxes."""

import argparse
import json
import subprocess
import time

PROTOCOL = 1

PREFIX = ["incus", "--project", "raft"]


def run(*argv):
    return subprocess.run([*PREFIX, *argv], check=True, capture_output=True, text=True).stdout


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
    for box in json.loads(run("list", "--fast", "--format", "json")):
        config = box["config"]
        if config.get("user.raft") != "true":
            continue
        name = box["name"]
        if args.prune_days is not None:
            stopped_at = config.get("user.raft.stopped-at")
            if box["status"] != "Stopped" or not stopped_at:
                continue
            if int(stopped_at) > now - args.prune_days * 86400:
                continue
            if args.yes:
                run("delete", name)
            print(json.dumps({"box": name, "action": "deleted" if args.yes else "would-delete"}))
            continue
        deadline = config.get("user.raft.expires")
        if not deadline or int(deadline) > now:
            continue
        if box["status"] not in ["Running", "Stopped"]:
            raise RuntimeError(f"Unexpected state for expired box {name}")
        if config.get("user.raft.disposable") == "true":
            run("delete", name, "--force")
            print(f"Deleted expired disposable box {name}", flush=True)
            continue
        if box["status"] == "Running":
            run("stop", name, "--timeout", "30")
        run("config", "set", name, f"user.raft.stopped-at={now}")
        run("config", "unset", name, "user.raft.expires")
        print(f"Stopped expired box {name}; disk retained", flush=True)


if __name__ == "__main__":
    main()
