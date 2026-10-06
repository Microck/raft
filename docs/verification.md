---
title: Verification
---

# Verification

Raft uses real disposable Incus workspaces, guest processes, files, Docker
builds and network connections. There are no mocked services. Tests verify Raft
behavior, not complete conformance with the hosted Boat service.

## Run the suites

Run allocation-policy checks without a host with `python3 deploy/verify-limits.py`.
Add `--location lab` to verify real create/stop/resume/destroy reporting with one
disposable box. Native image CI runs this test on both architectures.
`python3 deploy/verify-image-tests.py` checks clean and credential-bearing tar archives,
including non-root SSH files, symlinks, hardlinks and special entries.

Configure your own hosts and immutable image fingerprints first. Use dedicated
test infrastructure with free workspace slots and sufficient archive storage.
Each suite destroys only the fixtures it creates. Tests consume resources and
can download public packages. Backups temporarily hold the host lifecycle lock.

```sh
python3 deploy/verify-live.py --location lab --extended
# Optional daemon restart check, only on a dedicated test host:
python3 deploy/verify-live.py --location lab --restart-incus
python3 deploy/verify-controls.py --source lab --target second-host --directory /path/to/archive-storage
python3 deploy/verify-controls.py --source lab --target lab --directory /path/to/archive-storage
python3 deploy/verify-controls.py --source lab --target second-host --directory /path/to/archive-storage --development-image
```

`verify-tools.py <qualified-box>` also runs independently on a running development
workspace. It runs 31 checks as the unprivileged developer user. Language checks
compile or execute real programs. Cargo, Maven, Gradle, npm, pnpm, Bundler and
Composer run small projects. Maven can fetch public build plugins; several other
package-manager fixtures run offline.
It does not make coding-agent model calls or authenticate accounts.

## Coverage

| Implemented workflow | E2E assertion |
| --- | --- |
| Create/list/info/delete | Qualified handle, native state, ownership and cleanup |
| Root terminal/exec | Real PTY, workspace directory, literal arguments and exit code |
| Resource sizing | Effective CPU affinity and cgroup memory limit |
| Host capacity | Mixed-size budgets, stopped/frozen boxes, low RAM/disk and four saved slots; live create/stop/resume/destroy changes in CPU/memory allocations |
| Background jobs | Journal output, nonzero exit status and cancellation of child processes |
| TTL/extend/resume | Host-enforced stop, retained files and uninterrupted lifetime extension |
| Usage | Real cgroup counters and explicitly shared-pool disk scope |
| File transfer | Binary content and paths containing spaces |
| Docker | Real image build and container execution |
| Snapshot/restore/fork | File rollback, independent copy, retained network identity and deterministic concurrent-resume rejection under the real host lock |
| Backup/recovery | Stopped source, no overwrite, snapshots, content, permissions and fresh MAC |
| Private forwarding | Guest HTTP response through controller loopback tunnel |
| Desktop | WebSocket/RFB negotiation, framebuffer, pointer, keyboard, clipboard and reconnect |
| Network separation | Positive service control, negative IPv4/IPv6 peer, host and metadata probes |
| Development image | Real developer-user tool checks, immutable selection and credential-path archive rejection |
| Deployment/firewall | Repeat deployment removes stale rules without restarting Incus; failed filtering blocks normal and socket startup; boot timestamps prove filtering precedes Incus |
| Storage contract | Disposable oversized pool is rejected without automatic resizing |

## Results and limits

The extended lifecycle suite and strengthened tool checks passed on two ARM64
Ubuntu hosts with Incus 6.0.6 on 2026-10-05. Both ran all six CPU/memory combinations and all 31 developer-user
tool checks, real Docker builds, file transfer, stopped snapshots/forks/restore,
jobs, private HTTP and desktop tunnels, network restrictions and scheduled TTL
stop/resume. Desktop assertions included keyboard input, exact clipboard text,
pointer position, framebuffer updates and a second client connection.

A separate headed Chromium check passed as the developer user without disabling
its sandbox. It verified the page DOM and graphical window through Chromium's
debugging protocol, then navigated to the real noVNC web client and asserted its
connected state and 1280×720 canvas. This browser-client assertion is part of the
canonical lifecycle suite. Earlier lifecycle runs also passed the optional
Incus daemon restart check. Configuration boundary checks rejected invalid host definitions
before attempting SSH.

The four-saved-box admission test passed, including stopped boxes and rejection
of a fifth create, fork or recovery operation. Full development-image
portable recovery also passed with a 5,254,224,965-byte native archive, including
snapshots. The restored package inventories matched, all 31 strengthened tool
checks passed, and content, file permissions, fresh MAC and snapshot rollback
assertions passed. The execution wrapper reported exit 143 after the success
message; independent native inventory and archive-directory checks confirmed
that all full-image fixtures and temporary archives were removed. Same-host native
backup/recovery passed with a 385,278,177-byte Debian fixture archive, including
snapshot rollback, file permissions and distinct simultaneous IP/MAC identities.
Reverse-direction cross-host recovery also passed with a 385,282,465-byte
fixture archive. A final canonical admission/control run in the first cross-host
direction also exited 0 with a 385,272,379-byte fixture archive. All fixtures
from those runs were destroyed.

Tests cannot establish every possible application, package feed or long-running
workload. Host loss, adversarial multi-tenant security and exact Boat
image/version parity are not certified. ARM64 host reboot and physical disk
capacity are not certified by the AMD64 VM test described below.
Containers share the host kernel; these checks do not establish VM isolation.
Disk reporting covers the shared pool, not strict per-workspace quotas.
Large backup/import operations hold a host-wide lifecycle lock and can delay
expiry checks; timely TTL enforcement during these operations is not promised.

## Review-fix validation

On 2026-10-06, the extended suite passed on two configured ARM64 hosts after
redeployment of the review fixes. Each ran all 31 tool checks, six sizing
combinations and deterministic queued snapshot/restore/fork rejection when a
source started before its transaction obtained the host lock. Docker, files,
jobs, private HTTP and desktop access, network restrictions and TTL checks passed.
A cross-host Debian backup/recovery run passed with a 385,265,470-byte archive,
including permissions and snapshot rollback. Both hosts had no remaining test
boxes. These checks did not reboot either host.

The image-inspector suite passed 15 real archive cases, including a generated
non-root SSH private key, symlinks, hardlinks, FIFO entries and clean controls.
An isolated network namespace verified initial and repeated firewall application
while preserving an unrelated chain. Package and documentation-site CI passed
for the fix commit.

The [review-fix native run](https://github.com/Microck/raft/actions/runs/37496577945)
passed all native steps on ARM64 and AMD64: development-image builds, extended
lifecycle with daemon restart, capacity transitions, full-image backup/recovery,
startup measurements and clean-image inspection. Both architectures passed the
queued-source race cases. Recovery archives were 3,844,136,311 bytes on ARM64 and
3,985,872,081 bytes on AMD64.

The same run passed fresh provisioning on a disposable AMD64 KVM host. A repeat
deploy removed an injected stale allowance, retained an unrelated chain and left
the Incus daemon start timestamp unchanged. Deliberately failed filtering blocked
both normal and socket-activated Incus startup. After a real host reboot, timestamp
assertions proved that filtering became active before Incus started, and guest
data, snapshots, states, Docker and peer separation passed. A disposable pool
grown to 61 GiB caused deployment to fail and retained that size. ARM64 host reboot
and physical disk capacity remain outside this verification.

## Native image validation

On 2026-10-06, the [native CI run](https://github.com/Microck/raft/actions/runs/37447415532)
built ARM64 and AMD64 images on Ubuntu 24 and passed the extended lifecycle suite
on both. Each ran all six resource combinations and 31 developer-user tool
checks, including sandboxed headless Chromium. Headed Chromium and the real
noVNC client, IPv4/IPv6 peer isolation, host/metadata restrictions, Docker,
files, jobs, snapshots, forks, restore and scheduled TTL stop/resume all passed.
Five boot samples per architecture and clean image exports followed.
[Boot performance](./boot-performance.md) records timings and limits. This does
not certify production provisioning.

The native image jobs in the [release-validation run](https://github.com/Microck/raft/actions/runs/37469540182)
also passed full development-image backup and recovery on both architectures.
The recovered package inventories matched, all 31 tool checks passed again,
and snapshots, file permissions, fresh MAC addresses and distinct concurrent
IP addresses passed. Native backup archives were 3,844,135,523 bytes on ARM64
and 3,985,894,517 bytes on AMD64. Both clean image exports passed fingerprint,
credential-file and machine-ID inspection.

## Fresh-host and reboot validation

The [release run](https://github.com/Microck/raft/actions/runs/37469540182)
passed native image build, extended lifecycle, full-image backup/recovery and
clean-template inspection on both ARM64 and AMD64. A separate KVM VM booted a
checksum-verified Ubuntu 24 AMD64 cloud image and passed the canonical deployer,
a second deployment, release-image import and lifecycle verification.

The VM then rebooted. A changed kernel boot ID and active Incus, network and
expiry services were required before checking retained files, snapshots, running
and stopped workspace states, Docker execution and IPv4/IPv6 peer isolation.
These checks passed. The VM, SSH keys and controller configuration were disposable;
existing operator hosts were not rebooted or reconfigured.

The host test uses a sparse 100 GiB virtual disk and accessible KVM. It preserves
the production deployer's 80 GiB free-disk check but does not prove 80 GiB of
underlying physical capacity. ARM64 host reboot, host loss and cross-architecture
recovery remain unverified. VM acceleration belongs to the test host; released
workspaces are system containers, not VMs.

## Package and privacy checks

Build with `uv build`. Inspect both the wheel and source archive before release.
Source archives use an explicit allowlist to exclude version-control metadata
and local caches. Verify a clean install from the wheel outside the worktree.
Run Ruff, Python compilation, locked dependency sync and secret scans.

A clean source tree does not clean old repository history. Follow
[publishing](./publishing.md) before changing visibility. Do not publish private
controller configuration, workspace archives or guest journals.

Local packaging, Python 3.11/3.14 compilation, Ruff, shell syntax and skill
validation passed. A clean wheel install and CLI execution were checked outside
the worktree. The public package workflow passed on Python 3.11 and 3.14. Native image
validation and startup measurements are recorded in [boot performance](./boot-performance.md).

## Capacity command validation

On 2026-10-06, `raft limits` passed read-only text/JSON inspection and real
create/stop/resume/destroy tests on two configured ARM64 Ubuntu hosts. Each test
removed its own box and confirmed the original inventory and allocations.
Allocation-policy fixtures also checked mixed sizes, frozen boxes, low available
RAM and disk, host reserves, oversubscribed CPUs and saved-slot exhaustion.
The real four-stopped-box admission test also passed: capacity reporting showed
zero free saved slots, and create, fork and recovery rejected a fifth box.
These are conservative allocation checks, not workload saturation benchmarks.
