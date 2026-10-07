"""Inspect built release artifacts for local metadata and private path leakage.

This complements a secret scanner and manual history review. It is not a proof
that every possible personal identifier has been removed.
"""

from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {".git", ".jj", ".venv", ".ruff_cache", "__pycache__"}
PRIVATE_TEXT = re.compile(r"/(?:home|Users)/[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def inspect(name, content):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or FORBIDDEN.intersection(path.parts):
        raise ValueError("Private metadata or unsafe archive member: " + name)
    if path.suffix == ".pyc":
        raise ValueError("Compiled cache in release: " + name)
    # Source archives also contain binary branding assets; scan their readable bytes.
    match = PRIVATE_TEXT.search(content.decode("utf-8", errors="replace"))
    if match:
        raise ValueError("Private path or email in release member: " + name)


def main():
    sources = list((ROOT / "dist").glob("*.tar.gz"))
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(sources) != 1 or len(wheels) != 1:
        raise ValueError("Build exactly one source archive and wheel before checking")
    with tarfile.open(sources[0]) as archive:
        members = archive.getmembers()
        for member in members:
            if not member.isfile():
                raise ValueError("Unexpected non-file member: " + member.name)
            inspect(member.name, archive.extractfile(member).read())
        names = {PurePosixPath(member.name).parts[1] for member in members}
        if not {"raft.py", "deploy", "docs", "images", "skills", "LICENSE"} <= names:
            raise ValueError("Source archive missing installation resources")
    with zipfile.ZipFile(wheels[0]) as archive:
        for name in archive.namelist():
            inspect(name, archive.read(name))
        if not {"raft.py", "raft_files.py"} <= set(archive.namelist()):
            raise ValueError("Wheel missing CLI module")
    # A new environment avoids both editable checkouts and uv's cached same-version
    # tool environments. Run the installed entry point outside the source tree.
    with tempfile.TemporaryDirectory(prefix="raft-wheel-") as work:
        environment = Path(work) / "venv"
        python = environment / "bin/python"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        subprocess.run(
            ["uv", "pip", "install", "--no-cache", "--python", str(python), str(wheels[0])],
            check=True,
        )
        for command in [["--help"], ["upload", "--help"], ["new", "--help"], ["prune", "--help"]]:
            output = subprocess.check_output(
                [str(environment / "bin/raft"), *command], cwd=work, text=True
            )
            if command[0] == "upload" and "--recursive" not in output:
                raise ValueError("Installed wheel lacks recursive transfers")
            if command[0] == "new" and "--disposable" not in output:
                raise ValueError("Installed wheel lacks disposable lifecycle")
        subprocess.run([str(python), "-I", "-c", "import raft, raft_files"], cwd=work, check=True)
    print("Release archives and isolated wheel installation passed")


if __name__ == "__main__":
    main()
