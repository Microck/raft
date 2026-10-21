"""Deploy isolated Incus system-container infrastructure on configured hosts.

Run once after reviewing the host setup. Incus VMs need KVM; this deployment
selects system containers explicitly and never silently substitutes a VM.
"""

import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import BOX_DISK_POOL, DEFAULT_DISK_POOL, configuration, gib, gib_size  # noqa: E402

RELEASES = "https://github.com/Microck/raft/releases/download/"
# Matches the user agent install-incus.sh sends for its own downloads.
USER_AGENT = "OpenAI File Downloader, XaiImageApiFetch/1.0"
INCUS_ARCHITECTURES = {"arm64": "aarch64", "amd64": "x86_64"}

# Shared tail of both image scripts. A unified image's Incus fingerprint is the
# SHA-256 of its archive, so checking the digest also predicts the fingerprint and
# lets a rerun reuse an earlier import instead of failing on a duplicate.
IMPORT_IMAGE = """fingerprint=$(sha256sum "$archive" | cut -d ' ' -f 1)
test "$fingerprint" = "$expected" || { echo 'Image SHA-256 does not match the expected checksum' >&2; exit 1; }
if ! sudo -n incus --project raft image info "$fingerprint" >/dev/null 2>&1; then
  sudo -n incus --project raft image import "$archive" >&2
fi
echo "$fingerprint"
"""

RELEASE_IMAGE = (
    """set -eu
work=$(mktemp -d /tmp/raft-image.XXXXXXXX)
trap 'rm -rf "$work"' EXIT
archive="$work/raft-dev-$2.tar.gz"
curl -fsSL -A "$3" -o "$work/SHA256SUMS" "$1/SHA256SUMS"
curl -fsSL -A "$3" -o "$archive" "$1/raft-dev-$2.tar.gz"
expected=$(awk -v name="raft-dev-$2.tar.gz" '$2 == name {print $1}' "$work/SHA256SUMS")
test -n "$expected" || { echo "Release SHA256SUMS lists no raft-dev-$2.tar.gz" >&2; exit 1; }
"""
    + IMPORT_IMAGE
)

ARCHIVE_IMAGE = (
    """set -eu
archive=$(mktemp /tmp/raft-image.XXXXXXXX)
trap 'rm -f "$archive"' EXIT
cat > "$archive"
expected="$1"
"""
    + IMPORT_IMAGE
)


def hosts():
    # Read at call time: onboarding imports this module before its location exists.
    return {name: entry["ssh"] for name, entry in configuration().items()}


def remote(host, command):
    response = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", host, command],
        capture_output=True,
        text=True,
    )
    if response.returncode:
        raise RuntimeError(response.stderr.strip() or "Remote command failed: " + command)
    return response.stdout


def copy(host, source, destination):
    subprocess.run(["scp", "-q", str(source), f"{host}:{destination}"], check=True)


def host_architecture(host):
    architecture = remote(host, "dpkg --print-architecture").strip()
    if architecture not in {"arm64", "amd64"}:
        raise RuntimeError("Raft hosts must use native ARM64 or AMD64 Debian/Ubuntu packages")
    return architecture


def script(host, body, *arguments, stdin=None):
    """Run a shell script on the host with positional arguments, returning its stdout."""
    response = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            host,
            shlex.join(["sh", "-c", body, "raft", *arguments]),
        ],
        stdin=stdin,
        capture_output=True,
        text=True,
    )
    if response.returncode:
        raise RuntimeError(response.stderr.strip() or "Remote script failed")
    return response.stdout.strip()


def checked_image(host, fingerprint, architecture):
    """Confirm the imported image exists and matches the host architecture."""
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise RuntimeError("Image import returned an invalid fingerprint")
    images = json.loads(
        remote(host, f"sudo -n incus --project raft image list {fingerprint} --format json")
    )
    image = next((item for item in images if item["fingerprint"] == fingerprint), None)
    if image is None or image["architecture"] != INCUS_ARCHITECTURES[architecture]:
        raise RuntimeError(f"Imported image is missing or not a native {architecture} image")
    return fingerprint


def import_release(host, tag, architecture):
    """Download a release image on the host, verify it against SHA256SUMS and import it."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Release tags look like v0.3.0")
    fingerprint = script(host, RELEASE_IMAGE, RELEASES + tag, architecture, USER_AGENT)
    return checked_image(host, fingerprint, architecture)


def import_archive(host, archive, architecture):
    """Upload a local image archive over SSH stdin, verify its digest and import it."""
    with Path(archive).open("rb") as source:
        expected = hashlib.file_digest(source, "sha256").hexdigest()
        source.seek(0)
        fingerprint = script(host, ARCHIVE_IMAGE, expected, stdin=source)
    return checked_image(host, fingerprint, architecture)


def free_disk(host):
    return int(
        remote(
            host,
            shlex.join(
                [
                    "python3",
                    "-c",
                    "import shutil; print(shutil.disk_usage('/var/lib/incus').free)",
                ]
            ),
        )
    )


def box_disk_pool(host, pools, size):
    """Create or verify the LVM pool that holds own box disks.

    Thick logical volumes (lvm.use_thinpool=false) give each `new --disk` box an
    ext4 filesystem of exactly its size, so nothing inside the box, Docker
    included, can write past it, and space is taken when the box is created
    rather than when it writes. A thin pool would allow overcommit, and running
    out of thin-pool space fails every box in the pool at once.
    """
    pool = next((item for item in pools if item["name"] == BOX_DISK_POOL), None)
    if pool is None:
        size = size or DEFAULT_DISK_POOL
        # Same 20 GiB margin as the fresh-host check for the 60 GiB shared pool.
        if free_disk(host) < (gib(size) + 20) * 1024**3:
            raise RuntimeError(
                f"Creating the {size} {BOX_DISK_POOL} pool needs {gib(size) + 20} GiB free disk; "
                "pass a smaller --disk-pool or free host disk"
            )
        remote(
            host,
            f"sudo -n incus storage create {BOX_DISK_POOL} lvm size={size} lvm.use_thinpool=false",
        )
        pool = json.loads(remote(host, f"sudo -n incus query /1.0/storage-pools/{BOX_DISK_POOL}"))
    if pool["driver"] != "lvm" or pool["config"].get("lvm.use_thinpool") != "false":
        raise RuntimeError(
            f"{BOX_DISK_POOL} must be a thick LVM pool (lvm.use_thinpool=false); inspect it and "
            "back up its boxes before explicit storage recovery"
        )
    if size is not None and pool["config"].get("size") != size:
        raise RuntimeError(
            f"{BOX_DISK_POOL} has size={pool['config'].get('size')}, not {size}. "
            "Deployment does not resize existing storage; omit --disk-pool to keep it"
        )
    # Incus creates the pool's loop file sparse. Allocating its full length makes
    # the host disk hold every own disk in full; fallocate only fills holes and
    # never changes the file's length or data, so repeating it is a no-op. A pool
    # an operator placed on a dedicated block device has no file to allocate.
    remote(
        host,
        shlex.join(
            [
                "sudo",
                "-n",
                "sh",
                "-c",
                'test ! -f "$1" || fallocate -l "$(stat -c %s "$1")" "$1"',
                "raft",
                pool["config"]["source"],
            ]
        ),
    )


def deploy(name, host, disk_pool=None):
    host_architecture(host)
    copy(host, ROOT / "deploy/install-incus.sh", "/tmp/raft-install-incus.sh")
    print(remote(host, "bash /tmp/raft-install-incus.sh </dev/null"), flush=True)
    # Inspect rather than overwriting an unrelated existing project or pool.
    projects = json.loads(remote(host, "sudo -n incus project list --format json"))
    project = next((p for p in projects if p["name"] == "raft"), None)
    pools = json.loads(remote(host, "sudo -n incus storage list --format json"))
    networks = json.loads(remote(host, "sudo -n incus network list --format json"))
    pool = next((item for item in pools if item["name"] == "raft-data"), None)
    network = next((item for item in networks if item["name"] == "rfbr0"), None)
    if project is not None:
        if project.get("description") != "Raft managed workspaces":
            raise RuntimeError("Existing raft project needs ownership verification")
    else:
        if (
            pool is not None
            or network is not None
            or any(item["name"] == BOX_DISK_POOL for item in pools)
        ):
            raise RuntimeError(
                "Raft resource names already exist without an owned project; review them explicitly"
            )
        # 60 GiB shared pool, the own-disk pool and 20 GiB for images and the host.
        needed = 80 + gib(disk_pool or DEFAULT_DISK_POOL)
        if free_disk(host) < needed * 1024**3:
            raise RuntimeError(f"Fresh deployment needs at least {needed} GiB free disk")
        remote(
            host,
            "sudo -n incus project create raft -c features.images=true -c features.profiles=true </dev/null",
        )
        remote(
            host,
            "sudo -n incus project set raft --property description='Raft managed workspaces'",
        )
        remote(host, "sudo -n incus storage create raft-data btrfs size=60GiB")
        remote(
            host,
            "sudo -n incus network create rfbr0 ipv4.address=10.232.0.1/24 ipv4.nat=true ipv6.address=none",
        )
        remote(
            host,
            "sudo -n incus --project raft profile device add default root disk path=/ pool=raft-data",
        )
        remote(
            host,
            "sudo -n incus --project raft profile device add default eth0 nic network=rfbr0 name=eth0",
        )
        remote(
            host,
            "sudo -n incus --project raft profile set default limits.cpu=1 limits.memory=2GiB security.nesting=true security.syscalls.intercept.mknod=true security.syscalls.intercept.setxattr=true",
        )
    pools = json.loads(remote(host, "sudo -n incus storage list --format json"))
    networks = json.loads(remote(host, "sudo -n incus network list --format json"))
    profiles = json.loads(remote(host, "sudo -n incus --project raft profile list --format json"))
    pool = next((item for item in pools if item["name"] == "raft-data"), None)
    network = next((item for item in networks if item["name"] == "rfbr0"), None)
    profile = next((item for item in profiles if item["name"] == "default"), None)
    if pool is None or pool["driver"] != "btrfs" or network is None or profile is None:
        raise RuntimeError(
            "Incomplete Raft infrastructure; inspect the project, pool, bridge and profile before retrying"
        )
    if pool["config"].get("size") != "60GiB":
        raise RuntimeError(
            "Raft pool must have configured size=60GiB; inspect raft-data and back up boxes "
            "before explicit storage recovery. Deployment does not resize existing storage."
        )
    box_disk_pool(host, pools, disk_pool)

    devices = profile["devices"]
    if (
        devices.get("root", {}).get("pool") != "raft-data"
        or devices.get("eth0", {}).get("network") != "rfbr0"
        or network["config"].get("ipv4.address") != "10.232.0.1/24"
        or network["config"].get("ipv4.nat") != "true"
        or network["config"].get("ipv6.address") != "none"
        or profile["config"].get("security.nesting") != "true"
        or profile["config"].get("security.syscalls.intercept.mknod") != "true"
        or profile["config"].get("security.syscalls.intercept.setxattr") != "true"
        or profile["config"].get("security.privileged", "false") != "false"
    ):
        raise RuntimeError(
            "Raft profile/network differs from the contract; inspect and recover explicitly"
        )
    remote(host, "sudo -n mkdir -p /usr/local/lib/raft")
    for source, target in [
        ("deploy/raft-network.sh", "network"),
        ("deploy/raft-expire.py", "expire-worker"),
    ]:
        copy(host, ROOT / source, "/tmp/raft-" + target)
        remote(host, f"sudo -n install -m 755 /tmp/raft-{target} /usr/local/lib/raft/{target}")
    wrapper = (
        '#!/bin/sh\nexec flock /run/lock/raft-incus.lock /usr/local/lib/raft/expire-worker "$@"\n'
    )
    local_wrapper = ROOT / ".raft-expire-wrapper"
    local_wrapper.write_text(wrapper)
    try:
        copy(host, local_wrapper, "/tmp/raft-expire-wrapper")
    finally:
        local_wrapper.unlink()
    remote(host, "sudo -n install -m 755 /tmp/raft-expire-wrapper /usr/local/lib/raft/expire")
    for unit in ["raft-expire.service", "raft-expire.timer", "raft-network.service"]:
        copy(host, ROOT / "deploy" / unit, "/tmp/" + unit)
        remote(host, f"sudo -n install -m 644 /tmp/{unit} /etc/systemd/system/{unit}")
    remote(host, "sudo -n mkdir -p /etc/systemd/system/incus.service.d")
    copy(host, ROOT / "deploy/incus-network.conf", "/tmp/raft-incus-network.conf")
    remote(
        host,
        "sudo -n install -m 644 /tmp/raft-incus-network.conf /etc/systemd/system/incus.service.d/raft-network.conf",
    )
    remote(host, "sudo -n systemctl daemon-reload")
    remote(host, "sudo -n systemctl enable raft-network.service raft-expire.timer")
    # Reload active filtering without stopping the Incus daemon that requires it.
    remote(host, "sudo -n systemctl reload-or-restart raft-network.service")
    remote(host, "sudo -n systemctl start raft-expire.timer")
    print(f"{name}: Incus, private bridge and host expiry timer ready", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    configured = hosts()
    parser.add_argument("--location", choices=configured)
    parser.add_argument(
        "--disk-pool",
        type=gib_size,
        metavar="SIZE",
        help=f"Size of a new {BOX_DISK_POOL} pool for own box disks (default: "
        f"{DEFAULT_DISK_POOL}); an existing pool must already have this size",
    )
    args = parser.parse_args()
    for name in [args.location] if args.location else configured:
        deploy(name, configured[name], args.disk_pool)


if __name__ == "__main__":
    main()
