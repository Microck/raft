"""Stream directory archives and publish new trees without replacing existing paths.

The same code runs on the Linux controller and in the guest. Unpacking always
uses Python's data filter, and publication uses renameat2(RENAME_NOREPLACE).
"""

import argparse
import ctypes
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile


def publish(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def safe_member(member, destination):
    if PurePosixPath(member.name).is_absolute():
        raise ValueError("Directory transfers reject absolute paths: " + member.name)
    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
        raise ValueError("Directory transfers reject special files: " + member.name)
    return tarfile.data_filter(member, destination)


def pack_directory(source, stream):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Recursive transfer source must be a directory")

    # Validate links while creating the archive too, before an upload reaches a guest.
    def validate(member):
        safe_member(member, source)
        return member

    with tarfile.open(fileobj=stream, mode="w|") as archive:
        archive.add(source, arcname=".", filter=validate)


def unpack_directory(stream, destination):
    # Resolve the parent, never the final component: existing destination symlinks
    # must be rejected, not followed to a different publication path.
    destination = Path(destination).absolute()
    destination = destination.parent.resolve(strict=True) / destination.name
    if os.path.lexists(destination):
        raise FileExistsError("Recursive destination already exists: " + str(destination))
    staged = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".raft-tree-"))
    try:
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            archive.extractall(staged, filter=safe_member)
        publish(staged, destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("operation", choices=["pack", "unpack"])
    cli.add_argument("path")
    args = cli.parse_args()
    if args.operation == "pack":
        pack_directory(args.path, sys.stdout.buffer)
    else:
        unpack_directory(sys.stdin.buffer, args.path)


if __name__ == "__main__":
    main()
