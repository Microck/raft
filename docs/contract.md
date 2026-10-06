---
title: Runtime contract
---

# Raft contract

Raft manages persistent unprivileged native-architecture Incus system containers on configured ARM64 or AMD64 Linux hosts. Containers share the host kernel. Images must match host CPU architecture; foreign-architecture emulation and VM workflows are not implemented.

## Image releases

- Versioned releases provide clean ARM64 and AMD64 templates with SHA-256 checksums, package inventories, and build provenance.
- Publication requires native lifecycle checks, recovery verification, and fresh AMD64 Ubuntu VM provisioning and reboot checks.
- Disposable AMD64 KVM verification uses a sparse 100 GiB disk and does not certify physical storage capacity or ARM64 host reboot behavior.

## Infrastructure and deployment

- **Strict infrastructure validation**: Deployment refuses conflicting storage or network names and incomplete infrastructure rather than overwriting or repairing state.
- **Storage pool sizing**: Existing owned Btrfs pools must retain the configured 60 GiB size; drift is rejected without automatic resizing.
- **Firewall precedence**: Redeployment reapplies firewall rules. Incus startup requires successful firewall application before the daemon starts, including socket activation.
- **CLI coverage**: The single-operator workflow spans lifecycle, execution, files, snapshots, jobs, tunnels, backup, and capacity inspection; see the [command reference](./reference/cli.md).

## Configuration and lifecycle defaults

| Setting | Behavior |
| --- | --- |
| Host identification | Configuration requires named SSH targets using letters, digits, underscores, or hyphens. |
| Qualified handles | Every handle includes location and instance name (`<location>:<name>`). |
| Image pinning | Workspace creation requires the immutable 64-hex image fingerprint, not an updating alias. |
| Lifecycle defaults | Defaults to first configured location, 1 CPU, 2 GiB RAM, and 600-second lifetime. Supported choices are 1 or 2 CPUs, 1/2/4 GiB RAM, and 60 to 2,592,000 seconds (30 days) TTL. |
| Admission limit | Hard ceiling of four saved boxes per host, including stopped boxes. |
| TTL calculation | Running TTL begins after launch completes, using the host clock. Cold image preparation does not consume TTL. |
| Host locking | Creation, lifecycle transitions, and expiry are serialized by host file lock (`/run/lock/raft-incus.lock`). Native Incus admin calls bypass CLI policy. |

## Capacity and admission inspection

`raft limits [--location NAME] [--json]` inspects hosts without modifying state. It reports effective CPUs, total and available RAM, saved and active box counts, active resource limits, and disk space.

- **Reserve calculations**: Recommends running boxes after reserving 1 CPU and the larger of 2 GiB or 10% host RAM.
- **Total capacity**: Assumes an empty dedicated host, capped at four boxes.
- **Additional running**: Subtracts active configured limits and evaluates available RAM minus host reserves. Every non-`Stopped` state (including `Frozen`) counts as active.
- **New box capacity**: Bounded by remaining saved-box slots: `min(more running, max(0, 4 - saved boxes))`.
- **Advisory estimates**: CPU and memory calculations are advisory planning estimates, not admission guarantees. Disk space below 5 GiB triggers diagnostic warnings. The four-saved-box admission cap is strictly enforced.

## Container lifecycle and execution

- **Stop and resume**: Stop terminates guest processes and preserves disk contents; stopped boxes retain disk but do not reserve guest RAM. Resume starts the container anew with an explicit TTL without restoring process memory.
- **Lifetime extension**: `raft extend --ttl` resets a running box's deadline from current host time without restarting; stopped boxes require `raft resume`.
- **Expiration cleanup**: The host timer checks expirations every 15 seconds, stopping expired containers without deleting files. Expiry may be delayed by graceful shutdown, long locked transactions, and host outage until startup.
- **Destroy**: Removes the container instance and its snapshots, releasing storage. Destroy confirms absence; SSH failure is not proof of deletion. Disposable runs must execute destroy.
- **Execution transport**: Incus exec and file APIs travel over authenticated host SSH without a public Incus listener or injected credentials. Interactive SSH runs a PTY over host SSH and Incus exec.
- **State inspection and tunnels**: `raft list` and `raft info` read native metadata without guest process counters during shutdown; tunnels use the managed NIC.
- **Resource reporting**: `raft usage` reports running cgroup memory, CPU microseconds, CPU affinity, and shared pool space. It does not provide per-box disk accounting or billing.

## Security and network isolation

- **User namespaces**: Guest root maps to an unprivileged host UID. Container nesting and intercepted syscalls support Docker without mounting the host Docker socket.
- **Network filtering**: Dedicated bridge `rfbr0` allows DNS, DHCP, and outbound internet. Interface-scoped firewall rules block guest access to host services, cloud metadata, and private address ranges.
- **Bridge peer isolation**: A native nftables bridge rule blocks all inter-workspace frames on `rfbr0`. IPv6 and link-local peer traffic are blocked.
- **Private tunnels**: Forwarding listeners bind strictly to controller loopback (`127.0.0.1`). Guest services must listen on `0.0.0.0` or their managed IP, not guest loopback.
- **Image hygiene**: Development images omit coding agents, personal keys, and host credentials. SSH host keys are generated fresh on first boot. Release inspection rejects root and non-root SSH content, guest host keys, and credential-path links or nonregular entries.

## Storage, snapshots, and cloning

- **Storage pool**: A dedicated 60 GiB Btrfs pool per host supports snapshots and clones. Nested container subvolumes prevent strict per-box quota enforcement. The native compressed image cache resides outside the shared pool, so host disk space still matters.
- **Stopped-state requirement**: Snapshots, restores and forks require a stopped source for consistent disk contents. Operations run under the host lifecycle lock.
- **Snapshot restore**: `raft restore` rolls back disk blocks via `--diskonly`, retaining container configuration, limits, and MAC address.
- **Workspace forks**: `raft fork` copies filesystem data and secrets to a new container with an independent Incus name, fresh MAC address, and renewed TTL. Source snapshots are not inherited.

## Background jobs and desktop

- **Detached jobs**: Commands run as guest systemd units with journal logging and exit status. Job inspection and cancellation reject unknown IDs in the selected workspace. `raft cancel` stops the unit's control group. Stopping the container terminates jobs.
- **Desktop service**: On-demand Xvfb, Openbox, and noVNC stack on display `:99` without a login manager. Clipboard exchange is available without login-manager delay. Desktop provides full workspace access, not browser confinement. Closing the tunnel leaves the service running.

## Backup and recovery

- **Export safeguards**: `raft backup` exports stopped containers and snapshots to a local archive. Destination path must not exist, requires mode `0600`, and uses atomic hard-link publication.
- **Host locking**: Export holds the host lifecycle lock, temporarily delaying expiry checks on the source host.
- **Recovery admission**: `raft recover` imports archives into a stopped container with a fresh name, new MAC address, and cleared TTL, while enforcing the four-box admission limit. Recovery never overwrites existing boxes.
- **Archive security**: Archives contain full container filesystems, including credentials. Native archive configuration is not sanitized; backups do not imply scheduled off-host uploads. Store archives privately and import only trusted exports.
