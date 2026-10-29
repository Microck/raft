"""Build a pinned Raft development template from a published Incus base image."""

import argparse
import json
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import ROOT, copy, host_architecture, hosts, remote  # noqa: E402


def build(host, alias):
    """Build and publish a native image under a new alias, returning its fingerprint."""
    architecture = host_architecture(host)
    started = time.monotonic()
    prefix = "sudo -n incus --project raft"
    aliases = json.loads(remote(host, prefix + " image list --format json"))
    if any(a["name"] == alias for image in aliases for a in image["aliases"]):
        raise RuntimeError(alias + " exists; choose a fresh alias or review its removal explicitly")
    images = json.loads(
        remote(host, prefix + f" image list images:debian/13/{architecture} --format json")
    )
    bases = [
        image
        for image in images
        if image["type"] == "container"
        and image["properties"].get("variant") == "default"
        and image["properties"].get("architecture") == architecture
    ]
    if len(bases) != 1:
        raise RuntimeError("Expected exactly one native Debian 13 default container image")
    fingerprint = bases[0]["fingerprint"]
    # Incus --project selects the source project; the destination must be explicit.
    remote(host, prefix + " image copy images:" + fingerprint + " local: --target-project raft")
    print("Published base fingerprint: " + fingerprint, flush=True)
    remote(
        host,
        prefix + " launch " + fingerprint + " raft-builder -c limits.cpu=2 -c limits.memory=4GiB",
    )
    # Stage outside /tmp: its boot-time tmpfs mount can hide an early file push.
    for source, guest, mode in [
        ("images/incus/build.sh", "/root/raft-build.sh", "755"),
        ("images/incus/desktop.sh", "/usr/local/bin/raft-desktop", "755"),
        ("images/incus/raft-desktop.service", "/etc/systemd/system/raft-desktop.service", "644"),
    ]:
        staged = "/tmp/" + Path(guest).name
        copy(host, ROOT / source, staged)
        remote(host, prefix + f" file push {staged} raft-builder{guest} --mode {mode}")
    remote(host, prefix + " exec raft-builder -- bash /root/raft-build.sh")
    remote(host, prefix + " exec raft-builder -- rm /root/raft-build.sh")
    remote(host, prefix + " stop raft-builder --timeout 30")
    print(
        remote(
            host,
            prefix
            + " publish raft-builder --alias "
            + shlex.quote(alias)
            + " --compression gzip description="
            + shlex.quote(f"Raft Debian 13 {architecture} development workspace"),
        )
    )
    # Build failures intentionally retain their named builder for diagnostics.
    # A successful immutable publication no longer needs its writable builder.
    remote(host, prefix + " delete raft-builder")
    published = json.loads(remote(host, prefix + " image list " + alias + " --format json"))
    fingerprint = next(
        image["fingerprint"]
        for image in published
        if any(item["name"] == alias for item in image["aliases"])
    )
    # Unpacking a full image can take a minute. Pay it during setup, before new
    # reports a usable box; init prepares the native cache without booting a guest.
    cache_probe = "raft-image-cache-" + uuid.uuid4().hex[:8]
    print("Preparing optimized image cache with " + cache_probe, flush=True)
    remote(host, prefix + " init " + fingerprint + " " + cache_probe)
    remote(host, prefix + " delete " + cache_probe)
    print(
        f"Native {architecture} image built in {time.monotonic() - started:.1f} seconds", flush=True
    )
    return fingerprint


def main():
    configured = hosts()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", choices=configured, required=True)
    parser.add_argument(
        "--alias", default="raft-dev", help="New image alias; existing aliases are refused"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", args.alias):
        parser.error("alias must use letters, digits, underscores or hyphens")
    build(configured[args.location], args.alias)


if __name__ == "__main__":
    main()
