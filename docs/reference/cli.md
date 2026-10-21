---
title: CLI command reference
description: Complete command-line reference for the Raft controller CLI.
---

# CLI command reference

Command-line reference for the `raft` controller tool.

Syntax:
`raft <subcommand> [options] [arguments]`

`raft --help` lists commands, `raft <subcommand> --help` lists its arguments,
and `raft --version` prints the installed version without loading configuration.
Output is command-specific; see [Scripting and CI](../guides/scripting-ci.mdx).

Workspaces are referenced by location-qualified handles formatted as `<location>:<instance-name>` (for example `lab:rf-a1b2c3d4e5f60718`).

## Command index

| Task | Commands |
| --- | --- |
| Workspace lifecycle | [`new`](#new), [`stop`](#stop), [`resume`](#resume), [`extend`](#extend), [`destroy`](#destroy) |
| Execution and inspection | [`exec`](#exec), [`ssh`](#ssh), [`info`](#info), [`usage`](#usage) |
| Background job | [`status`](#status), [`logs`](#logs), [`cancel`](#cancel) |
| File transfer | [`upload`](#upload), [`download`](#download) |
| Snapshots and cloning | [`snapshot`](#snapshot), [`snapshots`](#snapshots), [`restore`](#restore), [`snapshot-delete`](#snapshot-delete), [`fork`](#fork) |
| Port forwarding and desktop | [`forward`](#forward), [`desktop`](#desktop) |
| Backup and recovery | [`backup`](#backup), [`recover`](#recover) |
| Locations | [`host add`](#host-add), [`locations`](#locations), [`note`](#note) |
| Defaults and policies | [`config`](#config), [`policy`](#policy) |
| Host inspection and maintenance | [`capacity`](#capacity), [`doctor`](#doctor), [`list`](#list), [`gc`](#gc), [`prune`](#prune) |

## Workspace lifecycle commands

### `new`

Create and launch a new workspace container.

`raft new [--location LOCATION] [--ttl TTL] [--cpu N] [--memory SIZE] [--disk SIZE] [--disposable] [--from LOCATION:BOX/SNAPSHOT] [--purpose TEXT [--owner TEXT] [--next TEXT]]`

- `--location LOCATION`: Target host location in `~/.config/raft/incus.json`. Built-in default: first configured location.
- `--ttl TTL`: Lifetime in seconds before automatic stop (`60` to `2592000`). Built-in default: `600`.
- `--cpu N`: CPU limit, any whole number from `1` up to the host's CPU count. Built-in default: `1`, or the snapshot's with `--from`.
- `--memory SIZE`: Memory limit in whole GiB, for example `16GiB`, up to the host's RAM. Built-in default: `2GiB`, or the snapshot's with `--from`.
- `--disk SIZE`: Give the box its own disk of SIZE in whole GiB, for example `20GiB`, instead of the shared pool. Needs a host deployed with `--disk-pool`. The box, and Docker inside it, cannot write past SIZE, and the pool reserves SIZE when the box is created. `--disk shared` asks for the shared pool, overriding a configured `disk`. Built-in default: the shared pool, or with `--from` the snapshot's disk. See [Box sizing and own disks](./capacity.mdx#own-box-disks).

An omitted option takes the controller default set with [`config`](#config),
then the built-in default. With `--from`, configured `cpu`, `memory` and `disk`
defaults do not apply: the box keeps the snapshot's size unless a flag overrides
it, and `--disk shared` is refused.

- `--disposable`: Delete the box and snapshots on stop or expiry. Off by default.
- `--from LOCATION:BOX/SNAPSHOT`: Copy a named snapshot on its source host instead of the configured image. The box keeps the snapshot's CPU, RAM and disk unless `--cpu`, `--memory` or `--disk` overrides them; the source can be running. Cannot target a different location.
- `--purpose TEXT`, `--owner TEXT`, `--next TEXT`: Record a [box note](#note). `--owner` and `--next` need `--purpose`. Without them the box starts with no note, including boxes created `--from` a noted snapshot. A host [policy](#policy) can require fields.

Returns the qualified workspace handle. If the location has a note, `new` prints
it to stderr; stdout still contains only the handle. Raft limits neither the
number of boxes nor their size; requests larger than the whole host, own disks
on a host without `raft-disks`, or own disks larger than the unreserved
`raft-disks` space, fail with the reason before anything is created.

### `stop`

Stop a running workspace.

`raft stop <box>`

Terminates guest processes and stops the container. Persistent boxes retain filesystem contents, packages and snapshots. Disposable boxes are deleted, including their snapshots.

### `resume`

Start a stopped workspace with a renewed lifetime deadline.

`raft resume <box> --ttl TTL [--cpu N] [--memory SIZE]`

- `--ttl TTL`: Lifetime in seconds from resumption (`60` to `2592000`). Required.

CPU/RAM overrides on resume apply before startup. Omitted values stay unchanged.
A running box is rejected before changing its limits. Resume never changes the
box's disk.

### `extend`

Reset the expiration deadline for a running workspace without restarting it.

`raft extend <box> --ttl TTL`

- `--ttl TTL`: Lifetime in seconds from the current host timestamp. Required.

### `destroy`

Permanently delete a workspace container and its snapshots.

`raft destroy <box>`

Stops the container if running, removes all associated snapshots, and releases its storage, including an own disk.

## Execution and inspection commands

### `exec`

Execute a command inside a workspace.

`raft exec <box> [--detach] [--] <command ...>`

- `--detach`: Run in background as a guest systemd unit. Returns a job ID immediately.
- `<command ...>`: Command and arguments passed to `execve`. For shell features (pipes, redirects, variables), wrap with `bash -lc '...'`.

Example:
```sh
job=$(raft exec "$box" --detach -- make test)
```

### `ssh`

Open an interactive root terminal session inside the workspace over host SSH and Incus exec.

`raft ssh <box>`

### `info`

Display container metadata from Incus in JSON format, including status, IP addresses, resource limits, and timestamps. `expanded_devices.root` shows the disk: `pool: raft-data` for the shared pool, or `pool: raft-disks` with the own disk's `size`.

`raft info <box>`

### `usage`

Display active resource consumption for a running workspace: cgroup memory, CPU microseconds, CPU affinity, and filesystem space. `filesystemScope` says whether the space is the shared pool or the box's own disk.

`raft usage <box>`

## Background job commands

`status`, `logs` and `cancel` require a job ID returned by `exec --detach` in the selected running workspace. Unknown jobs return an error.

Cancelled jobs remain inspectable while their guest journal records exist. Rotating old logs does not prevent inspection of a live job.

### `status`

Check the execution state of a detached background job.

`raft status <box> <job>`

Queries `systemctl show` inside the container and outputs `ActiveState`, `SubState`, and `ExecMainStatus`.

### `logs`

Retrieve journal output from a detached background job using `journalctl --no-pager -o cat -u <job>`.

`raft logs <box> <job>`

### `cancel`

Terminate a running detached background job using `systemctl stop <job>`.

`raft cancel <box> <job>`

## File transfer commands

### `upload`

Transfer a local file from the controller to an absolute path inside the workspace.

`raft upload <box> <source> <destination> [--recursive]`

- `<source>`: Path to local file on controller.
- `<destination>`: Target absolute path inside container.

### `download`

Transfer a file from an absolute path inside the workspace to the local controller.

`raft download <box> <source> <destination> [--recursive]`

- `<source>`: Absolute path to file inside container.
- `<destination>`: Local file destination path on controller.

`--recursive` transfers a directory tree into a new destination directory. It requires a running workspace and Python 3.11.8+ in the guest. Parent directories must exist. Existing destinations are rejected, including empty directories and symlinks. Internal relative links, hardlinks, executable files and empty directories are supported; escaping links and special files are rejected. Archives use temporary controller disk space. Single-file transfers still work on stopped boxes.

## Snapshots and cloning commands

### `snapshot`

Create a named point-in-time snapshot of a stopped workspace. Names use alphanumeric characters, underscores, or hyphens up to 64 characters.

`raft snapshot <box> <name>`

A snapshot of a box with its own disk is a thin snapshot that reserves the box's disk size in `raft-disks`; it fails before anything is created when the pool lacks unreserved space.

### `snapshots`

List existing snapshots and creation timestamps for a workspace.

`raft snapshots <box>`

### `snapshot-delete`

Delete one named snapshot without deleting the workspace. The box can be running.

`raft snapshot-delete <box> <name>`

### `restore`

Roll back a stopped workspace filesystem to a named snapshot using `--diskonly`, preserving container configuration and MAC address.

`raft restore <box> <name>`

### `fork`

Clone a stopped workspace into an independent container on the same host and start it.

`raft fork <box> --ttl TTL [--cpu N] [--memory SIZE] [--disposable] [--purpose TEXT [--owner TEXT] [--next TEXT]]`

- `--ttl TTL`: Lifetime in seconds for the clone. Required.
- `--purpose TEXT`, `--owner TEXT`, `--next TEXT`: Replace the clone's whole note. Without them the clone keeps the source note.

Omitted CPU/RAM values inherit the source limits; controller defaults never apply to forks. The clone keeps the source's disk: the shared pool, or an own disk of the same size as a thin snapshot, which reserves that size in `raft-disks`. To copy a box onto a disk of another size, snapshot it and use `new --from BOX/SNAPSHOT --disk SIZE`. The clone is persistent unless `--disposable` is supplied, regardless of the source policy.
The note the clone ends up with must satisfy the host [policy](#policy).

Returns the qualified handle of the clone.

## Port forwarding and desktop commands

### `forward`

Open a private SSH tunnel between a guest port and controller loopback (`127.0.0.1`).

`raft forward <box> --remote REMOTE --local LOCAL`

- `--remote REMOTE`: Port number inside the workspace (bound to `0.0.0.0` or container IP).
- `--local LOCAL`: Port number on controller loopback (`127.0.0.1`).

Runs in foreground; press `Ctrl+C` to close.

### `desktop`

Start `raft-desktop.service` inside the container and open a private tunnel for noVNC.

`raft desktop <box> [--local LOCAL]`

- `--local LOCAL`: Controller port to bind (default: `6080`). Access at `http://127.0.0.1:<LOCAL>/vnc.html`. Press `Ctrl+C` to close.

## Backup and recovery commands

### `backup`

Export a stopped workspace and its snapshots to a compressed archive on the controller.

`raft backup <box> <destination>`

- `<destination>`: Output archive path (e.g. `./workspace.tar.gz`). Must not exist. Created with mode `0600`.

### `recover`

Import a portable backup archive onto a host as a fresh stopped workspace.

`raft recover <source> --location LOCATION [--purpose TEXT [--owner TEXT] [--next TEXT]]`

- `<source>`: Path to archive file on controller.
- `--location LOCATION`: Target host location. Required.
- `--purpose TEXT`, `--owner TEXT`, `--next TEXT`: Replace the recovered box's whole note. Without them it keeps the archived note.

Under a host [policy](#policy), a given note is checked before the upload. An
archived note is checked after import; if it fails, the imported box is deleted
in the same locked step.

Returns the qualified handle of the imported stopped workspace. A backup of a box with its own disk is recovered onto an own disk of the same size in `raft-disks`, which must exist on the destination and have room to reserve that size for the box and each of its snapshots; any other backup lands in the shared pool.

## Location commands

### `host add`

Check, provision and register a new workspace host, then prove it works.

`raft host add LOCATION --ssh TARGET [--image-release TAG | --image-archive PATH | --build] [--disk-pool SIZE] [--dry-run] [--yes]`

- `LOCATION`: New location name. Existing locations are rejected.
- `--ssh TARGET`: Key-based SSH target, such as `ubuntu@192.0.2.10` or an `~/.ssh/config` alias. Accept the host key interactively first.
- `--image-release TAG`: Download `raft-dev-<arch>.tar.gz` from this release on the host and verify it against the release `SHA256SUMS`. Default: the CLI's own version.
- `--image-archive PATH`: Upload and import a local image archive instead.
- `--build`: Build the image on the host from Debian 13 instead.
- `--disk-pool SIZE`: Enable [own box disks](./capacity.mdx#own-box-disks) with a `raft-disks` thin pool of SIZE, in whole GiB. Default: not enabled. The pool's file takes its full size on the host disk when created. To enable them on a host that is already deployed, run `python3 deploy/deploy.py --location LOCATION --disk-pool SIZE`. An existing pool keeps its size; a different explicit value is refused.
- `--dry-run`: Probe the host and print the plan without changing anything.
- `--yes`: Apply the plan without the interactive prompt. Without a terminal, `--yes` is required.

Before any change, a read-only probe checks SSH, passwordless sudo, Ubuntu 22.04 or
24.04, ARM64 or AMD64, python3, at least 2 CPUs, 8 GiB RAM and, for a fresh host,
80 GiB of free disk, plus the `--disk-pool` size when given. Unsupported targets, including macOS, fail with the reasons and
no changes. The command then runs the deployer, imports the image only if its
checksum and architecture match, adds the location with the pinned fingerprint,
and runs `doctor` plus a disposable create/exec/destroy smoke test. If the final
checks fail, it removes the location again; host changes remain and a rerun
reuses them.

```sh title="CONTROLLER: Onboard a host"
raft host add lab --ssh ubuntu@192.0.2.10 --dry-run
raft host add lab --ssh ubuntu@192.0.2.10
```

The deployment files ship in the npm package and source checkouts. The Python
wheel contains only the CLI; use npm or a checkout to onboard hosts.

### `locations`

List configured locations without contacting hosts.

`raft locations`

Prints one JSON object per location (JSONL) in configuration order, with
`location`, `default` and `note` fields. `default` is true for the location that
`new` uses without `--location`. `note` is empty when none is set.

### `note`

Set or clear the note of a location or a box.

`raft note LOCATION TEXT`
`raft note LOCATION --clear`
`raft note BOX --purpose TEXT [--owner TEXT] [--next TEXT]`
`raft note BOX --clear`

Location notes are operator guidance stored in `~/.config/raft/incus.json`. Use
them to record which host is preferred or what a host should not run. Raft shows
them in `locations`, `capacity` and `new`.

Box notes tell other agents and people why a box exists. They follow one
template, stored on the box as JSON in the Incus key `user.raft.note`:

| Field | Meaning | Example |
| --- | --- | --- |
| `purpose` (required) | What the box is for | `Integration tests for PR 42` |
| `owner` | Who uses it | `codex session in ~/work/app` |
| `next` | What happens to it next | `Destroy after the tests pass` |

Each call replaces the whole note, on running or stopped boxes. `raft list`
shows it as `note`. Forks and recovered backups keep the source box's note
unless `fork` or `recover` gets note flags; update it if the copy has a
different job.

Empty values are rejected; use `--clear` to remove a note. A host
[policy](#policy) can require fields, and then rejects notes and `--clear` that
would leave them out. Otherwise Raft does not interpret notes.

### `config`

Show or change the controller defaults for `new`.

`raft config`
`raft config set KEY VALUE`
`raft config unset KEY`

- `KEY`: One of `location`, `ttl`, `cpu`, `memory`, `disk`.
- `VALUE`: Accepted exactly when the same text is accepted by the matching `new` flag. A `location` must be configured.

`cpu`, `memory` and `disk` size only boxes created from the image. `new --from`
and `fork` keep the source's CPU, RAM and disk; only explicit flags change them.
A `disk` default on a location without `raft-disks`, or larger than its
unreserved space, fails `new` with the same error as `--disk`, plus a sentence
naming `defaults.disk` as the source. `--disk shared` overrides it for one box.

Defaults live in `~/.config/raft/config.json` as `{"defaults": {"KEY": VALUE}}`.
`set` and `unset` print the old and new value, write atomically under a
controller lock and keep mode `0600`. An unknown key, bad value or malformed
file makes `new` fail with the file path and key; `set` or `unset` that key to
repair it.

`raft config` prints one JSON object: `file`, `defaults` (each key's `value` and
`source`, `config` or `built-in`) and `policy` (each location's `require` list
and `changed` record). It contacts every configured host to read policies.
See [Defaults and note policies](../guides/config-policy.mdx).

### `policy`

Set or clear the box note fields a location's host requires.

`raft policy LOCATION --require FIELDS`
`raft policy LOCATION --clear`

- `--require FIELDS`: Comma-separated fields from `purpose`, `owner`, `next`. Must include `purpose`.
- `--clear`: Stop requiring fields.

The policy is stored on the host in the Incus project key `user.raft.require`,
so it applies to every controller. `new`, `fork`, `recover` and `note` check it
on the controller for an actionable error, then again in the host transaction
that writes the note. The command prints the change and the host records the
time, SSH account and controller in `user.raft.require.changed` and the host
journal (`journalctl -t raft`).

This is a guardrail for agents, not a security boundary: SSH and sudo on the
host can change the policy or bypass it, and older controllers do not check it.

## Host inspection and maintenance commands

### `capacity`

Estimate how many more boxes a host can run well. The report is advice: Raft
enforces no box count or size budget, so create the boxes your task needs.

`raft capacity [--location LOCATION] [--json]`

- `--location LOCATION`: Filter report to a specific host.
- `--json`: Output report as JSON.

Each report leads with that advisory notice and includes the location's `note`,
if any. See [Box sizing and capacity](./capacity.mdx) for the fields.

### `doctor`

Inspect host connectivity, Incus version, expiry-worker compatibility, active systemd units (`incus`, `raft-expire.timer`, `raft-network.service`), root filesystem usage, status of the shared `raft-data` pool and, when own box disks are enabled, the `raft-disks` pool, and saved box count across configured hosts. Without `raft-disks` it prints `own box disks: not enabled (redeploy with --disk-pool SIZE)` and does not fail. An incompatible worker returns a nonzero exit status and names the failing location without running expiry or modifying boxes.

`raft doctor`

### `list`

List existing workspace containers across configured hosts.

`raft list [--location LOCATION]`

Prints one JSON object per box (JSONL), with `box`, `state`, `stopAt`, `disk` and `note` fields.
`disk` is `"shared"` for the shared pool or the own disk's size, such as `"20GiB"`.
`note` is the box note object, or `null` when the box has none.
An empty inventory prints no records. See [Scripting and CI](../guides/scripting-ci.mdx)
for output formats, exit codes, readiness, and failure handling.

### `gc`

Enforce deadlines immediately. Persistent boxes stop; disposable boxes are deleted.

`raft gc [--location LOCATION]`

### `prune`

Preview deletion of persistent stopped boxes older than the selected number of days.

`raft prune --older-than DAYS [--location LOCATION] [--yes]`

Days must be at least one. Without `--yes`, this only prints JSONL candidates. With `--yes`, it deletes matching boxes and their snapshots under the host lock. Running boxes and boxes without a recorded stop time are excluded. Stop timestamps come from Raft stop/expiry, not box creation. No automatic retention policy is enabled.

Disposable creation/fork and prune require the current expiry worker installed through the deployer. A mismatch fails with deployment guidance; the CLI does not install or adapt an older worker.
