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

    class Cancelled(BaseException):
        def __init__(self, number):
            self.number = number

    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    handlers = {number: signal.getsignal(number) for number in signals}
    process = None
    pending = None

    def cancel(number, _frame):
        nonlocal pending
        signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        pending = number
        # Popen must finish publishing the child handle before cancellation unwinds.
        if process is not None:
            raise Cancelled(number)

    try:
        for number in signals:
            if handlers[number] != signal.SIG_IGN and (
                number != signal.SIGALRM or callable(handlers[number])
            ):
                signal.signal(number, cancel)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        process = subprocess.Popen(
            [*entry["plugin"], args.action],
            env=environment(location, entry, {**vars(args), "location": location}),
            start_new_session=True,
        )
        if pending is not None:
            raise Cancelled(pending)
        returncode = process.wait()
        return returncode if returncode >= 0 else 128 - returncode
    except BaseException as error:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                pass
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        if isinstance(error, Cancelled):
            handler = handlers[error.number]
            if callable(handler) and handler != signal.default_int_handler:
                handler(error.number, None)
            return 128 + error.number
        raise
    finally:
        # Discard queued cancellation before restoring the caller's signal state.
        for number in signals:
            signal.signal(number, signal.SIG_IGN)
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for number, handler in handlers.items():
            signal.signal(number, handler)
