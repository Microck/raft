"""Fault-injection, real signal/file tests, and optional native-host CI-run verification."""

import argparse
import contextlib
import io
import json
import os
import re
import select
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import raft  # noqa: E402
import raft_files  # noqa: E402
import raft_run  # noqa: E402


class Transport:
    """Explicit orchestration fault fixture; not a native Incus verification."""

    def __init__(self, fault="", status=0):
        self.fault, self.status = fault, status
        self.events = []

    def new(self, args, *, run_id, on_allocate):
        self.events.append("allocate")
        assert args.disposable is True and args.ttl == args.timeout + 120
        self.token = run_id
        on_allocate("lab", "rf-fixture")
        if self.fault == "create":
            raise RuntimeError("uncertain SSH create outcome")
        self.wait("create")
        return "lab:rf-fixture"

    def wait(self, phase):
        if self.fault == "timeout-" + phase:
            time.sleep(2)
        if self.fault == "signal-" + phase:
            os.kill(os.getpid(), signal.SIGTERM)

    def execute(self, _location, _name, command, **_kwargs):
        if command == ["true"]:
            self.events.append("ready")
            return SimpleNamespace(returncode=0)
        self.events.append("payload")
        self.wait("payload")
        return SimpleNamespace(returncode=self.status)

    def transfer_tree(self, args, _location, _name):
        self.events.append(args.action + ":" + str(args.source))
        self.wait(args.action)
        if self.fault == args.action or (self.fault == "first-pull" and args.source == "/first"):
            raise RuntimeError(args.action + " failed")

    def transaction(self, _location, _script, name, token, code):
        self.events.append("cleanup")
        assert name == "rf-fixture" and token == self.token and code == raft_run.CLEANUP
        self.wait("cleanup")
        if self.fault == "cleanup":
            raise RuntimeError("cleanup SSH failed")


class RunTests(unittest.TestCase):
    def invoke(self, transport, *options, timeout=0.1):
        args = raft.parser().parse_args(["run", *options, "--", "echo", "hello"])
        args.timeout = timeout
        old = {
            number: signal.getsignal(number)
            for number in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)
        }
        with contextlib.redirect_stderr(io.StringIO()), patch.object(raft_run, "GRACE", 0.1):
            result = raft_run.run(args, transport)
        for number, handler in old.items():
            self.assertEqual(signal.getsignal(number), handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))
        return result

    def test_receive_deadline_after_stdout_eof_kills_child(self):
        class ArmOnWrite(io.BytesIO):
            def write(self, data):
                signal.setitimer(signal.ITIMER_REAL, 0.1)
                return super().write(data)

        previous = signal.getsignal(signal.SIGALRM)

        def expired(_number, _frame):
            raise raft_run.Stopped(124, "test deadline")

        began = time.monotonic()
        try:
            signal.signal(signal.SIGALRM, expired)
            with self.assertRaises(raft_run.Stopped):
                raft.receive(
                    [
                        sys.executable,
                        "-c",
                        "import os,time; print('ready', flush=True); os.close(1); time.sleep(5)",
                    ],
                    ArmOnWrite(),
                )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        self.assertLess(time.monotonic() - began, 2, "EOF-before-exit must not evade the deadline")

    def test_success_and_payload_status(self):
        for status, expected in [(0, 0), (23, 23), (-signal.SIGTERM, 143)]:
            api = Transport(status=status)
            self.assertEqual(self.invoke(api), expected)
            self.assertEqual(api.events, ["allocate", "ready", "payload", "cleanup"])

    def test_uncertain_creation_and_upload_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            for fault in ["create", "upload"]:
                api = Transport(fault)
                self.assertEqual(self.invoke(api, "--push-all", directory), 1)
                self.assertEqual(api.events[-1], "cleanup")
                self.assertNotIn("payload", api.events)

    def test_signal_and_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            for phase in ["create", "upload", "payload"]:
                for prefix, expected in [("timeout-", 124), ("signal-", 143)]:
                    api = Transport(prefix + phase)
                    self.assertEqual(self.invoke(api, "--push-all", directory), expected)
                    self.assertEqual(api.events[-1], "cleanup")

    def test_artifacts_and_error_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            options = [
                "--pull",
                "/first",
                directory + "/one",
                "--pull",
                "/second",
                directory + "/two",
            ]
            for failure in [
                "first-pull",
                "download",
                "timeout-download",
                "cleanup",
                "timeout-cleanup",
            ]:
                for payload, expected in [(0, 1), (23, 23)]:
                    api = Transport(failure, status=payload)
                    self.assertEqual(self.invoke(api, *options), expected)
                    self.assertEqual(api.events[-1], "cleanup")
                    if failure == "first-pull":
                        self.assertIn("download:/second", api.events)

    def test_invalid_paths_and_command_do_not_allocate(self):
        with tempfile.TemporaryDirectory() as directory:
            for extra in [
                ["--pull", "/x", directory],
                ["--cwd", "relative"],
                ["--pull", "/x", directory + "/new", "--pull", "/y", directory + "/new"],
            ]:
                api = Transport()
                with self.assertRaises(ValueError):
                    self.invoke(api, *extra)
                self.assertFalse(api.events)
            link = Path(directory) / "link"
            link.symlink_to(Path(directory) / "absent")
            with self.assertRaises(ValueError):
                self.invoke(Transport(), "--pull", "/x", str(link))
        args = raft.parser().parse_args(["run"])
        with self.assertRaises(ValueError):
            raft_run.run(args, Transport())
        for value in ["0", "2592000", "1.5"]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                raft.parser().parse_args(["run", "--timeout", value, "--", "true"])

    def test_creation_plan_sets_ownership_and_expiry_before_start(self):
        args = raft.parser().parse_args(["run", "--location", "lab", "--", "true"])
        args.ttl, args.disposable = 720, True
        calls = []
        config = {"ssh": "fixture", "image": "a" * 64}
        with (
            patch.object(raft, "configured_defaults", return_value={}),
            patch.object(raft, "configuration", return_value={"lab": config}),
            patch.object(raft, "settings", return_value=config),
            patch.object(raft, "require_note"),
            patch.object(raft, "fits_host", return_value=""),
            patch.object(raft, "transaction", side_effect=lambda *args: calls.append(args)),
        ):
            allocated = []
            handle = raft.new(
                args, run_id="owner-token", on_allocate=lambda *args: allocated.append(args)
            )
        self.assertEqual(handle, ":".join(allocated[0]))
        script = calls[0][1]
        self.assertLess(
            script.index('user.raft.expires="$(( '), script.index("incus --project raft start")
        )
        self.assertEqual(script.count("user.raft.expires="), 1)
        self.assertIn("user.raft.run=owner-token", calls[0])
        self.assertIn("user.raft.disposable=true", calls[0])


class FileTests(unittest.TestCase):
    def test_git_filter_and_pinned_directory_packing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".env\nignored/\n")
            (root / ".env").write_text("not-for-upload")
            (root / "tracked").write_text("tracked")
            (root / "ignored").mkdir()
            (root / "ignored/kept").write_text("tracked even though ignored")
            (root / "ignored/secret").write_text("not-for-upload")
            strange = "line\nbreak-" + os.fsdecode(b"\xff")
            (root / strange).write_text("untracked, not ignored")
            subprocess.run(["git", "-C", str(root), "add", ".gitignore", "tracked"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "-f", "ignored/kept"], check=True)
            selected = raft_run.git_files(root)
            self.assertIn(strange, selected)
            self.assertIn("ignored/kept", selected)
            self.assertNotIn(".env", selected)
            self.assertNotIn("ignored/secret", selected)
            stream = io.BytesIO()
            raft_files.pack_directory(root, stream, include=selected)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                names = {name.removeprefix("./") for name in archive.getnames()}
                self.assertIn(strange, names)
                self.assertIn("ignored/kept", names)
                self.assertNotIn("ignored/secret", names)
                self.assertNotIn(".env", names)
                self.assertFalse(any(".git" in Path(name).parts for name in names))
            for bad in [["../outside"], ["/absolute"], ["nested/.git/secret"]]:
                with self.assertRaises(ValueError):
                    raft_files.pack_directory(root, io.BytesIO(), include=bad)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "160000," + "1" * 40 + ",module",
                ],
                check=True,
            )
            with self.assertRaisesRegex(ValueError, "submodules"):
                raft_run.git_files(root)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                raft_run.git_files(Path(directory))

    def test_cleanup_program_checks_ownership_and_lookup_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "incus"
            shim.write_text(
                "#!/usr/bin/env python3\n"
                + """import json, os, pathlib, sys
path = pathlib.Path(os.environ['RUN_STATE'])
state = json.loads(path.read_text())
if state.get('fail'):
    sys.exit(7)
if sys.argv[3] == 'list':
    print(json.dumps(state['boxes']))
elif sys.argv[3] == 'delete':
    state['boxes'] = []; state['deleted'] = True
    path.write_text(json.dumps(state))
else:
    sys.exit(8)
"""
            )
            shim.chmod(0o755)
            state = root / "state.json"
            environment = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "RUN_STATE": str(state),
            }
            for boxes, fail, expected, deleted in [
                ([], False, 0, False),
                (
                    [
                        {
                            "name": "rf-fixture",
                            "config": {"user.raft": "true", "user.raft.run": "token"},
                        }
                    ],
                    False,
                    0,
                    True,
                ),
                (
                    [
                        {
                            "name": "rf-fixture",
                            "config": {"user.raft": "true", "user.raft.run": "other"},
                        }
                    ],
                    False,
                    1,
                    False,
                ),
                ([], True, 1, False),
            ]:
                state.write_text(json.dumps({"boxes": boxes, "fail": fail}))
                result = subprocess.run(
                    [sys.executable, "-c", raft_run.CLEANUP, "rf-fixture", "token"],
                    env=environment,
                    capture_output=True,
                )
                self.assertEqual(bool(result.returncode), bool(expected), result.stderr)
                self.assertEqual(json.loads(state.read_text()).get("deleted", False), deleted)


def native(location):
    """Real CLI/SSH/Incus transfers, templates and cancellation on a selected fixture host."""
    owner = "verify-run-" + uuid.uuid4().hex
    before = {box["name"] for box in raft.inventory(location)}
    owned = set()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / ".config/raft"
        config.mkdir(parents=True)
        (config / "incus.json").write_text(json.dumps({location: raft.settings(location)}))
        environment = {**os.environ, "HOME": directory, "RAFT_NO_UPDATE_CHECK": "1"}
        command = [sys.executable, str(ROOT / "raft.py")]
        options = [
            "--location",
            location,
            "--purpose",
            "Disposable CI verification",
            "--owner",
            owner,
            "--next",
            "Delete after verification",
        ]

        def remember(stderr):
            for handle in re.findall(r"^raft run: allocated (.+)$", stderr, re.MULTILINE):
                if raft.parse_handle(handle)[0] == location:
                    owned.add(handle)

        def cli(*args):
            result = subprocess.run(
                [*command, *args], env=environment, capture_output=True, text=True, timeout=150
            )
            remember(result.stderr)
            return result

        source = root / "source"
        source.mkdir()
        (source / "input.txt").write_text("source arrived")
        try:
            for status in [0, 23]:
                output = root / f"output-{status}"
                result = cli(
                    "run",
                    *options,
                    "--timeout",
                    "60",
                    "--push-all",
                    str(source),
                    "--pull",
                    "/workspace/project/out",
                    str(output),
                    "--",
                    "bash",
                    "-lc",
                    f"mkdir out; cp input.txt out/result.txt; printf payload-output; exit {status}",
                )
                assert result.returncode == status, result.stderr
                assert result.stdout == "payload-output", result.stdout
                assert (output / "result.txt").read_text() == "source arrived"
                assert "cleanup confirmed" in result.stderr, result.stderr
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / ".gitignore").write_text(".env\n")
            (source / ".env").write_text("fixture-not-a-real-secret")
            result = cli(
                "run",
                *options,
                "--timeout",
                "60",
                "--push",
                str(source),
                "--",
                "bash",
                "-lc",
                "set -e; test ! -e .git; test ! -e .env; cat input.txt",
            )
            assert result.returncode == 0 and result.stdout == "source arrived", result.stderr
            result = cli(
                "run", *options, "--timeout", "10", "--", "bash", "-lc", "echo started; sleep 60"
            )
            assert result.returncode == 124 and "started" in result.stdout, (
                result.returncode,
                result.stderr,
            )
            assert "cleanup confirmed" in result.stderr, result.stderr
            for cancellation in [signal.SIGINT, signal.SIGTERM]:
                with tempfile.TemporaryFile(mode="w+") as errors:
                    with subprocess.Popen(
                        [
                            *command,
                            "run",
                            *options,
                            "--timeout",
                            "60",
                            "--",
                            "bash",
                            "-lc",
                            "echo started; sleep 60",
                        ],
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        text=True,
                    ) as process:
                        try:
                            assert select.select([process.stdout], [], [], 30)[0], (
                                "No payload-ready marker"
                            )
                            assert process.stdout.readline().strip() == "started"
                            process.send_signal(cancellation)
                            process.communicate(timeout=70)
                            assert process.returncode == 128 + cancellation, process.returncode
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait()
                            errors.seek(0)
                            diagnostic = errors.read()
                            remember(diagnostic)
                    assert "cleanup confirmed" in diagnostic, diagnostic
            created = cli(
                "new", *options, "--persistent", "--ttl", "600", "--cpu", "1", "--memory", "2GiB"
            )
            assert created.returncode == 0, created.stderr
            base = created.stdout.strip()
            owned.add(base)
            prepared = cli(
                "exec",
                base,
                "--",
                "bash",
                "-lc",
                "mkdir -p /opt/ci-cache; echo warm >/opt/ci-cache/proof",
            )
            assert prepared.returncode == 0, prepared.stderr
            assert cli("stop", base).returncode == 0
            assert cli("snapshot", base, "ci-base").returncode == 0
            for _ in range(2):
                result = cli(
                    "run",
                    *options,
                    "--from",
                    base + "/ci-base",
                    "--timeout",
                    "60",
                    "--",
                    "bash",
                    "-lc",
                    'set -e; test "$(cat /opt/ci-cache/proof)" = warm; '
                    "test ! -e /opt/ci-cache/job; touch /opt/ci-cache/job; cat /sys/fs/cgroup/memory.max",
                )
                assert result.returncode == 0 and result.stdout.strip() == str(2 * 1024**3), (
                    result.stderr
                )
            assert cli("info", base).returncode == 0, "Template source must remain"
        finally:
            # Only recorded handles whose unique note still belongs to this invocation.
            for handle in owned:
                name = raft.parse_handle(handle)[1]
                found = next((box for box in raft.inventory(location) if box["name"] == name), None)
                if (
                    found
                    and json.loads(found["config"].get("user.raft.note") or "{}").get("owner")
                    == owner
                ):
                    removed = cli("destroy", handle)
                    assert removed.returncode == 0, removed.stderr
    after = {box["name"] for box in raft.inventory(location)}
    assert before == after, f"CI run leaked boxes: {after - before}"
    print(f"{location}: native transfers, status, signals, timeout and template isolation passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    if not result.wasSuccessful():
        sys.exit(1)
    if args.location:
        native(args.location)
