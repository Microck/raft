"""Verify executable plugins through real CLI processes, without hosts or credentials.

Run with python3 deploy/verify-plugins.py. The provider and guest files live only
under a temporary directory; PATH deliberately contains no SSH or Incus client.
"""

import json
import base64
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent

# This is a real, deliberately small provider, not an interception of Raft internals.
# Its state survives invocations so CLI routing errors affect observable box state.
PROVIDER = r"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

store = Path(sys.argv[1])
mode = sys.argv[2]
action = sys.argv[3]
request = json.loads(os.environ["RAFT_PLUGIN_REQUEST"])
assert request["protocol"] == 1
assert isinstance(request["location"], str) and request["location"]
assert isinstance(request["options"], dict)
commands = ["new", "list", "info", "exec", "upload", "download", "note", "destroy"]
if action == "describe":
    assert request["args"] == {}
    assert sys.stdin.read() == ""
    replies = {
        "good": {"protocol": 1, "commands": commands},
        "version": {"protocol": 2, "commands": commands},
        "array": [],
        "missing": {"protocol": 1},
        "string-commands": {"protocol": 1, "commands": "new"},
        "nonstring-command": {"protocol": 1, "commands": [42]},
        "management-command": {"protocol": 1, "commands": ["host"]},
    }
    print("not JSON" if mode == "malformed" else json.dumps(replies[mode]))
    sys.exit(0)

args = request["args"]
assert args["action"] == action and args["location"] == request["location"]
store.mkdir(parents=True, exist_ok=True)
with (store / "actions.jsonl").open("a") as journal:
    journal.write(json.dumps({"action": action, "location": request["location"]}) + "\n")
if action not in commands:
    raise SystemExit("unsupported action reached provider")

def write_record(directory, record):
    (directory / "info.json").write_text(json.dumps(record))

if action == "new":
    # This provider has a fixed template. Native sizing defaults would break it.
    if args["cpu"] is not None or args["memory"] is not None:
        raise SystemExit("fixed-template provider rejects explicit sizing")
    if args["template"] is not None:
        raise SystemExit("fixture has no snapshot support")
    sequence = store / "sequence"
    number = int(sequence.read_text()) + 1 if sequence.exists() else 1
    sequence.write_text(str(number))
    directory = store / ("box-" + str(number))
    (directory / "workspace").mkdir(parents=True)
    handle = request["location"] + ":job-" + str(number) + ":opaque+β"
    note = {key: args[key] for key in ("purpose", "owner", "next") if args[key] is not None}
    record = {"box": handle, "state": "Running", "stopAt": str(int(time.time()) + args["ttl"]),
              "note": note or None}
    write_record(directory, record)
    print(handle)
elif action == "list":
    for filename in sorted(store.glob("box-*/info.json")):
        print(filename.read_text())
else:
    handle = args["target"] if action == "note" else args["box"]
    for filename in store.glob("box-*/info.json"):
        record = json.loads(filename.read_text())
        if record["box"] == handle:
            directory = filename.parent
            break
    else:
        raise SystemExit("box not found: " + handle)
    workspace = directory / "workspace"
    if action == "info":
        print(json.dumps(record))
    elif action == "destroy":
        shutil.rmtree(directory)
    elif action == "note":
        record["note"] = None if args["clear"] else {
            key: args[key] for key in ("purpose", "owner", "next") if args[key] is not None
        }
        write_record(directory, record)
    elif action == "exec":
        assert not args["detach"]
        result = subprocess.run(args["command"], cwd=workspace)
        if result.returncode < 0:
            os.kill(os.getpid(), -result.returncode)
        sys.exit(result.returncode)
    else:
        if args["recursive"]:
            raise SystemExit("fixture supports single files only")
        remote = args["destination"] if action == "upload" else args["source"]
        remote = Path(remote)
        if not remote.is_absolute() or ".." in remote.parts or remote.parts[1] != "workspace":
            raise SystemExit("fixture files must be below /workspace")
        guest = workspace.joinpath(*remote.parts[2:])
        if action == "upload":
            shutil.copyfile(args["source"], guest)
        else:
            shutil.copyfile(guest, args["destination"])
"""


class Controller:
    def __init__(self, directory):
        self.directory = directory
        self.home = directory / "home"
        self.config = self.home / ".config/raft/incus.json"
        self.store = directory / "provider-state"
        self.script = directory / "provider script.py"
        self.script.write_text(PROVIDER)
        binary = directory / "bin"
        binary.mkdir()
        (binary / "provider-python").symlink_to(sys.executable)
        # An allowlist avoids inherited Raft overrides, SSH agents and credentials.
        self.env = {
            "HOME": str(self.home),
            "PATH": str(binary),
            "TMPDIR": str(directory),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "LC_ALL": "C.UTF-8",
            "RAFT_NO_UPDATE_CHECK": "1",
        }

    def raft(self, *arguments, code=0, env=None, input=""):
        result = subprocess.run(
            [sys.executable, str(ROOT / "raft.py"), *map(str, arguments)],
            cwd=self.directory,
            env=self.env if env is None else env,
            input=input,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if code is None:
            assert result.returncode != 0, (arguments, result.stdout, result.stderr)
        else:
            assert result.returncode == code, (
                arguments,
                result.returncode,
                result.stdout,
                result.stderr,
            )
        return result

    def command(self, mode="good"):
        return ["provider-python", str(self.script), str(self.store), mode]

    def add(self, location="cloud", mode="good", code=0):
        return self.raft("plugin", "add", location, "--", *self.command(mode), code=code)

    def rows(self):
        return [
            json.loads(line)
            for line in self.raft("list", "--location", "cloud").stdout.splitlines()
        ]

    def info(self, box):
        return json.loads(self.raft("info", box).stdout)

    def actions(self):
        journal = self.store / "actions.jsonl"
        return journal.read_bytes() if journal.exists() else b""

    def configure(self, inventory):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(inventory))


def verify_registration(controller):
    for arguments in [("--version",), ("--help",), ("plugin", "--help")]:
        controller.raft(*arguments)
    assert not controller.config.exists()
    controller.add()
    assert controller.config.stat().st_mode & 0o777 == 0o600
    saved = controller.config.read_bytes()
    replacement = controller.command()
    replacement[2] = str(controller.directory / "replacement-state")
    controller.raft("plugin", "add", "cloud", "--", *replacement, code=None)
    assert controller.config.read_bytes() == saved, "duplicate registration changed config"
    # Removal and introspection are controller operations, not remote deletion.
    described = json.loads(controller.raft("plugin", "info", "cloud").stdout)
    assert described["protocol"] == 1 and "destroy" in described["commands"]
    assert controller.actions() == b""
    # Resolution at registration must survive a later PATH without that executable.
    no_path = {**controller.env, "PATH": str(controller.directory / "absent")}
    controller.raft("plugin", "info", "cloud", env=no_path)
    print("registration: private clean-HOME config, duplicate protection, resolved executable")


def verify_lifecycle(controller):
    created = controller.raft("new", "--disposable")
    box = created.stdout.strip()
    location, identifier = box.split(":", 1)
    assert location == "cloud" and not re.fullmatch(r"rf-[a-f0-9]{16}", identifier)
    record = controller.info(box)
    assert record["box"] == box and record["state"] == "Running"
    assert controller.rows() == [record]
    # A full minute window avoids scheduler-sensitive timestamp assertions.
    assert 540 <= int(record["stopAt"]) - int(time.time()) <= 600

    literal = [
        "a b",
        "",
        "$HOME",
        "$(touch controller-expanded)",
        "; touch controller-expanded",
        "*",
        "|",
        ">",
        "'quoted'",
        "β",
    ]
    (controller.directory / "glob-match").write_text("must not expand")
    echo = "import json,sys; print(json.dumps(sys.argv[1:]))"
    output = controller.raft("exec", box, "--", sys.executable, "-c", echo, *literal)
    assert json.loads(output.stdout) == literal
    assert not (controller.directory / "controller-expanded").exists()

    # A real guest process changes state before failing. Retrying would duplicate it.
    program = (
        "from pathlib import Path; import sys; "
        "f=Path('attempts').open('a'); f.write('once\\n'); f.close(); "
        "sys.stdout.write('guest stdout\\n'); sys.stderr.write('guest stderr\\n'); sys.exit(37)"
    )
    failed = controller.raft("exec", box, "--", sys.executable, "-c", program, code=37)
    assert failed.stdout == "guest stdout\n" and failed.stderr == "guest stderr\n"
    attempts = controller.directory / "attempts"
    controller.raft("download", box, "/workspace/attempts", attempts)
    assert attempts.read_bytes() == b"once\n", "uncertain exec was retried"
    controller.raft(
        "exec",
        box,
        "--",
        sys.executable,
        "-c",
        "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
        code=128 + signal.SIGTERM,
    )

    payload = b"\x00plugin file\xff\n"
    source = controller.directory / "upload source"
    destination = controller.directory / "download destination"
    source.write_bytes(payload)
    controller.raft("upload", box, source, "/workspace/payload")
    source.unlink()
    controller.raft("download", box, "/workspace/payload", destination)
    assert destination.read_bytes() == payload

    actions = controller.actions()
    controller.raft("stop", box, code=None)
    assert controller.actions() == actions, "unsupported command reached provider"
    assert controller.info(box) == record, "unsupported stop changed remote state"
    actions = controller.actions()
    for arguments in [
        ["sync", box, ".", "/workspace/source"],
        ["status", box, "provider-job"],
        ["policy", "cloud", "--require", "purpose"],
        ["run", "--location", "cloud", "--", "true"],
    ]:
        controller.raft(*arguments, code=None)
        assert controller.actions() == actions, "unsupported command reached provider"
    report = json.loads(controller.raft("config", "--location", "cloud").stdout)
    assert report["policy"] == {"cloud": None}

    # The describe preflight must not consume stdin intended for the guest command.
    echoed = controller.raft(
        "exec",
        box,
        "--",
        sys.executable,
        "-c",
        "import sys; sys.stdout.write(sys.stdin.read())",
        input="piped payload\n",
    )
    assert echoed.stdout == "piped payload\n"
    structured = controller.raft(
        "--json",
        "exec",
        box,
        "--",
        sys.executable,
        "-c",
        "import os,sys; os.write(1, bytes(range(256))); os.write(2, b'failure'); sys.exit(23)",
        code=23,
    )
    events = [json.loads(line) for line in structured.stdout.splitlines()]
    assert not structured.stderr and events[-1] == {"type": "exit", "code": 23}
    for stream, expected in [("stdout", bytes(range(256))), ("stderr", b"failure")]:
        assert (
            b"".join(base64.b64decode(row["data"]) for row in events if row["type"] == stream)
            == expected
        )

    # Configured sizing must reach a fixed-template provider instead of being silently ignored.
    controller.raft("config", "set", "cpu", "3")
    controller.raft("new", "--location", "cloud", "--disposable", code=None)
    assert controller.rows() == [record]
    controller.raft("config", "unset", "cpu")
    actions = controller.actions()
    controller.raft("note", "cloud", "controller guidance")
    locations = [json.loads(line) for line in controller.raft("locations").stdout.splitlines()]
    assert locations[0]["note"] == "controller guidance"
    controller.raft("note", "cloud", "--clear")
    locations = [json.loads(line) for line in controller.raft("locations").stdout.splitlines()]
    assert locations[0]["note"] == ""
    assert controller.actions() == actions, "location note reached provider"
    controller.raft(
        "note", box, "--purpose", "regression", "--owner", "verifier", "--next", "destroy"
    )
    assert controller.info(box)["note"] == {
        "purpose": "regression",
        "owner": "verifier",
        "next": "destroy",
    }
    controller.raft("note", box, "--clear")
    assert controller.info(box)["note"] is None

    controller.add("other")
    actions = controller.actions()
    controller.raft("new", "--location", "other", "--from", box + "/snapshot", code=None)
    assert controller.actions() == actions, "cross-location template reached provider"
    assert len(controller.rows()) == 1
    controller.raft("plugin", "remove", "other")

    # Selecting the plugin must not attempt native SSH, even when native is default.
    inventory = json.loads(controller.config.read_text())
    controller.configure({"unusable": {"ssh": "must-not-contact.invalid"}, **inventory})
    assert controller.rows() == [record]
    actions = controller.actions()
    controller.raft("plugin", "info", "cloud")
    assert controller.actions() == actions
    controller.raft("plugin", "remove", "cloud")
    assert controller.actions() == actions, "removing registration executed a provider action"
    controller.add()
    assert controller.info(box) == record, "registration removal deleted remote box"
    controller.raft("destroy", box)
    assert controller.rows() == []
    controller.raft("info", box, code=None)
    print(
        "lifecycle: opaque handles, argv/stdio/exits, files, notes, capability and location isolation"
    )


def verify_rejections(directory):
    modes = [
        "version",
        "array",
        "missing",
        "string-commands",
        "nonstring-command",
        "management-command",
        "malformed",
    ]
    for mode in modes:
        work = directory / mode
        work.mkdir()
        controller = Controller(work)
        controller.add(mode=mode, code=None)
        assert not controller.config.exists(), "invalid handshake persisted registration: " + mode
        assert controller.actions() == b""
    work = directory / "invalid-config"
    work.mkdir()
    controller = Controller(work)
    resolved = [sys.executable, str(controller.script), str(controller.store), "good"]
    entries = [
        {"plugin": "not-an-argv"},
        {"plugin": []},
        {"plugin": [sys.executable, 42]},
        {"plugin": resolved, "ssh": "must-not-contact.invalid"},
    ]
    for entry in entries:
        controller.configure({"cloud": entry})
        before = controller.config.read_bytes()
        controller.raft("list", "--location", "cloud", code=None)
        assert controller.config.read_bytes() == before
        assert controller.actions() == b"", "invalid config reached provider"
    print("rejections: invalid protocol/JSON/capabilities and malformed or mixed location config")


def main():
    with tempfile.TemporaryDirectory(prefix="raft-plugin-verification-") as temporary:
        directory = Path(temporary)
        controller = Controller(directory)
        verify_registration(controller)
        verify_lifecycle(controller)
        verify_rejections(directory)
    print("Executable plugin verification passed; no live hosts or operator state used")


if __name__ == "__main__":
    main()
