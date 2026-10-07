"""Stream directory archives and publish new trees without replacing existing paths.

The same code runs on the Linux controller and in the guest. Unpacking always
uses Python's data filter, and publication uses renameat2(RENAME_NOREPLACE).
"""

import argparse
import ctypes
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace


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

    with tarfile.open(fileobj=stream, mode="w|") as archive:

        def add(parent, entry, name):
            # O_PATH pins the entry without opening a device or following a link.
            # All metadata, reads and descent then use that held object.
            descriptor = os.open(entry, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
            try:
                metadata = os.fstat(descriptor)
                if stat.S_ISLNK(metadata.st_mode):
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE
                    member.linkname = os.readlink("", dir_fd=descriptor)
                    safe_member(member, source)
                    archive.addfile(member)
                elif stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
                    opened = SimpleNamespace(name=name, fileno=lambda: descriptor)
                    member = archive.gettarinfo(arcname=name, fileobj=opened)
                    safe_member(member, source)
                    if member.isfile():
                        # Reopening the pinned descriptor cannot follow a replacement
                        # pathname. Linux /proc is part of the guest/controller contract.
                        with open(f"/proc/self/fd/{descriptor}", "rb") as content:
                            archive.addfile(member, content)
                    else:
                        archive.addfile(member)
                    if member.isdir():
                        directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptor)
                        try:
                            for child in sorted(os.listdir(directory)):
                                add(directory, child, name + "/" + child)
                        finally:
                            os.close(directory)
                else:
                    raise ValueError("Directory transfers reject special files: " + name)
            finally:
                os.close(descriptor)

        add(None, source, ".")


class TransferReader:
    """Count physical bytes, including tar's read-ahead and trailing padding."""

    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = self.stream.read(size)
        self.bytes_read += len(chunk)
        return chunk


def unpack_directory(stream, destination, expected_size):
    # Resolve the parent, never the final component: existing destination symlinks
    # must be rejected, not followed to a different publication path.
    destination = Path(destination).absolute()
    destination = destination.parent.resolve(strict=True) / destination.name
    if os.path.lexists(destination):
        raise FileExistsError("Recursive destination already exists: " + str(destination))
    staged = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".raft-tree-"))
    try:
        reader = TransferReader(stream)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            archive.extractall(staged, filter=safe_member)
        # Tar accepts EOF between entries without a proper end marker. Drain the
        # stream and check the sender's size before making any tree visible.
        while reader.read(1024 * 1024):
            pass
        if reader.bytes_read != expected_size:
            raise ValueError("Directory archive size mismatch; transfer did not complete")
        publish(staged, destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("operation", choices=["pack", "unpack"])
    cli.add_argument("path")
    cli.add_argument("--size", type=int)
    args = cli.parse_args()
    if args.operation == "pack":
        pack_directory(args.path, sys.stdout.buffer)
    else:
        if args.size is None or args.size < 0:
            cli.error("Unpacking requires --size with the sender's archive byte count")
        unpack_directory(sys.stdin.buffer, args.path, args.size)


if __name__ == "__main__":
    main()
