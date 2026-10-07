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
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import incus, inventory, parse_handle, settings, transaction  # noqa: E402
from raft_files import pack_directory, publish, unpack_directory  # noqa: E402

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("verify-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


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
                unpack_directory(stream, root / "output")
            except (ValueError, tarfile.FilterError):
                pass
            else:
                raise AssertionError("Unsafe archive accepted: " + name)
            assert not (root / "output").exists()
            assert not list(root.glob(".raft-tree-*"))
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
        outside = root / "outside"
        outside.mkdir()
        link = root / "destination"
        link.symlink_to(outside, target_is_directory=True)
        try:
            unpack_directory(io.BytesIO(), link)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Destination symlink was followed")
        assert link.is_symlink() and not list(outside.iterdir())
    print(
        "Archive traversal, links, special files and destination symlink rejection passed",
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
        box = create("--memory", "1GiB")
        live.wait_running(box)
        _, name = parse_handle(box)
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
        assert metadata(template)["expanded_config"]["limits.cpu"] == "1"
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
    verify_archives()
    if args.location:
        verify(args.location, args.isolated_worker)
