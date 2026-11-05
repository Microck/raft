"""Versioned executable location plugins. No provider dependencies live in Raft."""

import json
import os
import signal
import subprocess

PROTOCOL = 1
COMMANDS = frozenset(
    "new run recover list capacity doctor gc prune policy note info stop resume extend destroy ssh exec "
    "upload download snapshot snapshots snapshot-delete restore fork forward desktop logs "
    "status cancel backup usage sync".split()
)


def validate(entry):
    """Validate the controller boundary before launching any plugin code."""
    command = entry["plugin"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part or "\0" in part for part in command)
    ):
        raise ValueError("Plugin command must be a nonempty array of nonempty strings")
    if "ssh" in entry:
        raise ValueError("A location cannot configure both ssh and plugin")
    if not isinstance(entry.get("options", {}), dict):
        raise ValueError("Plugin options must be a JSON object")


def environment(location, entry, arguments):
    return {
        **os.environ,
        "RAFT_PLUGIN_REQUEST": json.dumps(
            {
                "protocol": PROTOCOL,
                "location": location,
                "options": entry.get("options", {}),
                "args": arguments,
            }
        ),
    }


def describe(location, entry):
    """Negotiate afresh so upgrades cannot run stale, unsupported commands."""
    with subprocess.Popen(
        [*entry["plugin"], "describe"],
        env=environment(location, entry, {}),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            output, errors = process.communicate(timeout=10)
        except BaseException as error:
            # A helper inheriting the pipes must not outlive a timed-out handshake.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError(
                    f"Plugin {location} describe timed out after 10 seconds"
                ) from None
            raise
        if process.returncode:
            raise RuntimeError(f"Plugin {location} describe failed: {errors.strip()}")
    try:
        manifest = json.loads(output)
    except ValueError as error:
        raise ValueError(f"Plugin {location} describe must return one JSON object") from error
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("protocol")) is not int
        or manifest["protocol"] != PROTOCOL
    ):
        raise ValueError(f"Plugin {location} must implement protocol {PROTOCOL}")
    commands = manifest.get("commands")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(action, str) or action not in COMMANDS for action in commands)
        or len(set(commands)) != len(commands)
    ):
        raise ValueError(f"Plugin {location} commands must be a nonempty list of Raft actions")
    return manifest


def run(location, entry, args):
    manifest = describe(location, entry)
    if args.action not in manifest["commands"]:
        raise ValueError(f"Plugin {location} does not support {args.action}")
    result = subprocess.run(
        [*entry["plugin"], args.action],
        env=environment(location, entry, {**vars(args), "location": location}),
        check=False,
    )
    return result.returncode if result.returncode >= 0 else 128 - result.returncode
