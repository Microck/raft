"""Deploy isolated Incus system-container infrastructure on configured hosts.

Run once after reviewing the host setup. Incus VMs need KVM; this deployment
selects system containers explicitly and never silently substitutes a VM.
"""

import argparse
import json
import shlex
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from raft import configuration  # noqa: E402

HOSTS = {name: entry["ssh"] for name, entry in configuration().items()}


def remote(host, command):
    return subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", host, command],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def copy(host, source, destination):
    subprocess.run(["scp", "-q", str(source), f"{host}:{destination}"], check=True)


def deploy(name):
    host = HOSTS[name]
    if remote(host, "uname -m").strip() != "aarch64":
        raise RuntimeError("This tested deployment targets ARM64 hosts")
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
        if pool is not None or network is not None:
            raise RuntimeError(
                "Raft resource names already exist without an owned project; review them explicitly"
            )
        available = int(
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
        if available < 80 * 1024**3:
            raise RuntimeError("Fresh deployment needs at least 80 GiB free disk")
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
    wrapper = "#!/bin/sh\nexec flock /run/lock/raft-incus.lock /usr/local/lib/raft/expire-worker\n"
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
    remote(host, "sudo -n systemctl daemon-reload")
    remote(host, "sudo -n systemctl enable --now raft-network.service raft-expire.timer")
    print(f"{name}: Incus, private bridge and host expiry timer ready", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", choices=HOSTS)
    args = parser.parse_args()
    for name in [args.location] if args.location else HOSTS:
        deploy(name)


if __name__ == "__main__":
    main()
