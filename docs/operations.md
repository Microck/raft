---
title: Installation and operations
---

# Installation and operations

## Requirements

Use a dedicated ARM64 or AMD64 Ubuntu 22.04/24.04 Linux host with systemd, SSH access,
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
private destinations and host services. A native nftables bridge hook blocks
peer frames on `rfbr0`, including IPv6, without changing host-wide bridge
sysctls. The installer ensures the `nftables` host dependency is available.

Incus requires `raft-network.service` to finish successfully before daemon startup,
including socket activation. A failed firewall installation blocks startup.
Deployment reloads the active firewall without restarting Incus and replaces its
owned IPv4 chains transactionally, preserving unrelated host chains. Review the
[systemd dependency rules](https://www.freedesktop.org/software/systemd/man/systemd.unit.html)
when managing these units. Do not bypass the dependency or flush host firewall rules.

Existing owned pools must have `size=60GiB`. Deployment rejects a different size
without resizing storage. Back up boxes, inspect the pool and choose explicit
storage recovery on a dedicated host before retrying. Do not shrink a live pool
as an automatic repair.

```sh
python3 deploy/deploy.py --location lab
python3 deploy/build-image.py --location lab
```

The builder downloads a public `images:debian/13` container image, installs the
development tools and publishes local alias `raft-dev`. It prints the base and
resulting image fingerprints. Put the full resulting fingerprint into the
location's `image` field. Do not use an alias in the controller configuration.
The builder selects the host's native architecture automatically. ARM64 uses
Node's `arm64` archive; AMD64 uses `x64`. Cross-architecture emulation is not
supported. Build each host's image separately. Native image CI builds both
ARM64 and AMD64 templates. Successful runs attach seven-day CI artifacts.
Manual runs with a new release tag also publish permanent
[image releases](https://github.com/Microck/raft/releases) after fresh-host
validation; results are recorded in
[boot performance](./boot-performance.md).

A successful build removes its builder. Failed builds retain `raft-builder` for
diagnosis. An existing `raft-dev` alias is rejected. Use `--alias raft-dev-next` to build
a separate candidate without replacing a pinned image. Package inventories are in `/opt/raft/` inside guests.
The build has no operator credentials. It omits coding-agent packages; install
your preferred agent inside a workspace when needed. Guest SSH uses a fresh
Ed25519 host key. The image includes Chromium's sandbox helper and a guest-local
AppArmor rule for `/usr/lib/chromium/chromium` to create user namespaces. It
retains Incus confinement and does not disable the host-wide namespace
restriction. See Chromium's [upstream explanation](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md).
Third-party tools retain their licenses.

```sh
raft doctor
raft list
box=$(raft new --location lab --ttl 600)
raft exec "$box" -- docker info
raft destroy "$box"
```

To use a versioned image instead of rebuilding, download the archive matching
your host from [releases](https://github.com/Microck/raft/releases), plus
`SHA256SUMS` and `manifest-<architecture>.json`. Verify the downloaded archive
before copying it to the host. Release downloads are public and do not expire
on the CI artifact schedule. Copy the image
archive to the host and import it into the Raft project:

```sh
sha256sum --ignore-missing -c SHA256SUMS
sudo incus --project raft image import ./raft-dev-arm64.tar.gz --alias raft-dev
```

The checksum command must report `OK` for your downloaded archive; missing
checksums or a nonzero exit are failures. CI artifact downloads instead contain
`SHA256SUMS-arm64` or `SHA256SUMS-amd64`, and require a GitHub login.
Use the AMD64 archive filename on an AMD64 host. Set the resulting immutable
fingerprint in controller configuration. Import refuses an existing alias;
choose a fresh candidate alias if another image already uses `raft-dev`.

## Host capacity

Run `raft limits` before allocating. Use `--location lab` to select one host or
`--json` for one JSON object per location. It reads host resources and Incus
metadata without starting guests or changing limits.

```sh
raft limits
raft limits --location lab --json
```

The table covers all six supported CPU/RAM sizes. `Total running` assumes an
otherwise empty host. `More running` accounts for current active box limits and
available RAM. `New boxes` also respects free saved-box slots. Stopped boxes use
saved slots and disk; frozen boxes retain their resource allocation.

The [capacity policy](./contract.md) reserves one CPU and the larger of 2 GiB or
10% of host RAM. Counts assume each box may use its full configured limits and
avoid CPU oversubscription. This is deliberately conservative for simultaneous
builds. Mostly idle workloads can share more CPUs; recommendations do not block
create or resume. The four-saved-box cap remains enforced.

Illustrative totals for 1 CPU / 2 GiB boxes, using exact usable RAM values:

| Host CPUs | Host RAM | Recommended running | Enforced saved maximum |
| ---: | ---: | ---: | ---: |
| 2 | 8 GiB | 1 | 4 |
| 4 | 8 GiB | 3 | 4 |
| 4 | 16 GiB | 3 | 4 |
| 8 | 32 GiB | 4 | 4 |

The OS reports less usable RAM than the machine's advertised size. Near a
boundary, `raft limits` may therefore recommend fewer boxes than this table.
Current available RAM and unrelated services can reduce `More running` further.
This is a point-in-time estimate, not a capacity reservation or benchmark.

Disk growth is not included in those counts. The report shows free host and
shared-pool space and warns below 5 GiB. Image unpacking, Docker layers, snapshots
and backups can use much more; inspect disk before creating or recovering boxes.
The pool remains shared and does not enforce strict per-box disk quotas.

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

Follow [verification](./verification.md) for real E2E suites and their side effects.
Install the optional agent skill by linking `skills/raft-cli` into your agent's
skills directory. The skill source is maintained in this repository.
