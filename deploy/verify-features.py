"""Exercise disposable lifecycle, retention, templates, resizing, tree transfers,
controller defaults and host note policies.

--isolated-worker stages the current worker under a private temporary path for
existing hosts. It never replaces their installed services. CI uses the installed
worker and verifies automatic timer deletion as well as CLI prune/gc.
"""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import (  # noqa: E402
    CHECK_NOTE,
    CHECK_NOTE_CODE,
    READ_NOTE_POLICY,
    RESERVE_BOX_DISK,
    RETENTION_KEY,
    RETENTION_SINCE_KEY,
    disk_origin,
    host_problems,
    host_inventory,
    host_policy,
    incus,
    inventory,
    parse_handle,
    remote,
    settings,
    transaction,
)
import raft_files  # noqa: E402
from raft_files import pack_directory, publish, unpack_directory  # noqa: E402

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("verify-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def two_file_archive():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in ["first", "second"]:
            member = tarfile.TarInfo(name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    return stream.getvalue()


def verify_archives():
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        for name, kind, link in [
            ("../escape", tarfile.REGTYPE, ""),
            ("/absolute", tarfile.REGTYPE, ""),
            ("link", tarfile.SYMTYPE, "../../escape"),
            ("link", tarfile.LNKTYPE, "../escape"),
            ("pipe", tarfile.FIFOTYPE, ""),
            ("device", tarfile.CHRTYPE, ""),
        ]:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, link
                archive.addfile(member)
            stream.seek(0)
            try:
                unpack_directory(stream, root / "output", len(stream.getvalue()))
            except (ValueError, tarfile.FilterError):
                pass
            else:
                raise AssertionError("Unsafe archive accepted: " + name)
            assert not (root / "output").exists()
            assert not list(root.glob(".raft-tree-*"))
        complete = two_file_archive()
        try:
            unpack_directory(io.BytesIO(complete[:1024]), root / "truncated", len(complete))
        except ValueError as error:
            assert "size mismatch" in str(error)
        else:
            raise AssertionError("Truncated archive published a partial tree")
        assert not (root / "truncated").exists() and not list(root.glob(".raft-tree-*"))
        staged = root / "staged"
        staged.mkdir()
        existing = root / "existing"
        existing.mkdir()
        try:
            publish(staged, existing)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Atomic publication replaced an existing empty directory")
        assert staged.exists() and existing.exists()
        socket_source = root / "socket-source"
        socket_source.mkdir()
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(socket_source / "socket"))
            try:
                pack_directory(socket_source, io.BytesIO())
            except ValueError as error:
                assert "special files" in str(error)
            else:
                raise AssertionError("Source socket was silently omitted")
        # A real writer replaces both a file and a directory with external links.
        # No function interception: accepted archives must never contain the secret.
        race_source = root / "race-source"
        race_source.mkdir()
        secret = root / "secret"
        secret.write_bytes(b"raft-outside-secret")
        foreign = root / "foreign"
        foreign.mkdir()
        (foreign / "secret").write_bytes(secret.read_bytes())
        (race_source / "file").write_bytes(b"inside")
        (race_source / "directory").mkdir()
        (race_source / "directory" / "file").write_bytes(b"inside")
        finished = threading.Event()

        def replace_entries():
            while not finished.is_set():
                for name, target in [("file", secret), ("directory", foreign)]:
                    entry = race_source / name
                    held = root / (name + "-held")
                    entry.rename(held)
                    entry.symlink_to(target)
                    entry.unlink()
                    held.rename(entry)

        writer = threading.Thread(target=replace_entries)
        writer.start()
        verified_files = 0
        try:
            for _ in range(100):
                packed = io.BytesIO()
                try:
                    pack_directory(race_source, packed)
                except (OSError, ValueError, tarfile.FilterError):
                    continue
                packed.seek(0)
                with tarfile.open(fileobj=packed) as archive:
                    for member in archive:
                        if member.isfile():
                            assert archive.extractfile(member).read() == b"inside"
                            verified_files += 1
        finally:
            finished.set()
            writer.join()
        assert verified_files, "Source replacement test verified no file contents"
        print(f"Source replacement test verified {verified_files} regular-file members", flush=True)
        outside = root / "outside"
        outside.mkdir()
        link = root / "destination"
        link.symlink_to(outside, target_is_directory=True)
        try:
            unpack_directory(io.BytesIO(), link, 0)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Destination symlink was followed")
        assert link.is_symlink() and not list(outside.iterdir())
    print(
        "Archive safety, socket rejection, complete streams and no-overwrite checks passed",
        flush=True,
    )


def verify_location_notes():
    """Edit notes in a private controller home; no host is contacted."""
    with tempfile.TemporaryDirectory() as home:
        config = Path(home) / ".config/raft/incus.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"lab": {"ssh": "lab-host", "image": ""}, "spare": {"ssh": "spare-host"}})
        )

        def cli(*arguments):
            return subprocess.run(
                [sys.executable, str(ROOT / "raft.py"), *arguments],
                env={**os.environ, "HOME": home},
                capture_output=True,
                text=True,
            )

        assert cli("note", "spare", "  Avoid for Docker builds ").returncode == 0
        records = [json.loads(line) for line in cli("locations").stdout.splitlines()]
        assert records == [
            {"location": "lab", "default": True, "note": ""},
            {"location": "spare", "default": False, "note": "Avoid for Docker builds"},
        ], records
        assert list(json.loads(config.read_text())) == ["lab", "spare"], "Default location moved"
        assert config.stat().st_mode & 0o777 == 0o600, "Note editing widened permissions"
        for arguments, error in [
            (["note", "missing", "x"], "Unknown location"),
            (["note", "lab", " "], "--clear"),
        ]:
            rejected = cli(*arguments)
            assert rejected.returncode == 1 and error in rejected.stderr, rejected
        # Box note mistakes are rejected before any host is contacted.
        handle = "lab:rf-0123456789abcdef"
        for arguments, error in [
            (["note", "lab", "x", "--clear"], "TEXT or --clear"),
            (["note", "lab"], "TEXT or --clear"),
            (["note", "lab", "--purpose", "x"], "pass a box handle"),
            (["note", handle, "x"], "--purpose, --owner and --next"),
            (["note", handle], "--purpose (with optional"),
            (["note", handle, "--purpose", "x", "--clear"], "--purpose (with optional"),
            (["note", handle, "--owner", "agent"], "need --purpose"),
            (["note", handle, "--purpose", " "], "--purpose cannot be empty"),
            (["new", "--location", "lab", "--next", "later"], "need --purpose"),
        ]:
            rejected = cli(*arguments)
            assert rejected.returncode == 1 and error in rejected.stderr, (arguments, rejected)
        # Concurrent edits to different locations must all survive.
        many = {f"host{index}": {"ssh": f"host{index}"} for index in range(8)}
        saved = config.read_text()
        config.write_text(json.dumps(many))
        writers = [
            subprocess.Popen(
                [sys.executable, str(ROOT / "raft.py"), "note", location, "busy " + location],
                env={**os.environ, "HOME": home},
            )
            for location in many
        ]
        assert all(writer.wait() == 0 for writer in writers)
        notes = {key: entry.get("note") for key, entry in json.loads(config.read_text()).items()}
        assert notes == {location: "busy " + location for location in many}, notes
        config.write_text(saved)
        assert cli("note", "spare", "--clear").returncode == 0
        assert "note" not in json.loads(config.read_text())["spare"]
        assert not list(config.parent.glob(".raft-*")), "Staged configuration left behind"
        config.write_text(json.dumps({"lab": {"ssh": "lab-host", "note": 1}}))
        rejected = cli("locations")
        assert rejected.returncode == 1 and "notes must be strings" in rejected.stderr, rejected
    print("Location note editing, listing, ordering and permissions passed", flush=True)


def verify_controller_defaults():
    """Edit config.json in a private controller home and resolve new's options offline."""
    with tempfile.TemporaryDirectory() as home:
        directory = Path(home) / ".config/raft"
        directory.mkdir(parents=True)
        (directory / "incus.json").write_text(
            json.dumps({"lab": {"ssh": "lab-host", "image": ""}, "spare": {"ssh": "spare-host"}})
        )
        config = directory / "config.json"

        def cli(*arguments):
            return subprocess.run(
                [sys.executable, str(ROOT / "raft.py"), *arguments],
                env={**os.environ, "HOME": home},
                capture_output=True,
                text=True,
            )

        def resolved(*arguments):
            # The real parser and resolver, reading the real files under HOME.
            program = (
                "import json, sys; import raft; args = raft.parser().parse_args(sys.argv[1:]); "
                "used = raft.apply_new_defaults(args); "
                "print(json.dumps([args.location, args.ttl, args.cpu, args.memory, args.disk, "
                "args.disposable, sorted(used)]))"
            )
            response = subprocess.run(
                [sys.executable, "-c", program, *arguments],
                cwd=ROOT,
                env={**os.environ, "HOME": home},
                capture_output=True,
                text=True,
            )
            if response.returncode:
                return response.stderr
            return json.loads(response.stdout)

        # No disk is the shared pool.
        assert resolved("new") == ["lab", 600, 1, "2GiB", None, False, []]
        for arguments, printed in [
            (["set", "cpu", "2"], "defaults.cpu: built-in default -> 2"),
            (["set", "memory", "4GiB"], "defaults.memory: built-in default -> 4GiB"),
            (["set", "ttl", "3600"], "defaults.ttl: built-in default -> 3600"),
            (["set", "location", "spare"], "defaults.location: built-in default -> spare"),
            (["set", "disk", "20GiB"], "defaults.disk: built-in default -> 20GiB"),
            (["set", "disposable", "true"], "defaults.disposable: built-in default -> true"),
            (["set", "cpu", "1"], "defaults.cpu: 2 -> 1"),
        ]:
            response = cli("config", *arguments)
            assert response.returncode == 0 and response.stdout == printed + "\n", response
        assert json.loads(config.read_text()) == {
            "defaults": {
                "cpu": 1,
                "memory": "4GiB",
                "ttl": 3600,
                "location": "spare",
                "disk": "20GiB",
                "disposable": True,
            }
        }
        assert config.stat().st_mode & 0o777 == 0o600, "Default editing widened permissions"
        # Explicit flags win over config.json, which wins over built-in defaults.
        everything = ["cpu", "disk", "disposable", "location", "memory", "ttl"]
        assert resolved("new") == ["spare", 3600, 1, "4GiB", "20GiB", True, everything]
        assert resolved("new", "--location", "lab", "--ttl", "60", "--cpu", "2") == [
            "lab",
            60,
            2,
            "4GiB",
            "20GiB",
            True,
            ["disk", "disposable", "memory"],
        ]
        # --disk shared beats a configured own disk, and --persistent a disposable default.
        assert resolved("new", "--disk", "shared", "--persistent")[4:] == [
            None,
            False,
            ["cpu", "location", "memory", "ttl"],
        ]
        assert resolved("new", "--disposable")[5:] == [
            True,
            ["cpu", "disk", "location", "memory", "ttl"],
        ]
        rejected = cli("new", "--disposable", "--persistent")
        assert rejected.returncode == 2 and "not allowed with" in rejected.stderr, rejected
        # A box from a snapshot keeps the snapshot's location, CPU, RAM and disk:
        # defaults, configured or built-in, never override them; flags still do.
        # A snapshot has no lifecycle, so the TTL and disposable defaults apply.
        template = "lab:rf-0123456789abcdef/snap"
        assert resolved("new", "--from", template) == [
            None,
            3600,
            None,
            None,
            None,
            True,
            ["disposable", "ttl"],
        ]
        assert resolved("new", "--from", template, "--persistent")[5] is False
        assert resolved("new", "--from", template, "--cpu", "4", "--disk", "30GiB")[2:5] == [
            4,
            None,
            "30GiB",
        ]
        assert "--from keeps the snapshot's disk" in resolved(
            "new", "--from", template, "--disk", "shared"
        )
        # fork has no defaults at all: omitted sizes stay the source's, and a fork
        # is persistent without --disposable even under a disposable default.
        forked = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import raft; args = raft.parser().parse_args(['fork', 'lab:rf-0123456789abcdef', "
                "'--ttl', '600']); print(raft.sizing(args), hasattr(args, 'disk'), args.disposable)",
            ],
            cwd=ROOT,
            env={**os.environ, "HOME": home},
            text=True,
        )
        assert forked == "[] False False\n", forked
        for arguments, error in [
            (["set", "cpu", "0"], "defaults.cpu='0' is invalid"),
            (["set", "memory", "3GB"], "defaults.memory='3GB' is invalid"),
            (["set", "disk", "20"], "defaults.disk='20' is invalid"),
            (["set", "ttl", "59"], "TTL must be 60 seconds to 30 days"),
            (
                ["set", "disposable", "yes"],
                "defaults.disposable='yes' is invalid: Use true or false",
            ),
            (["set", "disposable", "True"], "defaults.disposable='True' is invalid"),
            (["set", "location", "missing"], "Unknown location"),
            (["set", "location", "bad name"], "Location names"),
        ]:
            rejected = cli("config", *arguments)
            assert rejected.returncode == 1 and error in rejected.stderr, (arguments, rejected)
            assert str(config) in rejected.stderr or "location" in arguments, rejected
        assert cli("config", "set", "colour", "red").returncode == 2
        saved = config.read_text()
        # A broken file fails new before any host is contacted, naming the file and key.
        for document, error in [
            ({"defaults": {"cpu": 0}}, "defaults.cpu=0 is invalid"),
            ({"defaults": {"cpu": True}}, "defaults.cpu=True is invalid"),
            ({"defaults": {"disk": 20}}, "defaults.disk=20 is invalid"),
            ({"defaults": {"disposable": 1}}, "defaults.disposable=1 is invalid"),
            ({"defaults": {"colour": "red"}}, "unknown key defaults.colour"),
            ({"defaults": []}, '{"defaults"'),
            ({"lab": {"ssh": "x"}}, '{"defaults"'),
            ({"defaults": {"location": "gone"}}, "unknown location gone"),
        ]:
            config.write_text(json.dumps(document))
            rejected = cli("new", "--purpose", "x")
            assert rejected.returncode == 1 and str(config) in rejected.stderr, rejected
            assert error in rejected.stderr and "Traceback" not in rejected.stderr, rejected
        config.write_text("{not json")
        rejected = cli("new")
        assert rejected.returncode == 1 and "invalid JSON" in rejected.stderr, rejected
        # The broken key can be repaired through the CLI.
        config.write_text(json.dumps({"defaults": {"cpu": 0, "ttl": 900}}))
        assert cli("config", "unset", "cpu").stdout == "defaults.cpu: 0 -> built-in default\n"
        assert json.loads(config.read_text()) == {"defaults": {"ttl": 900}}
        config.write_text(saved)
        # Concurrent edits to different keys must all survive.
        writers = [
            subprocess.Popen(
                [sys.executable, str(ROOT / "raft.py"), "config", "set", key, value],
                env={**os.environ, "HOME": home},
                stdout=subprocess.DEVNULL,
            )
            for key, value in [
                ("cpu", "2"),
                ("memory", "8GiB"),
                ("ttl", "120"),
                ("location", "lab"),
                ("disk", "shared"),
                ("disposable", "false"),
            ]
        ]
        assert all(writer.wait() == 0 for writer in writers)
        assert json.loads(config.read_text())["defaults"] == {
            "cpu": 2,
            "memory": "8GiB",
            "ttl": 120,
            "location": "lab",
            "disk": "shared",
            "disposable": False,
        }
        # JSON booleans read back as the CLI's true and false.
        assert cli("config", "set", "disposable", "true").stdout == (
            "defaults.disposable: false -> true\n"
        )
        # raft config reports it with its source; the report's host half needs SSH.
        effective = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import json, raft; print(json.dumps(raft.effective_defaults()['disposable']))",
            ],
            cwd=ROOT,
            env={**os.environ, "HOME": home},
            text=True,
        )
        assert json.loads(effective) == {"value": True, "source": "config"}, effective
        for key in ["cpu", "memory", "ttl", "location", "disk", "disposable"]:
            assert cli("config", "unset", key).returncode == 0
        assert not config.exists(), "Removing every default left an empty file"
        assert not list(directory.glob(".raft-*")), "Staged configuration left behind"
        # Policy and note flag mistakes are rejected before any host is contacted.
        handle = "lab:rf-0123456789abcdef"
        for arguments, error in [
            (["policy", "lab", "--require", "owner"], "always need purpose"),
            (["policy", "lab", "--require", "purpose,colour"], "Give note fields from"),
            (["policy", "lab", "--require", ","], "Give note fields from"),
            (["policy", "missing", "--require", "none"], "Unknown location"),
            (["policy", "lab"], "Give --require FIELDS|none, --delete-stopped-after"),
            (["policy", "lab", "--delete-stopped-after", "0"], "positive whole number of hours"),
            (["policy", "lab", "--delete-stopped-after", "48h"], "positive whole number of hours"),
            # Both options are parsed before either changes the host.
            (
                ["policy", "lab", "--require", "none", "--delete-stopped-after", "-1"],
                "positive whole number of hours",
            ),
            (["fork", handle, "--ttl", "600", "--owner", "agent"], "need --purpose"),
            (["recover", "archive", "--location", "lab", "--next", "x"], "need --purpose"),
        ]:
            rejected = cli(*arguments)
            assert rejected.returncode == 1 and error in rejected.stderr, (arguments, rejected)
        assert cli("policy", "lab", "--clear").returncode == 2
    # The controller message names the missing flags and a complete command.
    program = (
        "import raft; print(raft.note_problem('lab', ['purpose', 'owner'], "
        "{'purpose': 'x'}, 'new')); print(raft.note_problem('lab', ['purpose'], "
        "{'purpose': 'x'}, 'new'))"
    )
    messages = subprocess.check_output([sys.executable, "-c", program], cwd=ROOT, text=True)
    assert messages == (
        'location lab requires --owner (host policy). Add it: raft new --purpose "..." '
        '--owner "..." [--next "..."]\nNone\n'
    ), messages
    # Run the exact host-side check locally; it needs only python3.
    for required, note, accepted in [
        ("", "", True),
        ("purpose", json.dumps({"purpose": "x"}), True),
        ("purpose,owner", json.dumps({"purpose": "x", "owner": "y"}), True),
        ("purpose,owner", json.dumps({"purpose": "x"}), False),
        ("purpose", "", False),
    ]:
        check = subprocess.run(
            [sys.executable, "-I", "-c", CHECK_NOTE_CODE, required, note],
            capture_output=True,
            text=True,
        )
        assert (check.returncode == 0) == accepted, (required, note, check)
        if not accepted:
            assert "Nothing was changed" in check.stderr, check
    # A configured disk on a host without own box disks gets the same refusal as
    # --disk, plus where the size came from. Run the exact host check against a
    # fake incus that reports no storage pools besides the shared one.
    with tempfile.TemporaryDirectory() as bin_directory:
        fake = Path(bin_directory) / "incus"
        fake.write_text("#!/bin/sh\necho '[\"/1.0/storage-pools/raft-data\"]'\n")
        fake.chmod(0o755)
        refusals = [
            subprocess.run(
                [sys.executable, "-I", "-c", RESERVE_BOX_DISK, "lab", "20", "image:abc", origin],
                env={**os.environ, "PATH": f"{bin_directory}:{os.environ['PATH']}"},
                capture_output=True,
                text=True,
            )
            for origin in ["", disk_origin("20GiB")]
        ]
    flag, configured = (refusal.stderr.strip() for refusal in refusals)
    assert all(refusal.returncode == 1 for refusal in refusals), refusals
    assert flag.startswith("lab has no raft-disks pool") and "--disk-pool SIZE" in flag, flag
    assert configured == flag + ". " + disk_origin("20GiB"), configured
    assert "defaults.disk in " in configured and "raft config unset disk" in configured
    print("Controller defaults, precedence, validation and note policy messages passed", flush=True)


# Stands in for incus and logger on PATH for the real host worker. It answers the
# worker's two reads from the state file and appends every call to the log file.
FAKE_HOST_TOOL = """import json, os, sys
from pathlib import Path
arguments = [Path(sys.argv[0]).name, *sys.argv[1:]]
with open(os.environ["RAFT_FAKE_LOG"], "a") as log:
    log.write(json.dumps(arguments) + "\\n")
state = json.loads(Path(os.environ["RAFT_FAKE_STATE"]).read_text())
if arguments[1:] == ["--project", "raft", "list", "--fast", "--format", "json"]:
    print(json.dumps(state["instances"]))
elif arguments[1:] == ["--project", "raft", "project", "list", "--format", "json"]:
    print(json.dumps([{"name": "default", "config": {}}, {"name": "raft", "config": state["project"]}]))
"""


def verify_retention_worker():
    """Run the real host worker against fake incus fixtures and check what it changes."""
    now = int(time.time())

    def box(name, status, stopped_at=None, raft="true"):
        config = {"user.raft": raft}
        if stopped_at is not None:
            config["user.raft.stopped-at"] = str(stopped_at)
        return {"name": name, "status": status, "config": config}

    instances = [
        # Running boxes are never deleted; an old stop time left behind is cleared.
        box("rf-running", "Running", now - 30 * 86400),
        box("rf-old", "Stopped", now - 3 * 3600),
        box("rf-recent", "Stopped", now - 3600),
        box("rf-unrecorded", "Stopped"),
        box("unrelated", "Stopped", now - 30 * 86400, raft="false"),
    ]

    def run_worker(project, *arguments):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            for tool in ["incus", "logger"]:
                (root / tool).write_text(f"#!{sys.executable}\n" + FAKE_HOST_TOOL)
                (root / tool).chmod(0o755)
            (root / "state.json").write_text(
                json.dumps({"instances": instances, "project": project})
            )
            started = int(time.time())
            response = subprocess.run(
                [sys.executable, "-I", str(ROOT / "deploy/raft-expire.py"), *arguments],
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "RAFT_FAKE_STATE": str(root / "state.json"),
                    "RAFT_FAKE_LOG": str(root / "calls.jsonl"),
                },
                capture_output=True,
                text=True,
            )
            calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
        # Leave out the two reads and the stale stop time cleared on the running box;
        # normalize the recorded stop time to "now".
        clear = ["incus", "--project", "raft", "config", "unset", "rf-running"]
        cleared.append(clear + ["user.raft.stopped-at"] in calls)
        changes = []
        for call in calls:
            if call[-2:] == ["--format", "json"] or call == clear + ["user.raft.stopped-at"]:
                continue
            if call[-1].startswith("user.raft.stopped-at="):
                assert started <= int(call[-1].partition("=")[2]) <= int(time.time()), call
                call[-1] = "user.raft.stopped-at=now"
            changes.append(call)
        return response, changes

    cleared = []
    record = ["incus", "--project", "raft", "config", "set", "rf-unrecorded"]
    record.append("user.raft.stopped-at=now")
    two_hours = {"user.raft.delete-stopped-after": "2", "user.raft.delete-stopped-after.since": ""}
    # A 2-hour policy set long ago deletes the box stopped 3 hours ago and logs it.
    response, changes = run_worker({**two_hours, "user.raft.delete-stopped-after.since": "0"})
    assert response.returncode == 0, response
    assert changes[0] == ["incus", "--project", "raft", "delete", "rf-old"], changes
    assert changes[1][:3] == ["logger", "-t", "raft"] and "rf-old" in changes[1][3], changes
    assert "under the 2-hour retention" in changes[1][3] and changes[2:] == [record], changes
    assert cleared[-1], "the worker kept a stale stop time on a running box"
    assert changes[1][3] in response.stdout, response
    # Set an hour ago, the policy's clock starts then: nothing is deleted yet.
    since = str(now - 3600)
    response, changes = run_worker({**two_hours, "user.raft.delete-stopped-after.since": since})
    assert response.returncode == 0 and changes == [record], (response, changes)
    # Without a policy nothing is deleted, but stop times are still recorded.
    response, changes = run_worker({})
    assert response.returncode == 0 and changes == [record], (response, changes)
    # Prune mode only selects; it records and retains nothing else.
    response, changes = run_worker(
        {**two_hours, "user.raft.delete-stopped-after.since": "0"}, "--prune-days", "1"
    )
    assert response.returncode == 0 and changes == [], (response, changes)
    # An invalid policy deletes nothing, still enforces deadlines, and fails the run.
    instances.append(
        {
            "name": "rf-expired",
            "status": "Running",
            "config": {"user.raft": "true", "user.raft.expires": "1"},
        }
    )
    response, changes = run_worker({**two_hours, "user.raft.delete-stopped-after": "48h"})
    assert response.returncode == 1 and "Invalid retention" in response.stderr, response
    assert ["incus", "--project", "raft", "stop", "rf-expired", "--timeout", "30"] in changes
    assert not any("delete" in call for call in changes), changes
    print("Host worker retention, stop-time recording and grace period passed", flush=True)


def verify_host_preflight():
    """Check onboarding decisions and refusals that need no host."""
    ready = {
        "kernel": "Linux",
        "os": "ubuntu",
        "version": "24.04",
        "arch": "arm64",
        "cpus": "2",
        # MemTotal of an 8 GiB VM, in KiB.
        "memory": str(8_131_000),
        "disk": str(90 * 1024**2),
        "sudo": "yes",
        "python3": "yes",
        "deployed": "no",
    }
    assert host_problems(ready) == []
    # An existing deployment already owns its pool, so free disk no longer gates it.
    assert host_problems({**ready, "disk": "0", "deployed": "yes"}) == []
    # Own box disks are opt-in; enabling them adds the pool size to the minimum.
    assert host_problems({**ready, "disk": str(95 * 1024**2)}, "10GiB") == []
    problems = host_problems(ready, "40GiB")
    assert len(problems) == 1 and "120 GiB free disk" in problems[0], problems
    darwin = host_problems({"kernel": "Darwin", "sudo": "no"})
    assert len(darwin) == 1 and "macOS" in darwin[0], darwin
    for change, reason in [
        ({"os": "debian", "version": "13"}, "Ubuntu 22.04 or 24.04"),
        ({"version": "20.04"}, "Ubuntu 22.04 or 24.04"),
        ({"arch": "armhf"}, "ARM64 or AMD64"),
        ({"sudo": "no"}, "passwordless sudo"),
        ({"python3": "no"}, "python3"),
        ({"cpus": "1"}, "2 CPUs"),
        ({"memory": str(6 * 1024**2)}, "8 GiB RAM"),
        ({"disk": str(40 * 1024**2)}, "80 GiB"),
    ]:
        problems = host_problems({**ready, **change})
        assert len(problems) == 1 and reason in problems[0], (change, problems)
    with tempfile.TemporaryDirectory() as home:
        config = Path(home) / ".config/raft/incus.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"lab": {"ssh": "lab-host", "image": ""}}))
        # Each refusal happens before SSH; the unroutable target proves no host is contacted.
        for arguments, error in [
            (["lab"], "already configured"),
            (["bad name"], "Location names"),
            (["spare", "--image-archive", str(Path(home) / "missing.tar.gz")], "not found"),
        ]:
            rejected = subprocess.run(
                [sys.executable, str(ROOT / "raft.py"), "host", "add", *arguments, "--ssh", "x"],
                env={**os.environ, "HOME": home},
                capture_output=True,
                text=True,
            )
            assert rejected.returncode == 1 and error in rejected.stderr, rejected
    print("Host preflight decisions and pre-SSH onboarding refusals passed", flush=True)


def verify_readiness(location, expect_stale=False):
    # Isolate the selected host without losing the controller's verified SSH config.
    with tempfile.TemporaryDirectory(prefix="raft-doctor-test-") as work:
        home = Path(work)
        (home / ".ssh").symlink_to(Path.home() / ".ssh", target_is_directory=True)
        config = home / ".config/raft/incus.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"offline": {"ssh": "raft-offline.invalid"}, location: settings(location)})
        )
        response = live.raft(
            "doctor", "--location", location, check=False, env={**os.environ, "HOME": str(home)}
        )
    assert location + ": " in response.stdout, response
    if expect_stale:
        assert response.returncode == 1, response
        assert (
            f"raft: {location}: Host expiry worker differs from this CLI; "
            "deploy the current Raft checkout first"
        ) in response.stderr, response
    else:
        assert response.returncode == 0, response
        assert "saved boxes; system containers (shared host kernel)" in response.stdout, response
    print(f"{location}: doctor {'rejected stale worker' if expect_stale else 'passed'}", flush=True)


def verify_read_reports(location):
    """Compare batched reads with independent native reads on a quiescent host."""
    policy, boxes = host_inventory(location)
    assert policy == host_policy(location)
    assert sorted(boxes, key=lambda box: box["name"]) == sorted(
        inventory(location), key=lambda box: box["name"]
    )
    with tempfile.TemporaryDirectory(prefix="raft-config-test-") as work:
        home = Path(work)
        (home / ".ssh").symlink_to(Path.home() / ".ssh", target_is_directory=True)
        config = home / ".config/raft/incus.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"offline": {"ssh": "raft-offline.invalid"}, location: settings(location)})
        )
        env = {**os.environ, "HOME": str(home), "RAFT_NO_UPDATE_CHECK": "1"}
        report = live.raft("config", "--location", location, env=env)
        assert json.loads(report.stdout)["policy"] == {location: policy}
        rejected = live.raft(
            "config", "--location", location, "set", "cpu", "2", env=env, check=False
        )
        assert rejected.returncode == 1 and "controller-wide" in rejected.stderr
        assert not (config.parent / "config.json").exists()
    print(f"{location}: batched metadata and offline-host isolation passed", flush=True)


def verify_transaction_stdin(location):
    program = """import sys, tempfile
from raft import transaction
assert transaction(sys.argv[1], 'cat') == ''
with tempfile.TemporaryFile() as output:
    transaction(sys.argv[1], 'cat', stdout=output)
    output.seek(0)
    assert output.read() == b''
"""
    with subprocess.Popen(
        [sys.executable, "-c", program, location],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            process.stdin.write(b"must-not-reach-the-host\n")
            process.stdin.flush()
            process.wait(timeout=30)
        finally:
            # Release even a regressed remote reader before leaving this test.
            process.stdin.close()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise
        assert process.returncode == 0, process.stderr.read().decode()
    assert transaction(location, "printf lock-released") == "lock-released"
    print(f"{location}: open stdin ignored and lifecycle lock released", flush=True)


def verify_exec_stdin(create):
    """Piped stdin reaches the guest command intact; the box lookup must not drain it."""
    handle = create("--purpose", "Exec stdin check")
    payload = os.urandom(300_000)
    response = subprocess.run(
        [*live.CLI, "exec", handle, "--", "sha256sum"],
        input=payload,
        capture_output=True,
        env=live.CONTROLLER_ENV,
    )
    assert response.returncode == 0, response.stderr.decode()
    expected = subprocess.run(["sha256sum"], input=payload, capture_output=True).stdout
    assert response.stdout == expected, (response.stdout, expected)
    print("exec forwarded piped stdin byte for byte", flush=True)


def verify_note_policy(location, create, listed_note, fixtures):
    """Require note fields on the host, prove every note path honors it, then clear it."""
    source = create("--memory", "1GiB", "--purpose", "Policy fork source")
    live.raft("stop", source)
    changed = live.raft("policy", location, "--require", "purpose,owner")
    try:
        assert changed.stdout == f"{location}: required note fields none -> purpose,owner\n"
        report = json.loads(live.raft("config").stdout)
        assert report["policy"][location]["require"] == ["purpose", "owner"], report
        assert " by " in report["policy"][location]["requireChanged"], report
        saved = len(inventory(location))
        for arguments, error in [
            (["new", "--location", location, "--purpose", "x"], "requires --owner (host policy)"),
            (["new", "--location", location], "requires --purpose and --owner (host policy)"),
            (["fork", source, "--ttl", "600"], "Forks keep the source note"),
            (["fork", source, "--ttl", "600", "--purpose", "x"], "requires --owner"),
            (["note", source, "--clear"], "Clearing would leave the box without them"),
            (["note", source, "--purpose", "x", "--next", "y"], "requires --owner"),
            # Rejected before the archive is read or uploaded.
            (["recover", "missing.tar.gz", "--location", location, "--purpose", "x"], "--owner"),
        ]:
            rejected = live.raft(*arguments, check=False)
            assert rejected.returncode == 1 and error in rejected.stderr, (arguments, rejected)
            assert "Add it: raft " in rejected.stderr or "Add them: raft " in rejected.stderr
        assert len(inventory(location)) == saved, "A rejected note created a box"
        assert listed_note(source) == {"purpose": "Policy fork source"}
        # The host repeats the check under its lock, so a controller that skips the
        # precheck still cannot write a note that lacks a required field.
        try:
            transaction(
                location,
                'note="$1"\n' + READ_NOTE_POLICY + CHECK_NOTE,
                json.dumps({"purpose": "x"}),
            )
        except RuntimeError as error:
            assert "requires box note fields purpose, owner" in str(error), error
        else:
            raise AssertionError("Host accepted a note without a required field")
        owned = create("--memory", "1GiB", "--purpose", "Policy", "--owner", "verify")
        assert listed_note(owned) == {"purpose": "Policy", "owner": "verify"}
        live.raft("destroy", owned)
        fixtures.remove(owned)
        # Note flags replace the source note on the fork.
        fork = live.raft(
            "fork", source, "--ttl", "600", "--purpose", "Policy fork", "--owner", "verify"
        ).stdout.strip()
        fixtures.append(fork)
        assert listed_note(fork) == {"purpose": "Policy fork", "owner": "verify"}
        live.raft("destroy", fork)
        fixtures.remove(fork)
        live.raft("note", source, "--purpose", "Policy fork source", "--owner", "verify")
        assert listed_note(source) == {"purpose": "Policy fork source", "owner": "verify"}
    finally:
        cleared = live.raft("policy", location, "--require", "none")
    assert cleared.stdout == f"{location}: required note fields purpose,owner -> none\n"
    assert json.loads(live.raft("config").stdout)["policy"][location]["require"] == []
    live.raft("note", source, "--clear")
    live.raft("destroy", source)
    fixtures.remove(source)
    print(f"{location}: host note policy for new, fork, note and recover passed", flush=True)


def verify_retention(location, create, exists, worker):
    """Set a 1-hour stopped-box retention, prove the worker deletes only a box stopped
    past it, records stop times Raft missed and logs to the journal, then clear it."""
    host = settings(location)["ssh"]
    started = int(time.time())
    running = create("--memory", "1GiB", "--purpose", "Retention: running box")
    kept = create("--memory", "1GiB", "--purpose", "Retention: recently stopped box")
    expired = create("--memory", "1GiB", "--purpose", "Retention: long stopped box")
    unrecorded = create("--memory", "1GiB", "--purpose", "Retention: box stopped outside Raft")
    live.raft("stop", kept)
    live.raft("stop", expired)
    # A native stop records no stop time; the worker starts that box's clock.
    incus(location, "stop", parse_handle(unrecorded)[1], "--timeout", "30", locked=True)

    def stopped_at(handle):
        return incus(
            location, "config", "get", parse_handle(handle)[1], "user.raft.stopped-at"
        ).strip()

    def tick(done):
        # The staged worker runs once; the installed one runs on its 15-second timer.
        if worker:
            transaction(location, 'exec python3 "$1"', worker)
        deadline = time.monotonic() + 50
        while not done():
            if time.monotonic() > deadline:
                raise RuntimeError("Host expiry worker did not apply the retention policy")
            time.sleep(1)

    def listed():
        lines = live.raft("list", "--location", location).stdout.splitlines()
        return {record["box"]: record for record in map(json.loads, lines)}

    if worker:
        # Older workers ignore retention, so the CLI refuses to set it for them.
        rejected = live.raft("policy", location, "--delete-stopped-after", "1", check=False)
        assert rejected.returncode and "deploy the current Raft checkout" in rejected.stderr
        incus(
            location,
            "project",
            "set",
            "raft",
            f"{RETENTION_KEY}=1",
            f"{RETENTION_SINCE_KEY}={int(time.time())}",
            locked=True,
        )
    else:
        changed = live.raft("policy", location, "--delete-stopped-after", "1")
        assert changed.stdout == f"{location}: delete stopped boxes after never -> 1 hour\n"
    try:
        policy = json.loads(live.raft("config").stdout)["policy"][location]
        assert policy["deleteStoppedAfterHours"] == 1, policy
        since = policy["deleteStoppedAfterSince"]
        assert started <= since <= time.time(), policy
        # A box stopped before the policy gets the full hour from the change.
        records = listed()
        assert records[kept]["deleteAt"] == str(since + 3600), records[kept]
        assert records[running]["deleteAt"] == "", records[running]
        # Hours are the smallest unit, so backdate the policy and one stop by two
        # hours instead of waiting: only that box is then past the retention.
        backdated = int(time.time()) - 2 * 3600
        incus(location, "project", "set", "raft", f"{RETENTION_SINCE_KEY}={backdated}", locked=True)
        incus(
            location,
            "config",
            "set",
            parse_handle(expired)[1],
            f"user.raft.stopped-at={backdated}",
            locked=True,
        )
        tick(lambda: not exists(expired) and stopped_at(unrecorded))
        assert exists(kept) and exists(running) and exists(unrecorded)
        # The clock of a box stopped outside Raft starts when the worker sees it.
        assert int(stopped_at(unrecorded)) >= started
        assert listed()[unrecorded]["deleteAt"] == str(int(stopped_at(unrecorded)) + 3600)
        deleted = f"Deleted box {parse_handle(expired)[1]}, stopped since "
        command = ["sudo", "-n", "journalctl", "-t", "raft", "--since", f"@{started}", "-o", "cat"]
        deadline = time.monotonic() + 10
        while True:
            journal = subprocess.check_output(["ssh", "-T", host, shlex.join(command)], text=True)
            if deleted in journal or time.monotonic() > deadline:
                break
            time.sleep(1)
        assert deleted in journal, journal
        if not worker:
            assert "Stopped-box retention: hours: '' -> '1'" in journal, journal
    finally:
        if worker:
            incus(
                location,
                "project",
                "set",
                "raft",
                f"{RETENTION_KEY}=",
                f"{RETENTION_SINCE_KEY}=",
                locked=True,
            )
        else:
            cleared = live.raft("policy", location, "--delete-stopped-after", "never")
    if not worker:
        assert cleared.stdout == f"{location}: delete stopped boxes after 1 hour -> never\n"
    policy = json.loads(live.raft("config").stdout)["policy"][location]
    assert policy["deleteStoppedAfterHours"] is None, policy
    assert listed()[kept]["deleteAt"] == ""
    print(
        f"{location}: stopped-box retention deleted only the expired box, "
        "recorded missing stop times, logged and cleared",
        flush=True,
    )


def verify(location, isolated_worker=False):
    if inventory(location):
        raise RuntimeError(
            "Retention verification requires an empty Raft host; existing boxes are untouched"
        )
    if not isolated_worker:
        rejected = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--location",
                location,
                "--isolated-worker",
            ],
            capture_output=True,
            text=True,
        )
        assert rejected.returncode and "targets older deployments" in rejected.stderr
        assert not inventory(location), "Rejected verification mode allocated a box"
    fixtures = []
    worker = None
    host = settings(location)["ssh"]
    if isolated_worker:
        protocol = subprocess.check_output(
            [
                "ssh",
                "-T",
                host,
                shlex.join(
                    [
                        "sudo",
                        "-n",
                        "python3",
                        "-c",
                        "import runpy; print(runpy.run_path('/usr/local/lib/raft/expire-worker').get('PROTOCOL'))",
                    ]
                ),
            ],
            text=True,
        ).strip()
        if protocol == "2":
            raise RuntimeError(
                "--isolated-worker targets older deployments; use normal verification on a current host"
            )
        worker = subprocess.check_output(
            ["ssh", "-T", host, "mktemp /tmp/raft-feature-worker.XXXXXXXX"], text=True
        ).strip()
        subprocess.run(
            ["ssh", "-T", host, shlex.join(["sh", "-c", 'cat > "$1"', "raft", worker])],
            input=(ROOT / "deploy/raft-expire.py").read_bytes(),
            check=True,
        )

    def cleanup_command(*arguments):
        if worker:
            return transaction(location, 'exec python3 "$@"', worker, *arguments)
        command = ["prune", "--location", location]
        if arguments:
            command += ["--older-than", arguments[1]]
            if "--yes" in arguments:
                command += ["--yes"]
        else:
            command = ["gc", "--location", location]
        return live.raft(*command).stdout

    def create(*arguments):
        # Pin the sizes a controller's config.json could otherwise default.
        handle = live.raft(
            "new",
            "--location",
            location,
            "--ttl",
            "1800",
            "--cpu",
            "1",
            "--disk",
            "shared",
            *arguments,
        ).stdout.strip()
        fixtures.append(handle)
        return handle

    def metadata(handle):
        return json.loads(live.raft("info", handle).stdout)

    def exists(handle):
        return any(box["name"] == parse_handle(handle)[1] for box in inventory(location))

    try:
        verify_readiness(location, expect_stale=isolated_worker)
        verify_transaction_stdin(location)
        verify_exec_stdin(create)
        # A location note goes to stderr; stdout stays the bare handle for scripts.
        # A private HOME copy carries the note, leaving the operator config untouched;
        # OpenSSH reads keys and aliases from the passwd home, not HOME.
        with tempfile.TemporaryDirectory() as home:
            noted = Path(home) / ".config/raft/incus.json"
            noted.parent.mkdir(parents=True)
            noted.write_text(
                json.dumps({location: {**settings(location), "note": "feature verifier note"}})
            )
            response = live.raft(
                "new", "--location", location, "--ttl", "600", env={**os.environ, "HOME": home}
            )
        handle = response.stdout.strip()
        fixtures.append(handle)
        assert response.stdout == handle + "\n", response.stdout
        assert response.stderr == f"{location} note: feature verifier note\n", response.stderr
        live.raft("destroy", handle)
        fixtures.remove(handle)
        print(f"{location}: noted new kept stdout to the handle and the note on stderr", flush=True)
        box = create("--memory", "1GiB", "--purpose", " Feature verification ", "--owner", "verify")

        def listed_note(handle):
            for line in live.raft("list", "--location", location).stdout.splitlines():
                record = json.loads(line)
                if record["box"] == handle:
                    return record["note"]
            raise AssertionError(f"{handle} missing from list")

        assert listed_note(box) == {"purpose": "Feature verification", "owner": "verify"}
        # Each note edit replaces the whole note.
        live.raft("note", box, "--purpose", "Feature verification", "--next", "Destroy at the end")
        assert listed_note(box) == {"purpose": "Feature verification", "next": "Destroy at the end"}
        live.raft("note", box, "--clear")
        assert listed_note(box) is None
        live.raft("note", box, "--purpose", "Feature verification")
        live.wait_running(box)
        _, name = parse_handle(box)
        print(
            f"{location}: box note template, listing, replacement and clearing passed", flush=True
        )
        verify_note_policy(location, create, listed_note, fixtures)
        with tempfile.TemporaryDirectory(prefix="raft-tree-test-") as work:
            root = Path(work)
            source = root / "source with spaces"
            source.mkdir()
            (source / "binary").write_bytes(bytes(range(256)) * 1024)
            (source / "link").symlink_to("binary")
            os.link(source / "binary", source / "hard")
            (source / "empty").mkdir()
            (source / "run").write_text("#!/bin/sh\necho tree\n")
            (source / "run").chmod(0o755)
            guest = "/workspace/tree with spaces"
            live.raft("upload", box, str(source), guest, "--recursive")
            output = root / "download"
            live.raft("download", box, guest, str(output), "--recursive")
            assert (output / "binary").read_bytes() == (source / "binary").read_bytes()
            assert (output / "link").is_symlink() and (output / "link").readlink() == Path("binary")
            assert (output / "hard").stat().st_ino == (output / "binary").stat().st_ino
            assert (output / "empty").is_dir() and (output / "run").stat().st_mode & 0o777 == 0o755
            assert live.raft(
                "upload", box, str(source), guest, "--recursive", check=False
            ).returncode
            assert live.raft(
                "download", box, guest, str(output), "--recursive", check=False
            ).returncode
            (source / "unsafe").symlink_to("../../escape")
            assert live.raft(
                "upload", box, str(source), "/workspace/rejected", "--recursive", check=False
            ).returncode
            live.execute(box, "test", "!", "-e", "/workspace/rejected")
            live.execute(box, "ln", "-s", "/etc/shadow", guest + "/unsafe")
            assert live.raft(
                "download", box, guest, str(root / "rejected"), "--recursive", check=False
            ).returncode
            assert not (root / "rejected").exists()
            live.execute(box, "rm", guest + "/unsafe")
            complete = two_file_archive()
            interrupted = root / "interrupted.tar"
            interrupted.write_bytes(complete[:1024])
            with interrupted.open("rb") as truncated:
                rejected = remote(
                    location,
                    [
                        "exec",
                        name,
                        "--",
                        "python3",
                        "-c",
                        Path(raft_files.__file__).read_text(),
                        "unpack",
                        "/workspace/truncated",
                        "--size",
                        str(len(complete)),
                    ],
                    stdin=truncated,
                )
            assert rejected.returncode and b"size mismatch" in rejected.stderr
            live.execute(box, "test", "!", "-e", "/workspace/truncated")
            assert not live.execute(
                box, "find", "/workspace", "-maxdepth", "1", "-name", ".raft-tree-*"
            )
        print(
            f"{location}: recursive trees, permissions, links, overwrite and unsafe-link rejection passed",
            flush=True,
        )
        live.execute(box, "bash", "-ec", "echo original > /workspace/proof")
        live.raft("stop", box)
        assert metadata(box)["config"].get("user.raft.stopped-at")
        live.raft("snapshot", box, "prepared")
        live.raft("resume", box, "--ttl", "1800", "--cpu", "2", "--memory", "4GiB")
        resized = metadata(box)
        assert resized["expanded_config"]["limits.cpu"] == "2"
        assert resized["expanded_config"]["limits.memory"] == "4GiB"
        assert not resized["config"].get("user.raft.stopped-at")
        assert live.execute(box, "cat", "/sys/fs/cgroup/memory.max") == str(4 * 1024**3)
        assert (
            live.execute(box, "python3", "-c", "import os; print(len(os.sched_getaffinity(0)))")
            == "2"
        )
        assert live.execute(box, "cat", "/workspace/proof") == "original"
        assert live.raft("resume", box, "--ttl", "600", "--cpu", "1", check=False).returncode
        assert metadata(box)["expanded_config"]["limits.cpu"] == "2"
        template = live.raft("new", "--from", box + "/prepared", "--ttl", "600").stdout.strip()
        fixtures.append(template)
        live.wait_running(template)
        assert live.execute(template, "cat", "/workspace/proof") == "original"
        assert (
            metadata(template)["config"]["volatile.eth0.hwaddr"]
            != metadata(box)["config"]["volatile.eth0.hwaddr"]
        )
        assert metadata(template)["config"]["user.raft.disposable"] == "false"
        assert listed_note(template) is None, "new --from inherited the source note"
        # A template keeps the snapshot's sizes, not the box's later ones or the defaults.
        assert metadata(template)["expanded_config"]["limits.cpu"] == "1"
        assert metadata(template)["expanded_config"]["limits.memory"] == "1GiB"
        live.raft("snapshot-delete", box, "prepared")
        assert live.execute(template, "cat", "/workspace/proof") == "original"
        live.raft("destroy", template)
        assert "prepared" not in live.raft("snapshots", box).stdout
        assert live.raft("new", "--from", box + "/prepared", check=False).returncode
        assert live.raft("snapshot-delete", box, "missing", check=False).returncode
        live.raft("stop", box)
        fork_args = ["fork", box, "--ttl", "600", "--cpu", "1", "--memory", "1GiB"]
        if not worker:
            fork_args.append("--disposable")
        fork = live.raft(*fork_args).stdout.strip()
        fixtures.append(fork)
        assert metadata(fork)["expanded_config"]["limits.memory"] == "1GiB"
        assert listed_note(fork) == {"purpose": "Feature verification"}, "fork lost the note"
        assert metadata(fork)["expanded_config"]["limits.cpu"] == "1"
        assert metadata(box)["expanded_config"]["limits.memory"] == "4GiB"
        if worker:
            incus(
                location,
                "config",
                "set",
                parse_handle(fork)[1],
                "user.raft.disposable=true",
                locked=True,
            )
        live.raft("stop", fork)
        assert not exists(fork)
        live.raft("resume", box, "--ttl", "1800", "--memory", "1GiB")
        assert metadata(box)["expanded_config"]["limits.cpu"] == "2"
        print(
            f"{location}: live-source templates, snapshot deletion and resume/fork sizing passed",
            flush=True,
        )
        if worker:
            rejected = live.raft("new", "--location", location, "--disposable", check=False)
            assert rejected.returncode and "deploy the current Raft checkout" in rejected.stderr
            disposable = create("--memory", "1GiB")
            incus(
                location,
                "config",
                "set",
                parse_handle(disposable)[1],
                "user.raft.disposable=true",
                locked=True,
            )
            rejected = live.raft("prune", "--location", location, "--older-than", "1", check=False)
            assert rejected.returncode and "deploy the current Raft checkout" in rejected.stderr
        else:
            disposable = create("--disposable", "--memory", "1GiB")
        _, disposable_name = parse_handle(disposable)
        if worker:
            transaction(
                location,
                'incus --project raft config set "$1" user.raft.expires=1; python3 "$2"',
                disposable_name,
                worker,
            )
        else:
            incus(location, "config", "set", disposable_name, "user.raft.expires=1", locked=True)
            deadline = time.monotonic() + 50
            while exists(disposable):
                if time.monotonic() > deadline:
                    raise RuntimeError("Timer did not delete expired disposable box")
                time.sleep(1)
        assert not exists(disposable)
        recent = create("--memory", "1GiB")
        old = create("--memory", "1GiB")
        live.raft("stop", recent)
        live.raft("stop", old)
        _, old_name = parse_handle(old)
        incus(
            location,
            "config",
            "set",
            old_name,
            f"user.raft.stopped-at={int(time.time()) - 3 * 86400}",
            locked=True,
        )
        incus(location, "config", "set", old_name, "user.raft=false", locked=True)
        try:
            assert old_name not in cleanup_command("--prune-days", "2")
        finally:
            incus(location, "config", "set", old_name, "user.raft=true", locked=True)
        preview = cleanup_command("--prune-days", "2")
        assert (
            old_name in preview and parse_handle(recent)[1] not in preview and name not in preview
        )
        assert exists(old)
        cleanup_command("--prune-days", "2", "--yes")
        assert not exists(old) and exists(recent) and exists(box)
        _, recent_name = parse_handle(recent)
        incus(location, "config", "unset", recent_name, "user.raft.stopped-at", locked=True)
        assert recent_name not in cleanup_command("--prune-days", "1")
        assert live.raft("prune", "--older-than", "0", check=False).returncode
        print(
            f"{location}: disposable deletion, preview/apply retention and exclusions passed",
            flush=True,
        )
        verify_retention(location, create, exists, worker)
    finally:
        for handle in reversed(fixtures):
            if exists(handle):
                live.raft("destroy", handle)
        if worker:
            subprocess.run(
                [
                    "ssh",
                    "-T",
                    host,
                    shlex.join(
                        [
                            "python3",
                            "-c",
                            "from pathlib import Path; import sys; Path(sys.argv[1]).unlink()",
                            worker,
                        ]
                    ),
                ],
                check=True,
            )
        print(f"{location}: feature fixtures and staged worker removed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location")
    parser.add_argument("--isolated-worker", action="store_true")
    args = parser.parse_args()
    verify_location_notes()
    verify_controller_defaults()
    verify_retention_worker()
    verify_host_preflight()
    verify_archives()
    if args.location:
        verify_read_reports(args.location)
        verify(args.location, args.isolated_worker)
