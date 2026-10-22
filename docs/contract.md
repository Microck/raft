---
title: Runtime contract
---

# Raft contract

Raft manages persistent unprivileged native-architecture Incus system containers on configured ARM64 or AMD64 Linux hosts. Containers share the host kernel. Images must match host CPU architecture; foreign-architecture emulation and VM workflows are not implemented.

## Image releases

- Versioned releases provide clean ARM64 and AMD64 templates with SHA-256 checksums, package inventories, and build provenance.
- Publication requires native lifecycle checks, recovery verification, and fresh AMD64 Ubuntu VM provisioning and reboot checks.
- Disposable AMD64 KVM verification uses a sparse 100 GiB disk and does not certify physical storage capacity or ARM64 host reboot behavior.

## CLI distribution

- npm publishes `@microck/raft` with the same Python sources as the wheel and source archive. Its executable is `raft`; Linux and Python 3.11.8+ are required. Installation runs no scripts and downloads no workspace images.
- `raft --version` reports the release version without reading host configuration. The Python package, npm metadata and release tag must agree before publication.
- Releases include Python wheels, source archives, npm tarballs and both native images with checksums. The release workflow verifies installation outside the checkout and publishes npm with provenance before creating the GitHub release.
- **Self-update**: `raft self-update [--check | --version X.Y.Z]` takes the latest version from the GitHub releases API and compares versions numerically. It upgrades only npm global installs (through the registry, at the same prefix) and uv tools (a release wheel verified against the release `SHA256SUMS`), and refuses checkouts and other layouts. It never downgrades without `--version`, never changes hosts, and prints `raft doctor` as the next step. `--check` exits `100` when a newer release exists.
- **Release notice**: At most once per 24 hours a command checks the latest release in the background and, if it is newer, prints one stderr line after the command. It never installs, prompts, changes stdout or exit status, or waits more than 2 seconds; failures stay silent. `RAFT_NO_UPDATE_CHECK=1` disables it, and `self-update` and `--version` skip it.

## Infrastructure and deployment

- **Strict infrastructure validation**: Deployment refuses conflicting storage or network names and incomplete infrastructure rather than overwriting or repairing state.
- **Storage pool sizing**: The shared Btrfs pool `raft-data` must retain its configured 60 GiB size. The own-disk pool `raft-disks` is opt-in: deployment creates it only when `--disk-pool SIZE` is given, on a fresh host or on any later redeploy, as a loop-backed LVM thin pool (`lvm.use_thinpool=true`) of SIZE whose file is allocated in full on the host disk and whose thin pool uses `--discards nopassdown`. A fresh host needs 80 GiB free, plus SIZE when `--disk-pool` is given; adding the pool later needs SIZE plus 20 GiB free. A host without `raft-disks` is healthy. An existing `raft-disks` keeps its size, a different explicit `--disk-pool` is refused, and a pool with another driver or thick volumes is rejected. Deployment never resizes either pool.
- **Firewall precedence**: Redeployment reapplies firewall rules. Incus startup requires successful firewall application before the daemon starts, including socket activation.
- **Guided onboarding**: `raft host add LOCATION --ssh TARGET` probes the target read-only and refuses unsupported hosts, including macOS, before any change. It prints the exact plan and applies it only after an interactive yes or `--yes`; `--dry-run` stops after the plan. It runs the same deployer as `deploy/deploy.py`, imports an image only after its SHA-256 matches (release `SHA256SUMS` or the local archive digest) and its architecture matches the host, then registers the location with the image fingerprint. It finishes with `doctor` and a disposable create/exec/destroy smoke test; if either fails, it removes the new location from the configuration and leaves host changes for a rerun. It uses existing key-based SSH and never collects passwords or changes SSH, other firewall rules or IP configuration. It requires the deployment files from the npm package or a source checkout.
- **CLI coverage**: The single-operator workflow spans lifecycle, execution, files, snapshots, jobs, tunnels, backup, and advisory capacity inspection; see the [command reference](./reference/cli.md).

## Configuration and lifecycle defaults

| Setting | Behavior |
| --- | --- |
| Host identification | Configuration requires named SSH targets using letters, digits, underscores, or hyphens. |
| Qualified handles | Every handle includes location and instance name (`<location>:<name>`). |
| Image pinning | Workspace creation requires the immutable 64-hex image fingerprint, not an updating alias. |
| Lifecycle defaults | Built-in defaults are the first configured location, 1 CPU, 2 GiB RAM, the shared pool, a persistent box and 600-second lifetime. `~/.config/raft/config.json` can override them for `new` only; explicit flags win, and `--persistent` overrides a `disposable` default. Boxes from a snapshot (`new --from`) and forks keep the source's CPU, RAM and disk instead of any default, configured or built-in; each flag overrides only its own value. `--cpu` takes any positive whole number, `--memory` any positive whole GiB (`16GiB`), `--disk` a positive whole GiB or `shared`, and TTL 60 to 2,592,000 seconds (30 days). |
| Box count and size | Raft limits neither how many boxes a host holds nor their size. CPU or RAM limits larger than the whole host (CPU count, or `MemTotal` rounded up to whole GiB) are refused under the host lock before any change, because Incus would clip them silently. Other failures come from Incus, such as too little pool space. |
| TTL calculation | Running TTL begins after launch completes, using the host clock. Cold image preparation does not consume TTL. |
| Host locking | Creation, lifecycle transitions, and expiry are serialized by host file lock (`/run/lock/raft-incus.lock`). Noninteractive transactions close controller stdin so commands cannot wait for piped input while holding this lock. Exec, SSH, and file-transfer paths remain unchanged. Piped input to `raft exec` is unsupported. Native Incus admin calls bypass CLI policy. |

## Locations, notes and capacity inspection

`raft locations` lists configured locations as JSONL with `location`, `default` and `note` fields without contacting hosts. `raft note LOCATION TEXT` and `raft note LOCATION --clear` edit the optional `note` field in the controller configuration atomically, preserving location order and mode `0600`. Notes are advisory guidance; Raft shows them in `locations`, `capacity` and on stderr from `new`, and never enforces them.

Boxes accept an optional note with a fixed template: `purpose` (required), `owner` and `next`, all nonempty strings. `raft new --purpose TEXT [--owner TEXT] [--next TEXT]` and `raft note BOX ...` store it as JSON in the box's `user.raft.note` Incus key; `raft note BOX --clear` removes it. Each edit replaces the whole note. `raft list` reports it as `note`, or `null`, reports each box's `disk` as `"shared"` or its own disk size, and reports `deleteAt`, the Unix time retention deletes a stopped box, or `""`. `new` always writes the key, so `new --from` never inherits a snapshot's note. Forks and recovered backups keep it unless `fork` or `recover` gets note flags, which replace the whole note. Box notes never change lifecycle behavior.

## Controller defaults and host policies

- **Controller defaults**: `~/.config/raft/config.json` holds `{"defaults": {...}}` with optional `location`, `ttl`, `cpu`, `memory`, `disk` and `disposable` for `new`. Precedence is explicit flag, then configured default, then built-in default. `cpu`, `memory` and `disk` apply only to boxes from the image: `new --from` keeps the snapshot's location, CPU, RAM and disk unless a flag overrides them, and `--disk shared` is refused there. `ttl` and `disposable` apply to `new --from` as well, because a snapshot carries no lifecycle; `new --disposable` and `--persistent` are mutually exclusive overrides. `fork` takes no defaults and is persistent without `--disposable`. A `disk` default on a host without `raft-disks`, or larger than its unreserved space, fails like `--disk` and names `defaults.disk` as the source. Other commands take no defaults. A value is valid exactly when the same text is valid for the flag. Unknown keys, bad values and malformed JSON fail `new` with the file path and key, before any host access. `raft config set/unset` validate first, write atomically under a controller lock, keep mode `0600`, print the change and can repair the key they edit. The file is separate from `incus.json`, so location names never collide with setting names.
- **Policy storage**: `raft policy LOCATION [--require FIELDS|none] [--delete-stopped-after HOURS|never]` sets either host policy or both; each option changes only its own. Required box note fields, which must include `purpose`, live in the Incus project key `user.raft.require`, and `--require none` empties it. Stopped-box retention lives in `user.raft.delete-stopped-after` as a positive whole number of hours, with the time it was set in `user.raft.delete-stopped-after.since`; `never` empties both. Both values are validated before any host change. Each change runs under the host lock, prints the old and new value, records UTC time, the SSH account (`SUDO_USER`) and the self-reported controller user and hostname in the policy's `.changed` key, and logs to the host journal with tag `raft`. Redeployment leaves these keys unchanged.
- **Note policy enforcement**: `new` (including `--from`), `fork`, `recover` and `raft note BOX` check the note the box will carry. The controller reads the policy first and fails with the missing flags and a complete corrected command before changing anything. The host repeats the check inside the transaction that writes the note, under the host lock, so a concurrent policy change cannot be missed. That transaction also holds the oversize and own-disk reservation checks; all of them run before Incus creates anything. `fork` checks the given note or, without one, the source note read under the lock. `recover` checks a given note before upload; an archived note is checked after import, and a failing import is deleted in the same transaction. `note --clear` and replacement notes that drop a required field are rejected.
- **Retention enforcement**: The host expiry worker deletes a stopped Raft box, with its snapshots, once the hours have passed since the later of its `user.raft.stopped-at` and the policy's `.since` time, so setting, shortening or renewing a policy never deletes a box sooner than its hours after the change. It runs on the 15-second timer and on `raft gc`, under the host lock, and writes each deletion to the host journal with tag `raft`. Running boxes are never touched. A stopped Raft box without `user.raft.stopped-at`, from a guest poweroff, a native stop, a failed start or `raft recover`, gets the worker's current time as its stop time, never an earlier guess, whether or not a policy is set. A malformed retention value makes the worker delete nothing by retention, still enforce deadlines, and exit nonzero; the CLI reports it with the fix. Setting retention requires the current worker protocol; clearing it does not.
- **Policy scope**: Policies are guardrails for agents, not access controls. Anyone with host SSH and sudo can change them or create boxes with native Incus commands, and controllers without note policy support do not check it. A native `incus start` leaves a stale stop time that retention trusts. `raft config` reports every location's policies and last changes.

`raft capacity [--location NAME] [--json]` inspects hosts without modifying state. Text and JSON (`notice`) lead with: "Advisory estimate only. Raft enforces no box count or size budget; create the boxes your task needs." It reports effective CPUs, total and available RAM, saved and active box counts, active resource limits, headroom, free space in the host disk and the shared pool, and for `raft-disks` its data space, reserved and used bytes and metadata use (`boxDiskPool`, `null` when own box disks are not enabled).

- **Reserve calculations**: Estimates leave out 1 CPU and the larger of 2 GiB or 10% host RAM.
- **Example shapes**: `estimates` lists shapes doubling from 1 CPU / 2 GiB at 2 GiB per CPU while they fit an empty host, with `fitEmptyHost` and `fitNow` counts. No count is capped.
- **Headroom**: Subtracts active configured limits and keeps available RAM above the reserve. Every non-`Stopped` state (including `Frozen`) counts as active.
- **Advisory only**: Nothing in the report blocks creation, resume or fork. Disk space below 5 GiB and `raft-disks` thin pool metadata above 80% trigger warnings; a missing `raft-disks` does not.

## Container lifecycle and execution

- **Stop and resume**: Stop terminates guest processes and preserves disk contents; stopped boxes retain disk but do not reserve guest RAM. Resume starts the container anew with an explicit TTL without restoring process memory.
- **Lifetime extension**: `raft extend --ttl` resets a running box's deadline from current host time without restarting; stopped boxes require `raft resume`.
- **Host worker contract**: `doctor`, disposable creation/fork, prune and setting a retention policy require the current worker protocol (2 since 0.6.0, which added retention). `doctor` checks it without running expiry or changing boxes, and reports the failing location. A mismatch fails before executing deletion policy or creating a disposable box; deploy the current checkout explicitly. Installing a new CLI does not update host workers.
- **Disposable lifecycle**: `new --disposable`, or a configured `disposable` default without `--persistent`, opts a box into deletion on stop or TTL expiry, including its snapshots. Clones and recovered boxes default to persistent whatever the default; fork accepts an explicit `--disposable`.
- **Manual cleanup**: `prune --older-than DAYS` previews persistent stopped boxes with a recorded stop timestamp. `--yes` deletes matching boxes under the host lifecycle lock. Running boxes and boxes without a recorded stop time are excluded. Automatic deletion of persistent stopped boxes happens only under a host retention policy.
- **Resizing**: Resume and fork accept optional CPU/RAM overrides, preserving omitted values. Resume validates stopped state under the lock before changing sizing. Neither changes a box's disk.
- **Expiration cleanup**: The host timer checks expirations every 15 seconds, stopping expired persistent containers, deleting expired disposable containers and applying stopped-box retention. Expiry may be delayed by graceful shutdown, long locked transactions, and host outage until startup.
- **Destroy**: Removes the container instance and its snapshots, releasing storage. Destroy confirms absence; SSH failure is not proof of deletion. Disposable boxes created with `new --disposable` are deleted on explicit stop or expiry. Persistent boxes still require explicit destroy, prune or a retention policy for deletion.
- **Execution transport**: Incus exec and file APIs travel over authenticated host SSH without a public Incus listener or injected credentials. Interactive SSH runs a PTY over host SSH and Incus exec.
- **State inspection and tunnels**: `raft list` and `raft info` read native metadata without guest process counters during shutdown; tunnels use the managed NIC.
- **Resource reporting**: `raft usage` reports running cgroup memory, CPU microseconds, CPU affinity, and filesystem space with `filesystemScope`: the shared pool, or `own SIZE box disk`. It provides no billing.
- **Recursive transfers**: `upload/download --recursive` require a running workspace and Python 3.11.8+ in the guest. They stream a tar archive and publish a new destination directory atomically without overwriting or merging. Relative links contained within the tree, regular files, directories and internal hardlinks are supported; escaping paths/links and special files are rejected. Source entries are pinned with no-follow descriptors before metadata inspection, file reads or directory descent, so pathname replacement cannot redirect access outside the selected tree. Failed transfers leave existing destinations unchanged.
- **Directory transfer completion**: The receiver verifies the sender's exact archive byte count before publication. A truncated stream, including one ending between complete entries, is rejected without publishing a partial tree.
- **File transfer failures**: Controller write errors return an error before replacing a download destination or publishing a backup archive.

## Security and network isolation

- **User namespaces**: Guest root maps to an unprivileged host UID. Container nesting and intercepted syscalls support Docker without mounting the host Docker socket.
- **Network filtering**: Dedicated bridge `rfbr0` allows DNS, DHCP, and outbound internet. Interface-scoped firewall rules block guest access to host services, cloud metadata, and private address ranges.
- **Bridge peer isolation**: A native nftables bridge rule blocks all inter-workspace frames on `rfbr0`. IPv6 and link-local peer traffic are blocked.
- **Private tunnels**: Forwarding listeners bind strictly to controller loopback (`127.0.0.1`). Guest services must listen on `0.0.0.0` or their managed IP, not guest loopback.
- **Image hygiene**: Development images omit coding agents, personal keys, and host credentials. SSH host keys are generated fresh on first boot. Release inspection rejects root and non-root SSH content, guest host keys, and credential-path links or nonregular entries.

## Storage, snapshots, and cloning

- **Shared pool**: Boxes default to a dedicated 60 GiB Btrfs pool per host with instant snapshots and clones. Btrfs quotas cannot bound a box there, because nested subvolumes (Docker's, for example) escape them. The native compressed image cache resides outside both pools, so host disk space still matters.
- **Own box disks**: `new --disk SIZE` puts the box's root on an LVM thin volume of SIZE in `raft-disks`, formatted ext4. All guest writes, including Docker data, land in that filesystem, so writes fail with ENOSPC at SIZE. New own-disk boxes are thin snapshots of the cached image volume, and forks, templates and snapshots of own-disk boxes are thin snapshots of their source. The pool is never overcommitted: under the host lock, before `new`, `new --from`, `fork`, `snapshot` or `recover` adds thin volumes to `raft-disks`, Raft sums the sizes of every thin volume in the pool (from `lvs`) plus the new ones and refuses, before creating anything, if the total exceeds the thin pool's data space. A new volume counts as the larger of its size and its source's; the first own-disk box on a host also counts the image volume (`volume.size`, or 10 GiB); a recovered backup counts its size once per snapshot plus once. `restore` reserves nothing. On a host without `raft-disks`, `new --disk` and recovery of an own-disk backup fail before creating anything and name the deploy command that enables the pool. `fork` and `new --from` keep the source disk; `new --from ... --disk SIZE` copies onto an own disk of SIZE. A disk too small for its contents fails creation.
- **Stopped-state requirement**: Snapshots, restores and forks require a stopped source for consistent disk contents. Operations run under the host lifecycle lock.
- **Snapshot restore**: `raft restore` rolls back disk blocks via `--diskonly`, retaining container configuration, limits, and MAC address.
- **Snapshot management**: `snapshot-delete <box> <name>` removes one named snapshot under the lock. `new --from LOCATION:BOX/SNAPSHOT` creates a same-host box from that snapshot, with the snapshot's CPU, RAM and disk unless `--cpu`, `--memory` or `--disk` overrides them, and a fresh network identity. The source may run; the named snapshot is immutable. Templates retain filesystem secrets.
- **Workspace forks**: `raft fork` copies filesystem data and secrets to a new container with an independent Incus name, fresh MAC address, and renewed TTL. Source snapshots are not inherited.

## Background jobs and desktop

- **Detached jobs**: Commands run as guest systemd units with journal logging and exit status. Job inspection and cancellation reject unknown IDs in the selected workspace. `raft cancel` stops the unit's control group. Stopping the container terminates jobs.
- **Desktop service**: On-demand Xvfb, Openbox, and noVNC stack on display `:99` without a login manager. Clipboard exchange is available without login-manager delay. Desktop provides full workspace access, not browser confinement. Closing the tunnel leaves the service running.

## Backup and recovery

- **Export safeguards**: `raft backup` exports stopped containers and snapshots to a local archive. Destination path must not exist, requires mode `0600`, and uses atomic hard-link publication.
- **Host locking**: Export holds the host lifecycle lock, temporarily delaying expiry checks on the source host.
- **Recovery**: `raft recover` imports archives into a stopped container with a fresh name, new MAC address, and cleared TTL. It reads the archive's recorded pool from `backup/index.yaml`: own-disk backups return to `raft-disks` with their size after the reservation check, failing on a host without the pool, and other archives land in `raft-data`. The host note policy applies after the reservation check. Recovery never overwrites existing boxes.
- **Archive security**: Archives contain full container filesystems, including credentials. Native archive configuration is not sanitized; backups do not imply scheduled off-host uploads. Store archives privately and import only trusted exports.

## Disposable foreground CI

`run` creates a uniquely marked disposable box and establishes its fixed host
expiry before startup. It streams one foreground command without retrying it,
optionally transfers a Git-filtered or explicitly complete source tree, collects
result directories even after payload failure, then checks ownership and deletes
under the lifecycle lock. The controller handles timeout and cancellation with
bounded collection/cleanup attempts and reports unconfirmed cleanup as failure.
Host expiry is a backstop dependent on the worker and lock, not a strict deletion
guarantee after transport failure. Existing nonzero payload status is preserved.
This is not a registered runner, a scoped CI identity or VM-grade isolation.
See [Disposable CI jobs](guides/ci-jobs.mdx) for the full contract.
