"""Build a pinned Raft development template from a published Incus base image."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import HOSTS, ROOT, copy, remote  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", choices=HOSTS, required=True)
    args = parser.parse_args()
    host = HOSTS[args.location]
    prefix = "sudo -n incus --project raft"
    aliases = json.loads(remote(host, prefix + " image list --format json"))
    if any(a["name"] == "raft-dev" for image in aliases for a in image["aliases"]):
        raise RuntimeError(
            "raft-dev exists; review and remove that alias explicitly before rebuilding"
        )
    remote(host, prefix + " image copy images:debian/13 local: --alias raft-base")
    images = json.loads(remote(host, prefix + " image list raft-base --format json"))
    fingerprint = images[0]["fingerprint"]
    print("Published base fingerprint: " + fingerprint, flush=True)
    remote(
        host,
        prefix + " launch " + fingerprint + " raft-builder -c limits.cpu=2 -c limits.memory=4GiB",
    )
    for source, guest, mode in [
        ("images/incus/build.sh", "/tmp/raft-build.sh", "755"),
        ("images/incus/desktop.sh", "/usr/local/bin/raft-desktop", "755"),
        ("images/incus/raft-desktop.service", "/etc/systemd/system/raft-desktop.service", "644"),
    ]:
        staged = "/tmp/" + Path(guest).name
        copy(host, ROOT / source, staged)
        remote(host, prefix + f" file push {staged} raft-builder{guest} --mode {mode}")
    remote(host, prefix + " exec raft-builder -- bash /tmp/raft-build.sh")
    remote(host, prefix + " stop raft-builder --timeout 30")
    print(
        remote(
            host,
            prefix
            + " publish raft-builder --alias raft-dev --compression gzip description='Raft Debian 13 ARM64 development workspace'",
        )
    )
    # Build failures intentionally retain their named builder for diagnostics.
    # A successful immutable publication no longer needs its writable builder.
    remote(host, prefix + " delete raft-builder")


if __name__ == "__main__":
    main()
