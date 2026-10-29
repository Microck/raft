"""Verify JSONL bytes, errors, completion discovery and native command status."""

import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import raft  # noqa: E402
import raft_output  # noqa: E402


def events(command, env):
    response = subprocess.run(command, env=env, capture_output=True)
    records = [json.loads(line) for line in response.stdout.splitlines()]
    assert not response.stderr, response.stderr
    assert records[-1] == {"type": "exit", "code": response.returncode}, records
    return response, records


def stream(records, kind):
    return b"".join(base64.b64decode(r["data"]) for r in records if r["type"] == kind)


def verify():
    with tempfile.TemporaryDirectory(prefix="raft-output-") as directory:
        env = {**os.environ, "HOME": directory, "RAFT_NO_UPDATE_CHECK": "1"}
        command = [sys.executable, str(ROOT / "raft.py"), "--json"]
        response, records = events(command + ["--help"], env)
        assert response.returncode == 0 and b"usage:" in stream(records, "stdout")
        for arguments in [["invalid"], ["new", "--ttl", "no"], ["new", "--unknown"]]:
            response, records = events(command + arguments, env)
            assert response.returncode == 2 and records[0]["code"] == "usage", records
        response, records = events(command + ["locations"], env)
        assert response.returncode == 1 and records[0]["code"] == "operation", records
        inventory = Path(directory) / ".config/raft"
        inventory.mkdir(parents=True)
        (inventory / "incus.json").write_text('{"fixture":{"ssh":"offline.invalid","image":""}}')
        response, records = events(command + ["locations"], env)
        assert records[0] == {
            "type": "data",
            "value": {"location": "fixture", "default": True, "note": ""},
        }
        # Real subprocess descriptors, including binary bytes and more than a pipe
        # buffer on both channels. Reconstruction must be exact and cannot hang.
        program = """
import os, subprocess, sys
import raft_output
out = raft_output.JsonOutput().start()
subprocess.run([sys.executable, '-c', 'import os; os.write(1, bytes(range(256))*512); os.write(2,b"error"*32768)'], check=True)
out.finish(7)
sys.exit(7)
"""
        response, records = events(
            [sys.executable, "-c", program], {**env, "PYTHONPATH": str(ROOT)}
        )
        assert response.returncode == 7
        assert stream(records, "stdout") == bytes(range(256)) * 512
        assert stream(records, "stderr") == b"error" * 32768
        parser = raft.parser()
        parser.add_argument("--future-flag", action="store_true")
        assert raft_output.candidates(parser, ["--future"]) == ["--future-flag"]
        assert "--location" in raft_output.candidates(parser, ["new", "--lo"])
        assert "set" in raft_output.candidates(parser, ["config", ""])
        assert raft_output.candidates(parser, ["exec", "box", "--", ""]) == []
        assert raft_output.candidates(parser, ["completions", "z"]) == ["zsh"]
        for shell in ("bash", "zsh", "fish"):
            script = subprocess.run(
                [sys.executable, str(ROOT / "raft.py"), "completions", shell],
                env=env,
                capture_output=True,
                check=True,
            )
            assert b"_complete" in script.stdout and not script.stderr
        setup = raft_output.completions("bash")
        completed = subprocess.run(
            [
                "bash",
                "-c",
                setup
                + "\nraft() { "
                + sys.executable
                + " "
                + str(ROOT / "raft.py")
                + ' "$@"; }\nCOMP_WORDS=(raft config ""); COMP_CWORD=2; _raft_complete; printf "%s\\n" "${COMPREPLY[@]}"',
            ],
            env=env,
            capture_output=True,
            check=True,
        )
        assert "set" in completed.stdout.decode().splitlines(), completed
    print("JSONL bytes, subprocess streams, structured errors, reports and Bash completion passed")


def native(location):
    spec = importlib.util.spec_from_file_location("live", ROOT / "deploy/verify-live.py")
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    box = live.raft(
        "new",
        "--location",
        location,
        "--ttl",
        "600",
        "--disposable",
        "--purpose",
        "JSON output verification",
        "--owner",
        "verify-output",
    ).stdout.strip()
    try:
        live.wait_running(box)
        response, records = events(
            [
                sys.executable,
                str(ROOT / "raft.py"),
                "--json",
                "exec",
                box,
                "--",
                "sh",
                "-c",
                "printf hello; printf error >&2; exit 7",
            ],
            live.CONTROLLER_ENV,
        )
        assert response.returncode == 7
        assert stream(records, "stdout") == b"hello" and stream(records, "stderr") == b"error", (
            records
        )
        response, records = events(
            [sys.executable, str(ROOT / "raft.py"), "--json", "info", box], live.CONTROLLER_ENV
        )
        assert records[0]["value"]["name"] == box.partition(":")[2], records
        print(f"{location}: JSON native exec streams, exit 7 and info passed")
    finally:
        live.raft("destroy", box)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--location")
    args = cli.parse_args()
    verify()
    if args.location:
        native(args.location)
