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
| Host inspection and maintenance | [`limits`](#limits), [`doctor`](#doctor), [`list`](#list), [`gc`](#gc), [`prune`](#prune) |

## Workspace lifecycle commands

### `new`

Create and launch a new workspace container.

`raft new [--location LOCATION] [--ttl TTL] [--cpu {1,2}] [--memory {1GiB,2GiB,4GiB,8GiB}] [--disposable] [--from LOCATION:BOX/SNAPSHOT] [--purpose TEXT [--owner TEXT] [--next TEXT]]`

- `--location LOCATION`: Target host location in `~/.config/raft/incus.json`. Defaults to first configured location.
- `--ttl TTL`: Lifetime in seconds before automatic stop (`60` to `2592000`). Default: `600`.
- `--cpu {1,2}`: CPU core count managed by Incus. Default: `1`.
- `--memory {1GiB,2GiB,4GiB,8GiB}`: Memory limit. Default: `2GiB`.

- `--disposable`: Delete the box and snapshots on stop or expiry. Off by default.
- `--from LOCATION:BOX/SNAPSHOT`: Copy a named snapshot on its source host instead of the configured image. Normal creation sizing applies; the source can be running. Cannot target a different location.
- `--purpose TEXT`, `--owner TEXT`, `--next TEXT`: Record a [box note](#note). `--owner` and `--next` need `--purpose`. Without them the box starts with no note, including boxes created `--from` a noted snapshot.

Returns the qualified workspace handle. If the location has a note, `new` prints
it to stderr; stdout still contains only the handle.

### `stop`

Stop a running workspace.

`raft stop <box>`

Terminates guest processes and stops the container. Persistent boxes retain filesystem contents, packages and snapshots. Disposable boxes are deleted, including their snapshots.

### `resume`

Start a stopped workspace with a renewed lifetime deadline.

`raft resume <box> --ttl TTL [--cpu {1,2}] [--memory {1GiB,2GiB,4GiB,8GiB}]`

- `--ttl TTL`: Lifetime in seconds from resumption (`60` to `2592000`). Required.

CPU/RAM overrides on resume apply before startup. Omitted values stay unchanged.
A running box is rejected before changing its limits.

### `extend`

Reset the expiration deadline for a running workspace without restarting it.

`raft extend <box> --ttl TTL`

- `--ttl TTL`: Lifetime in seconds from the current host timestamp. Required.

### `destroy`

Permanently delete a workspace container and its snapshots.

`raft destroy <box>`

Stops the container if running, removes all associated Btrfs snapshots, and releases host storage.

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

Display container metadata from Incus in JSON format, including status, IP addresses, resource limits, and timestamps.

`raft info <box>`

### `usage`

Display active resource consumption for a running workspace: cgroup memory, CPU microseconds, CPU affinity, and shared-pool disk space.

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

`raft fork <box> --ttl TTL [--cpu {1,2}] [--memory {1GiB,2GiB,4GiB,8GiB}] [--disposable]`

- `--ttl TTL`: Lifetime in seconds for the clone. Required.

Omitted CPU/RAM values inherit the source limits. The clone is persistent unless `--disposable` is supplied, regardless of the source policy.

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

`raft recover <source> --location LOCATION`

- `<source>`: Path to archive file on controller.
- `--location LOCATION`: Target host location. Required.

Returns the qualified handle of the imported stopped workspace.

## Location commands

### `host add`

Check, provision and register a new workspace host, then prove it works.

`raft host add LOCATION --ssh TARGET [--image-release TAG | --image-archive PATH | --build] [--dry-run] [--yes]`

- `LOCATION`: New location name. Existing locations are rejected.
- `--ssh TARGET`: Key-based SSH target, such as `ubuntu@192.0.2.10` or an `~/.ssh/config` alias. Accept the host key interactively first.
- `--image-release TAG`: Download `raft-dev-<arch>.tar.gz` from this release on the host and verify it against the release `SHA256SUMS`. Default: the CLI's own version.
- `--image-archive PATH`: Upload and import a local image archive instead.
- `--build`: Build the image on the host from Debian 13 instead.
- `--dry-run`: Probe the host and print the plan without changing anything.
- `--yes`: Apply the plan without the interactive prompt. Without a terminal, `--yes` is required.

Before any change, a read-only probe checks SSH, passwordless sudo, Ubuntu 22.04 or
24.04, ARM64 or AMD64, python3, at least 2 CPUs, 8 GiB RAM and, for a fresh host,
80 GiB free disk. Unsupported targets, including macOS, fail with the reasons and
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
them in `locations`, `limits` and `new`.

Box notes tell other agents and people why a box exists. They follow one
template, stored on the box as JSON in the Incus key `user.raft.note`:

| Field | Meaning | Example |
| --- | --- | --- |
| `purpose` (required) | What the box is for | `Integration tests for PR 42` |
| `owner` | Who uses it | `codex session in ~/work/app` |
| `next` | What happens to it next | `Destroy after the tests pass` |

Each call replaces the whole note, on running or stopped boxes. `raft list`
shows it as `note`. Forks and recovered backups keep the source box's note;
update it if the copy has a different job.

Notes are advisory; Raft never enforces them. Empty values are rejected; use
`--clear` to remove a note.

## Host inspection and maintenance commands

### `limits`

Inspect host resource capacity, active allocations, and recommended workspace limits.

`raft limits [--location LOCATION] [--json]`

- `--location LOCATION`: Filter report to a specific host.
- `--json`: Output report as JSON.

Each report includes the location's `note`, if any.

### `doctor`

Inspect host connectivity, Incus version, expiry-worker compatibility, active systemd units (`incus`, `raft-expire.timer`, `raft-network.service`), root filesystem usage, storage pool status, and saved box count across configured hosts. An incompatible worker returns a nonzero exit status and names the failing location without running expiry or modifying boxes.

`raft doctor`

### `list`

List existing workspace containers across configured hosts.

`raft list [--location LOCATION]`

Prints one JSON object per box (JSONL), with `box`, `state`, `stopAt` and `note` fields.
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
