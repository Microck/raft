#!/usr/bin/python3
"""Stop expired Raft instances without deleting their saved disks."""

import json
import subprocess
import time

PREFIX = ["incus", "--project", "raft"]


def run(*argv):
    return subprocess.run([*PREFIX, *argv], check=True, capture_output=True, text=True).stdout


def main():
    # The systemd unit and manual CLI entry both take the same host flock.
    for box in json.loads(run("list", "--fast", "--format", "json")):
        if box["config"].get("user.raft") != "true":
            continue
        deadline = box["config"].get("user.raft.expires")
        if not deadline or int(deadline) > time.time():
            continue
        if box["status"] == "Running":
            run("stop", box["name"], "--timeout", "30")
        if box["status"] not in ["Running", "Stopped"]:
            raise RuntimeError(f"Unexpected state for expired box {box['name']}")
        run("config", "unset", box["name"], "user.raft.expires")
        print(f"Stopped expired box {box['name']}; disk retained", flush=True)


if __name__ == "__main__":
    main()
