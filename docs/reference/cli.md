---
title: CLI command reference
description: Complete command-line reference for the Raft controller CLI.
---

# CLI command reference

Command-line reference for the `raft` controller tool.

Syntax:
`raft [--json] <subcommand> [options] [arguments]`

`raft --help` lists commands, `raft <subcommand> --help` explains its behavior and arguments,
and `raft --version` prints the installed version without loading configuration.
Global `--json` emits JSONL events for structured results, byte streams, errors and exit codes.
Without it, output is command-specific; see [Scripting and CI](../guides/scripting-ci.mdx).

At most once a day, Raft checks GitHub for a newer release in the background
while a command runs, then prints one line on stderr after it finishes, such as
`raft 0.7.0 is available (you have 0.6.0). Run: raft self-update`. It never
installs anything or changes stdout, and network failures stay silent. See
[`self-update`](#self-update) to turn it off.

Workspaces are referenced by location-qualified handles formatted as `<location>:<instance-name>` (for example `lab:rf-a1b2c3d4e5f60718`).

## Command index

| Task | Commands |
| --- | --- |
| Workspace lifecycle | [`new`](#new), [`stop`](#stop), [`resume`](#resume), [`extend`](#extend), [`destroy`](#destroy) |
| Disposable CI jobs | [`run`](#run) |
| Execution and inspection | [`exec`](#exec), [`ssh`](#ssh), [`info`](#info), [`usage`](#usage) |
| Background job | [`status`](#status), [`logs`](#logs), [`cancel`](#cancel) |
| File transfer | [`upload`](#upload), [`download`](#download), [`sync`](#sync) |
| Snapshots and cloning | [`snapshot`](#snapshot), [`snapshots`](#snapshots), [`restore`](#restore), [`snapshot-delete`](#snapshot-delete), [`fork`](#fork) |
| Port forwarding and desktop | [`forward`](#forward), [`desktop`](#desktop) |
| Backup and recovery | [`backup`](#backup), [`recover`](#recover) |
| Locations | [`host add`](#host-add), [`locations`](#locations), [`note`](#note) |
| Defaults and policies | [`config`](#config), [`policy`](#policy) |
| Host inspection and maintenance | [`capacity`](#capacity), [`doctor`](#doctor), [`list`](#list), [`gc`](#gc), [`prune`](#prune) |
| CLI maintenance | [`self-update`](#self-update) |

## Disposable CI command

### `run`

**Unreleased 0.7.0 feature.** Not present in published 0.6.0. See the
[development requirements](../guides/ci-jobs.mdx) before using these commands.

`raft run [--location LOCATION | --from LOCATION:BOX/SNAPSHOT] [--cpu N] [--memory SIZE] [--disk SIZE] [--timeout SECONDS] [--push DIRECTORY | --push-all DIRECTORY] [--cwd GUEST_PATH] [--pull GUEST_DIRECTORY NEW_LOCAL_DIRECTORY]... [--purpose TEXT [--owner TEXT] [--next TEXT]] -- COMMAND [ARG ...]`

Create a disposable box, probe readiness, optionally upload source, stream the
command, collect result directories and attempt ownership-checked cleanup.
`--timeout` defaults to 600 and covers the entire job, with separate 30-second
collection and cleanup grace periods. The host expiry is established before
start at timeout + 120 seconds. No renewal, detach, persistent or keep option.

`--push` requires a Git working tree and copies tracked/unignored files without
`.git`; submodules are refused. `--push-all` explicitly copies the full tree,
including ignored files. Uploads require a new guest destination with an existing
parent, defaulting to `/workspace/project`. Without upload, cwd is `/workspace`.
Pulls are directory transfers to new local paths and run after a failed payload.

Payload failure status takes precedence; a successful payload with failed
collection/cleanup returns 1. Timeout is 124, Ctrl-C 130 and SIGTERM 143.
Lifecycle messages and the allocated handle go to stderr, not stdout. Unknown
remote outcomes are not automatically retried. See the [CI guide](../guides/ci-jobs.mdx)
for trust restrictions, template caching, recovery limits and a GitHub example.

## Workspace lifecycle commands

### `new`

Create and launch a new workspace container.

`raft new [--location LOCATION] [--ttl TTL] [--cpu N] [--memory SIZE] [--disk SIZE] [--disposable | --persistent] [--from LOCATION:BOX/SNAPSHOT] [--purpose TEXT [--owner TEXT] [--next TEXT]]`

- `--location LOCATION`: Target host location in `~/.config/raft/incus.json`. Built-in default: first configured location.
- `--ttl TTL`: Lifetime in seconds before automatic stop (`60` to `2592000`). Built-in default: `600`.
- `--cpu N`: CPU limit, any whole number from `1` up to the host's CPU count. Built-in default: `1`, or the snapshot's with `--from`.
- `--memory SIZE`: Memory limit in whole GiB, for example `16GiB`, up to the host's RAM. Built-in default: `2GiB`, or the snapshot's with `--from`.
- `--disk SIZE`: Give the box its own disk of SIZE in whole GiB, for example `20GiB`, instead of the shared pool. Needs a host deployed with `--disk-pool`. The box, and Docker inside it, cannot write past SIZE, and the pool reserves SIZE when the box is created. `--disk shared` asks for the shared pool, overriding a configured `disk`. Built-in default: the shared pool, or with `--from` the snapshot's disk. See [Box sizing and own disks](./capacity.mdx#own-box-disks).

An omitted option takes the controller default set with [`config`](#config),
then the built-in default. With `--from`, configured `cpu`, `memory` and `disk`
defaults do not apply: the box keeps the snapshot's size unless a flag overrides
it, and `--disk shared` is refused.

- `--disposable`: Delete the box and snapshots on stop or expiry. Built-in default: persistent.
- `--persistent`: Keep the box and its disk on stop or expiry, overriding a configured `disposable` default. Cannot be combined with `--disposable`.
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

`status`, `logs` and `cancel` require the exact workspace handle and job ID returned by `exec --detach`. Status and logs remain available after workspace stop or deletion. Unknown or expired jobs return an error. Guest journal rotation does not affect retained results.

### `status`

`raft status <box> <job>`

Read retained JSON state, exit code, timestamps and dropped output bytes from the
host, including after workspace stop or deletion. `completed` means execution
ended; inspect `exitCode` for success. Unknown or expired jobs are rejected.

### `logs`

`raft logs <box> <job>`

Read the first 1 MiB of combined output retained on the host. Results expire
seven days after completion and are pruned on access. The store allows at most
512 records. See [Background jobs](../guides/background-jobs.mdx).

### `cancel`

`raft cancel <box> <job>`

Stop the guest job control group. Retained results remain available. Cancellation
can fail while the guest unit is still starting; inspect before retrying.

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

Omitted CPU/RAM values inherit the source limits; controller defaults never apply to forks. The clone keeps the source's disk: the shared pool, or an own disk of the same size as a thin snapshot, which reserves that size in `raft-disks`. To copy a box onto a disk of another size, snapshot it and use `new --from BOX/SNAPSHOT --disk SIZE`. The clone is persistent unless `--disposable` is supplied, regardless of the source and of a configured `disposable` default.
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

`raft config [--location LOCATION]`
`raft config set KEY VALUE`
`raft config unset KEY`

- `KEY`: One of `location`, `ttl`, `cpu`, `memory`, `disk`, `disposable`.
- `VALUE`: Accepted exactly when the same text is accepted by the matching `new` flag. A `location` must be configured. `disposable` takes `true` or `false`.

`disposable true` makes `new` create disposable boxes, including `new --from`,
because a snapshot carries no lifecycle; `--persistent` keeps one box. `fork`
ignores it: a fork is persistent unless it gets `--disposable`.

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
`source`, `config` or `built-in`) and `policy`. For each location, `policy` has
`require` (the required note fields), `deleteStoppedAfterHours` (`null` without
retention), `deleteStoppedAfterSince` (when retention was set, Unix seconds) and
the last change records `requireChanged` and `deleteStoppedAfterChanged`. It
contacts every configured host to read policies unless `--location LOCATION`
selects one. This filter affects the policy report, not controller defaults,
and is rejected with `set` or `unset`. Location-scoped config and doctor are
development-branch additions, not available in published 0.6.0.
See [Defaults and host policies](../guides/config-policy.mdx).

### `policy`

Set or clear a location's host policies: the box note fields it requires and
how long it keeps stopped boxes.

`raft policy LOCATION [--require FIELDS|none] [--delete-stopped-after HOURS|never]`

- `--require FIELDS`: Comma-separated fields from `purpose`, `owner`, `next`. Must include `purpose`. `--require none` stops requiring fields.
- `--delete-stopped-after HOURS`: Delete boxes that stay stopped for HOURS, a positive whole number, with their snapshots. `--delete-stopped-after never` keeps stopped boxes.

Give either option or both; each changes only its own policy. Both values are
checked before the host changes.

Policies are stored on the host in the Incus project `raft`, so they apply to
every controller. The command prints each change and the host records the time,
SSH account and controller in a `.changed` key beside the policy and in the host
journal (`journalctl -t raft`).

The note policy lives in `user.raft.require`. `new`, `fork`, `recover` and
`note` check it on the controller for an actionable error, then again in the
host transaction that writes the note.

Retention lives in `user.raft.delete-stopped-after`, with the time it was set in
`user.raft.delete-stopped-after.since`. The host expiry worker deletes a stopped
box once HOURS have passed since the later of its stop and that time, so a new
or shorter policy never deletes a box sooner than HOURS after the change. The
worker runs every 15 seconds and on [`gc`](#gc), logs each deletion to the host
journal and never touches running boxes. A stopped box with no recorded stop
time, such as one powered off from inside, stopped with native `incus` or
recovered from a backup, starts its clock when the worker first sees it.
[`list`](#list) shows each box's `deleteAt`. Setting retention needs the current
host worker; on an older one it fails with redeploy guidance.

Policies are guardrails for agents, not a security boundary: SSH and sudo on the
host can change or bypass them, and older controllers do not check note policies.

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

`raft doctor [--location LOCATION]`

Pass `--location` to inspect one configured host independently of offline hosts.

### `list`

List existing workspace containers across configured hosts.

`raft list [--location LOCATION]`

Prints one JSON object per box (JSONL), with `box`, `state`, `stopAt`, `deleteAt`, `disk` and `note` fields.
`deleteAt` is the Unix time when the location's [retention](#policy) deletes a
stopped box, as a string like `stopAt`, or `""` when no deletion is due.
`disk` is `"shared"` for the shared pool or the own disk's size, such as `"20GiB"`.
`note` is the box note object, or `null` when the box has none.
An empty inventory prints no records. See [Scripting and CI](../guides/scripting-ci.mdx)
for output formats, exit codes, readiness, and failure handling.

### `gc`

Enforce deadlines and retention immediately. Persistent boxes stop; disposable boxes are deleted; stopped boxes past the location's [retention](#policy) are deleted.

`raft gc [--location LOCATION]`

### `prune`

Preview deletion of persistent stopped boxes older than the selected number of days.

`raft prune --older-than DAYS [--location LOCATION] [--yes]`

Days must be at least one. Without `--yes`, this only prints JSONL candidates. With `--yes`, it deletes matching boxes and their snapshots under the host lock. Running boxes and boxes without a recorded stop time are excluded. Stop timestamps come from Raft stop/expiry, or from the host worker when it first sees a box stopped another way, never from box creation. For automatic deletion, set a [retention policy](#policy).

Disposable creation/fork, prune and setting retention require the current expiry worker installed through the deployer. A mismatch fails with deployment guidance; the CLI does not install or adapt an older worker.

## CLI maintenance commands

### `self-update`

Upgrade the installed CLI to the latest GitHub release, or to a chosen one.

`raft self-update [--check | --version X.Y.Z]`

- `--check`: Report whether a newer release exists without installing it. Exits `0` when the CLI is current and `100` when an update is available.
- `--version X.Y.Z`: Install this release instead of the latest, for example to roll back. Without it, `self-update` never downgrades.

The latest release on GitHub decides what is current. `self-update` installs
through the channel that installed the running copy and never switches channel:

| Installed with | Detected by | Upgrade | Integrity |
| --- | --- | --- | --- |
| `npm install -g @microck/raft` | `PREFIX/lib/node_modules/@microck/raft`, linked from `PREFIX/bin/raft` | `npm install --global --prefix PREFIX @microck/raft@X.Y.Z` | npm checks the registry integrity hash; Raft publishes with npm provenance |
| `uv tool install` of a release wheel | A uv tool environment named `raft` | Downloads the wheel and `SHA256SUMS` from the release, then `uv tool install --force` | Refuses a wheel whose SHA-256 differs from the release `SHA256SUMS` |

Source checkouts, editable installs, pip and other layouts are refused with
instructions. For a uv tool, `uv tool dir` must still name the original tool
directory; set `UV_TOOL_DIR` if it moved.

`self-update` prints `raft OLD -> NEW` and the release notes link. A current CLI
is a no-op. Hosts are not changed: run [`doctor`](#doctor) next. If a host's
worker differs from the new CLI, redeploy that host from the same release; see
[Upgrades and image changes](../guides/upgrades.mdx). After an update,
[`host add`](#host-add) downloads the new release's image by default. Existing
image pins stay unchanged.

Set `RAFT_NO_UPDATE_CHECK=1` to turn off the daily release notice, for example in
pinned or scripted runs. The notice caches its last result in
`$XDG_CACHE_HOME/raft/update.json` (default `~/.cache/raft/update.json`). It
waits at most 2 seconds for GitHub and is skipped for `self-update` and
`--version`.

### `completions`

`raft completions bash|zsh|fish`

Print a shell integration script. Source it in the matching shell, for example
`source <(raft completions bash)`. Completions discover the installed parser on
each request, including nested commands, flags and fixed choices. They never
load host configuration or contact a host. The private `_complete` command is
used by these scripts, not needed in ordinary workflows.

### `sync`

`raft sync BOX SOURCE DESTINATION [--all] [--dry-run]`

Sync source into an existing running workspace, replacing matching files and
preserving guest-only files. Checksums detect content changes even if timestamps
and sizes match; unchanged content is not resent. Requires rsync on the
controller and guest. Install it explicitly if an older image lacks it.

Git selects tracked and unignored files by default, excluding `.git` and rejecting
submodules. `--all` includes ignored and hidden files too. Deleted source files
are not deleted remotely. Rsync reports skipped unsafe symlinks and special files.
Destination is an absolute non-root guest directory with an existing parent.
`--dry-run` previews changes without writing files. Updates are atomic per file,
not per tree; rerun after an interruption to finish syncing.

```sh
raft sync "$BOX" . /workspace/project --dry-run
raft sync "$BOX" . /workspace/project
```
