"""Exercise repeated source sync against a disposable native workspace."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location("live", Path(__file__).with_name("verify-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def verify(location):
    box = live.raft(
        "new",
        "--location",
        location,
        "--ttl",
        "600",
        "--disposable",
        "--purpose",
        "Source sync verification",
        "--owner",
        "verify-sync",
    ).stdout.strip()
    try:
        live.wait_running(box)
        with tempfile.TemporaryDirectory(prefix="raft-sync-") as folder:
            source = Path(folder) / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", source], check=True)
            (source / ".gitignore").write_text("ignored\n")
            (source / "ignored").write_text("private")
            (source / "file").write_text("first")
            (source / "nested").mkdir()
            (source / "nested/file").write_text("nested")
            unusual = [
                "space name",
                "a*b",
                "a?b",
                "a[b]",
                "back\\slash",
                "line\nbreak",
                "back\\*star",
            ]
            for path in unusual:
                (source / path).write_text(path)
            (source / "link").symlink_to("file")
            (source / "unsafe").symlink_to("../outside")
            outside = Path(folder) / "outside"
            outside.mkdir()
            (outside / "file").write_text("must not leak")
            subprocess.run(["git", "-C", source, "add", "."], check=True)

            def sync(*options):
                return live.raft("sync", box, str(source), "/workspace/sync target", *options)

            def files():
                return json.loads(
                    live.execute(
                        box,
                        "python3",
                        "-c",
                        """
import json, pathlib
root = pathlib.Path('/workspace/sync target')
print(json.dumps({str(p.relative_to(root)): p.read_text(errors="replace") for p in root.rglob('*') if p.is_file()}))
""",
                    )
                )

            sync("--dry-run")
            assert (
                live.raft(
                    "exec", box, "--", "test", "-e", "/workspace/sync target", check=False
                ).returncode
                != 0
            )
            sync()
            first = files()
            assert first["file"] == "first" and first["nested/file"] == "nested", first
            assert "ignored" not in first and "unsafe" not in first
            assert all(first[path] == path for path in unusual), first
            assert not any(path.startswith(".git/") for path in first), first
            repeated = sync()
            assert not any(
                line.startswith(("<f", ">f")) for line in repeated.stdout.splitlines()
            ), repeated.stdout
            stamp = (source / "file").stat()
            (source / "file").write_text("other")
            os.utime(source / "file", ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            sync("--dry-run")
            assert files()["file"] == "first"
            sync()
            assert files()["file"] == "other"
            (source / "file").unlink()
            sync()
            assert files()["file"] == "other", "Guest-only files must survive"
            # A directory replaced by an escaping symlink must never cause the
            # tracked descendant to be read from outside the selected source.
            shutil.rmtree(source / "nested")
            (source / "nested").symlink_to(outside)
            sync()
            assert files()["nested/file"] == "nested"
            sync("--all")
            assert files()["ignored"] == "private"
            assert live.raft("sync", box, str(source), "/", check=False).returncode != 0
            print(
                f"{location}: sync filtering, repeat, checksum, dry-run, preservation and links passed"
            )
    finally:
        live.raft("destroy", box)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--location", required=True)
    verify(cli.parse_args().location)
