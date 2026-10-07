"""Inspect built release artifacts for local metadata and private path leakage.

This complements a secret scanner and manual history review. It is not a proof
that every possible personal identifier has been removed.
"""

import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import __version__  # noqa: E402

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


def verify_cli(executable, directory):
    version = subprocess.check_output([str(executable), "--version"], cwd=directory, text=True)
    if version.strip() != __version__:
        raise ValueError("Installed CLI version differs from release source")
    for command in [["--help"], ["upload", "--help"], ["new", "--help"], ["prune", "--help"]]:
        output = subprocess.check_output([str(executable), *command], cwd=directory, text=True)
        if command[0] == "upload" and "--recursive" not in output:
            raise ValueError("Installed CLI lacks recursive transfers")
        if command[0] == "new" and "--disposable" not in output:
            raise ValueError("Installed CLI lacks disposable lifecycle")
    invalid = subprocess.run([str(executable), "new", "--from"], cwd=directory, capture_output=True)
    if invalid.returncode != 2:
        raise ValueError("Installed CLI did not preserve argument-error exit status")


def verify_npm():
    metadata = json.loads((ROOT / "package.json").read_text())
    if metadata["version"] != __version__:
        raise ValueError("npm version differs from Python release version")
    packed = json.loads(
        subprocess.check_output(
            ["npm", "pack", "--json", "--ignore-scripts", "--pack-destination", str(ROOT / "dist")],
            cwd=ROOT,
            text=True,
        )
    )[0]
    package = ROOT / "dist" / packed["filename"]
    with tarfile.open(package) as archive:
        names = set()
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError("Unexpected npm archive member: " + member.name)
            content = archive.extractfile(member).read()
            inspect(member.name, content)
            names.add(member.name)
            if member.name in {"package/raft.py", "package/raft_files.py"}:
                if content != (ROOT / PurePosixPath(member.name).name).read_bytes():
                    raise ValueError("npm runtime differs from canonical Python source")
        required = {
            "package/raft.py",
            "package/raft_files.py",
            "package/LICENSE",
            "package/deploy/deploy.py",
            "package/images/incus/build.sh",
            "package/docs/incus.example.json",
            "package/skills/raft-cli/SKILL.md",
        }
        if not required <= names:
            raise ValueError("npm package lacks required runtime files")
    with tempfile.TemporaryDirectory(prefix="raft-npm-") as work:
        subprocess.run(
            ["npm", "install", "--global", "--prefix", work, "--ignore-scripts", str(package)],
            cwd=work,
            check=True,
            capture_output=True,
        )
        verify_cli(Path(work) / "bin/raft", work)
    print("npm archive and isolated executable installation passed")


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
        verify_cli(environment / "bin/raft", work)
        subprocess.run([str(python), "-I", "-c", "import raft, raft_files"], cwd=work, check=True)
    verify_npm()
    print("Release archives and isolated wheel installation passed")


if __name__ == "__main__":
    main()
