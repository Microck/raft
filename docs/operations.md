# Installation and operations

## Requirements

Use a dedicated ARM64 Ubuntu 22.04 Linux host with systemd, SSH access,
passwordless sudo, at least 2 CPUs, 8 GiB RAM and sufficient free disk. Reserve
at least 80 GiB for the 60 GiB workspace pool, image cache and build artifacts.
Image builds use 2 CPUs and 4 GiB RAM. The deployment is single-operator software.
Passwordless host sudo grants administrative access; use trusted operators only.

On a fresh host, install the prerequisites before deploying:

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 openssh-server iptables btrfs-progs
```

The Linux controller needs Python 3.11+, SSH, SCP and uv. Verify host SSH keys before
using BatchMode commands. No public Incus listener or cloud account is needed.

```sh
uv sync --locked
uv tool install --editable .
mkdir -p ~/.config/raft
cp docs/incus.example.json ~/.config/raft/incus.json
chmod 600 ~/.config/raft/incus.json
```

Edit the example with your SSH target. Location names use letters, digits, underscores or hyphens. Before an
image exists, use an empty image value; deployment does not use that value.
The first configured location is the default for `raft new`.

## Provision a host and build its image

Review the installer before running it. It installs signed Incus 6.0 LTS
packages when Incus is absent, creates project `raft`, a 60 GiB Btrfs pool
`raft-data` and bridge `rfbr0` at `10.232.0.1/24`. Check that these names and this
subnet do not conflict with existing infrastructure. The deployer rejects name
conflicts and incomplete owned resources rather than replacing them. Network filters are scoped
to the Raft bridge and allow public outbound traffic while blocking metadata,
private destinations, host services and peer containers.

```sh
python3 deploy/deploy.py --location lab
python3 deploy/build-image.py --location lab
```

The builder downloads a public `images:debian/13` container image, installs the
development tools and publishes local alias `raft-dev`. It prints the base and
resulting image fingerprints. Put the full resulting fingerprint into the
location's `image` field. Do not use an alias in the controller configuration.
Build each host's image separately. No prebuilt Raft image is published.

A successful build removes its builder. Failed builds retain `raft-builder` for
diagnosis. An existing `raft-dev` alias is rejected; inspect it and replace it
explicitly when rebuilding. Package inventories are in `/opt/raft/` inside guests.
The build has no operator credentials. Third-party tools retain their licenses.

```sh
raft doctor
raft list
box=$(raft new --location lab --ttl 600)
raft exec "$box" -- docker info
raft destroy "$box"
```

## Ownership and persistence

Raft manages labelled `rf-` instances in its own project. Four saved boxes are
admitted per host. Stopped boxes count and retain files, installed packages and
secrets. Expiration stops processes rather than deleting data. `destroy` deletes
the box and snapshots. Forks copy filesystem secrets. Snapshot operations require
a stopped source; restore changes files and retains instance configuration.

Root units `raft-network.service` and `raft-expire.timer` restore network rules
and check expiry every 15 seconds. Stop allows 30 seconds for shutdown. Lifecycle
operations share `/run/lock/raft-incus.lock`; long backups delay expiry checks.
Restart and reverify network filtering if other tooling flushes firewall rules.

## Jobs and files

The examples below use a running box handle returned by `raft new`:

```sh
box=$(raft new --location lab --ttl 1800)
```

Run a background command, inspect its output and stop it:

```sh
job=$(raft exec "$box" --detach -- bash -lc 'echo hello; sleep 600')
raft status "$box" "$job"
raft logs "$box" "$job"
raft cancel "$box" "$job"
```

Extend the running box's deadline without restarting it and inspect resource use:

```sh
raft extend "$box" --ttl 1800
raft usage "$box"
```

Transfer individual files:

```sh
raft upload "$box" ./source.tar /workspace/source.tar
raft download "$box" /workspace/source.tar ./download.tar
```

## Snapshots and forks

Stop the source before saving a snapshot or creating a same-host copy:

```sh
raft stop "$box"
raft snapshot "$box" prepared
copy=$(raft fork "$box" --ttl 600)
```

The copy runs independently. Destroy it when finished with `raft destroy "$copy"`.
Restore the stopped original with `raft restore "$box" prepared`. Restore replaces
its files with snapshot contents and retains its current instance configuration.

## Private services

`raft forward` connects through the guest's managed network address. Bind the
service to that address or `0.0.0.0`; a service listening only on guest
`127.0.0.1` is not reachable through this command. The desktop already listens
on the guest network interface. The controller-side listener remains loopback
only. This does not publish a service to the internet.

Start a private desktop tunnel for a running box. If the snapshot example left
it stopped, first run `raft resume "$box" --ttl 1800`:

```sh
raft desktop "$box" --local 6080
```

Open `http://127.0.0.1:6080/vnc.html` in your browser. Ctrl+C closes the tunnel.
For another service, use `raft forward "$box" --remote 8080 --local 8080`.

## Backups and troubleshooting

```sh
raft stop "$box"
raft backup "$box" ./workspace.tar.gz
recovered=$(raft recover ./workspace.tar.gz --location lab)
raft resume "$recovered" --ttl 600
```

Archives contain guest secrets and native Incus configuration. Import only trusted
exports. They include snapshots and must fit on the controller and target host.
Recovery creates a fresh stopped box with a new MAC and no inherited deadline.
The host temporarily stages the upload under `/tmp/raft-recovery.*`. If a
controller crashes, inspect the reported handle and exact owned staging file
before cleanup. Never delete arbitrary host files. Keep backups off the source
host to survive loss of that host.

Backup storage must support owner-only file permissions and hard links. Raft
enforces mode `0600` before writing an export and rejects storage that cannot
keep it private. Network filesystems must provide those operations correctly.

`raft doctor` includes host disk and the actual Btrfs pool usage.

Use `raft doctor`, `raft list`, host `sudo incus --project raft list` and
`sudo journalctl -u incus -u raft-expire.service` to diagnose failures. Journals
may contain guest secrets. An unreachable host does not mean its boxes are gone.

## Tests and skill

Follow [verification](verification.md) for real E2E suites and their side effects.
Install the optional agent skill by linking `skills/raft-cli` into your agent's
skills directory. The skill source is maintained in this repository.
