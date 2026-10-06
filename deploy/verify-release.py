"""Inspect built release artifacts for local metadata and private path leakage.

This complements a secret scanner and manual history review. It is not a proof
that every possible personal identifier has been removed.
"""

from pathlib import Path, PurePosixPath
import re
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
    match = PRIVATE_TEXT.search(content.decode("utf-8"))
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
        if "raft.py" not in archive.namelist():
            raise ValueError("Wheel missing CLI module")
    print("Release archives contain source resources and no VCS/cache, home-path or email matches")


if __name__ == "__main__":
    main()
