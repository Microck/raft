"""Exercise disposable lifecycle, retention, templates, resizing and tree transfers.

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
    host_problems,
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
        config.write_text(json.dumps({location: settings(location)}))
        response = live.raft("doctor", check=False, env={**os.environ, "HOME": str(home)})
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
        if protocol == "1":
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
        handle = live.raft(
            "new", "--location", location, "--ttl", "1800", *arguments
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
    verify_host_preflight()
    verify_archives()
    if args.location:
        verify(args.location, args.isolated_worker)
