---
title: Look up a command
description: Parser-derived command syntax and the limits you need before running it.
---

# Look up a command

Use `raft [--json] COMMAND ...`. `raft --help`, command `--help` and `raft --version` work without host configuration. The syntax below comes from the CLI parser; use installed help when running another version.

Global `--json` must precede the command. It emits JSONL data, byte-stream, error and exit events, but rejects interactive `ssh`, `forward` and `desktop`. Without it, output is command-specific. See [scripting and exit codes](../guides/scripting-ci.mdx).

## Check shared arguments

- `box` is the exact location-qualified handle returned by creation, not a native Incus name. Inspect `raft list` and its ownership note first.
- TTL is seconds, 60 to 2,592,000. It is required on resume, extend and fork. `run` uses `--timeout` instead.
- `--cpu` takes a positive whole number. `--memory`, `--disk` and `--disk-pool` use whole GiB, such as `4GiB`. `--disk` also accepts `shared` for image-based creation. See [sizing](./capacity.mdx).
- `--purpose`, `--owner` and `--next` are nonempty note fields. Supplying owner or next requires purpose; a host can require more fields.
- `--location` defaults to all configured hosts for reports/maintenance, but selects one host for creation. `--from` fixes creation to its snapshot's host. See [configuration](./configuration.mdx).
- Tunnel ports must be 1 to 65535. Recursive transfers require running guests and absent destinations with existing parents.

## Choose a command

### `new`

`raft new [-h] [--location LOCATION] [--ttl TTL] [--cpu CPU] [--memory MEMORY] [--disk SIZE] [--disposable | --persistent] [--from LOCATION:BOX/SNAPSHOT] [--purpose PURPOSE] [--owner OWNER] [--next NEXT]`

Create and start a box; stdout is its exact `LOCATION:rf-` handle with 16 lowercase hex digits. Guest services may still be starting. Explicit flags override controller defaults; image-based built-ins are first location, 600 seconds, 1 CPU, 2GiB, shared disk and persistent. `--from` copies a named same-host snapshot, keeps its sizes unless overridden, and never inherits its note. Its source may run. `--disk shared` is rejected with `--from`. See [defaults](./configuration.mdx#defaults-for-new).

### `run`

`raft run [-h] [--location LOCATION] [--cpu CPU] [--memory MEMORY] [--disk SIZE] [--from LOCATION:BOX/SNAPSHOT] [--purpose PURPOSE] [--owner OWNER] [--next NEXT] [--timeout TIMEOUT] [--push DIRECTORY | --push-all DIRECTORY] [--cwd CWD] [--pull GUEST_DIRECTORY NEW_LOCAL_DIRECTORY] -- COMMAND [ARG ...]`

Create a disposable box, wait for command readiness, optionally upload, execute, collect directories and clean up. `--timeout` is 1 to 2,591,880 seconds, default 600, for all setup/upload/execution. Collection and cleanup each get 30 seconds. `--push` selects Git-tracked/unignored files without `.git` and rejects submodules; `--push-all` copies the full inspected tree. `--cwd` defaults to `/workspace/project` with upload or `/workspace` without it. Upload destinations must be new with an existing parent. Repeat `--pull` to collect directories into absent local destinations with existing parents, even after payload failure. See [CI limits and exit codes](../guides/ci-jobs.mdx).

### `locations`

`raft locations [-h]`

Print JSONL `location`, `default` and `note` from controller inventory without contacting hosts. `default` marks the first inventory entry, not a `config.json` override.

### `config`

`raft config [-h] [--location LOCATION]`
`raft config set [-h] {location,ttl,cpu,memory,disk,disposable} value`
`raft config unset [-h] {location,ttl,cpu,memory,disk,disposable}`

Print effective controller defaults with each value's source and host policies. `--location` filters only the policy report; it is rejected with `set` or `unset`. Keys are `location`, `ttl`, `cpu`, `memory`, `disk`, `disposable`; use `true` or `false` for the last. `unset` restores the built-in default. See [configuration](./configuration.mdx).

### `policy`

`raft policy [-h] [--require FIELDS|none] [--delete-stopped-after HOURS|never] location`

Set either or both host policies. `--require` takes comma-separated `purpose`, `owner`, `next`, including `purpose`, or `none`. `--delete-stopped-after` takes positive whole hours or `never`. Retention permanently deletes stopped boxes and snapshots after the later of stop or policy change plus those hours. See [policies and cleanup](../guides/config-policy.mdx).

### `note`

`raft note [-h] [--clear] [--purpose PURPOSE] [--owner OWNER] [--next NEXT] LOCATION|BOX [text]`

For a location, give free-text guidance or `--clear`. For a box, give `--purpose` with optional `--owner` and `--next`, or `--clear`. Each call replaces the entire note; empty values and missing host-required fields fail. Fork and recovery inherit source notes unless replaced; `new` does not.

### host add

`raft host add [-h] --ssh SSH [--image-release TAG | --image-archive PATH | --build] [--dry-run] [--yes] [--disk-pool SIZE] location`

Onboard a dedicated Ubuntu host. `--ssh` requires key-based access and passwordless sudo. The default image release matches your CLI; image options are mutually exclusive. `--dry-run` checks and prints the plan without changes; `--yes` applies without prompting and is required without a terminal. `--disk-pool` opts into own disks in whole GiB. Deployment files must come from npm or a source checkout, not the CLI-only wheel. Failed final checks unregister the location but leave host changes. See [setup](../operations.mdx).

### `list`

`raft list [-h] [--location LOCATION]`

Print one JSON object per box with `box`, `state`, `stopAt`, `deleteAt`, `disk` and `note`. Empty inventories emit no records; deadline fields are Unix-second strings or `""`. Read ownership notes before using a box.

### `capacity`

`raft capacity [-h] [--location LOCATION] [--json]`

Print advisory host estimates; local `--json` emits one JSON object per host rather than text. No box-count or aggregate CPU/RAM budget is enforced. Oversized individual requests and own-disk reservations are checked separately. See [capacity fields](./capacity.mdx).

### `doctor`

`raft doctor [-h] [--location LOCATION]`

Check host connectivity, Incus, worker compatibility, services, storage and saved boxes. Missing optional own disks is healthy. Does not validate image fingerprints or firewall rules. A mismatched worker fails with deployment guidance.

### `gc`

`raft gc [-h] [--location LOCATION]`

Enforce due expiry and retention immediately on selected hosts. Persistent boxes stop; disposable and retention-due boxes and snapshots are deleted. Inspect deadlines before invoking it.

### `prune`

`raft prune [-h] [--location LOCATION] --older-than DAYS [--yes]`

Preview JSONL candidates for deletion of persistent stopped boxes. `--older-than` requires at least 1 day; `--yes` permanently deletes matches and snapshots. Running boxes and boxes without a recorded stop time are excluded. Requires a compatible expiry worker.

### `recover`

`raft recover [-h] --location LOCATION [--purpose PURPOSE] [--owner OWNER] [--next NEXT] source`

Import a trusted backup as a fresh persistent, stopped box and print its handle. Target CPU architecture must match. Own-disk backups retain disk size and need reservation space for every snapshot too. Note flags replace the archived note; an inherited note rejected after import causes the imported box to be deleted. See [backup safety](../guides/backup-recovery.mdx).

### `self-update`

`raft self-update [-h] [--check | --version X.Y.Z]`

Update through the running copy's npm or uv tool channel. `--check` installs nothing and returns 100 if a release is available. `--version` chooses a release, including a downgrade; otherwise updates never downgrade. Unmanaged/source installations are refused. Hosts and image pins are unchanged. See [upgrades](../guides/upgrades.mdx).

### `info`

`raft info [-h] box`

Print native Incus metadata as JSON without starting guest processes. Inspect disk configuration in `expanded_devices.root`.

### `stop`

`raft stop [-h] box`

End guest processes. Persistent boxes retain disk and snapshots; disposable boxes and snapshots are permanently deleted. Inspect `info` before stopping.

### `resume`

`raft resume [-h] --ttl TTL [--cpu CPU] [--memory MEMORY] box`

Require a stopped box, start it and set a new host-clock lifetime. Optional CPU/RAM flags replace only those limits before startup. Disk and process memory are not resized or restored.

### `extend`

`raft extend [-h] --ttl TTL box`

Require a running box and reset its deadline to host time plus TTL, not the previous deadline plus TTL. Does not restart processes.

### `destroy`

`raft destroy [-h] box`

Permanently delete the box and all snapshots, then confirm absence. Copy needed data first; target only boxes you own or are authorized to delete.

### `ssh`

`raft ssh [-h] box`

Open an interactive guest-root shell in `/workspace` through host SSH. Does not expose a guest SSH port. Exit the shell to disconnect.

### `exec`

`raft exec [-h] [--detach] box command [command ...]`

Run literal arguments as guest root in `/workspace`, inherit caller stdin and preserve the payload status when transport succeeds. Use `bash -lc` for shell syntax. `--detach` returns a job ID for `status`, `logs` and `cancel` rather than a payload result.

### `sync`

`raft sync [-h] [--all] [--dry-run] box source destination`

Update source in a running box, replacing matches but retaining guest-only files. Requires rsync on controller and guest. Default Git selection excludes `.git` and rejects submodules; `--all` includes ignored/hidden files. Destination must be absolute, non-root and have an existing parent. `--dry-run` previews without writing. Unsafe links/special files are skipped; updates are atomic per file. See [sync](../guides/files.mdx#sync-source-edits).

### `upload`

`raft upload [-h] [--recursive] box source destination`

Copy a local file to an absolute guest filename, replacing an existing file. Parent directories must exist. `--recursive` copies a directory into a new destination instead; see [directory limits](../guides/files.mdx#copy-a-directory).

### `download`

`raft download [-h] [--recursive] box source destination`

Copy an absolute guest filename to a local filename; failure leaves an existing local file intact. `--recursive` copies a directory into a new destination with an existing parent. Individual files work while stopped; recursive transfers require a running box and guest Python 3.11.8+.

### `snapshot`

`raft snapshot [-h] box name`

Save disk state of a stopped persistent box. Names start with a letter/digit, then ASCII letters, digits, `_` or `-`, up to 64 characters. Own-disk snapshots reserve the full disk size.

### `snapshots`

`raft snapshots [-h] box`

Print native Incus information, including snapshot names and creation times.

### `snapshot-delete`

`raft snapshot-delete [-h] box name`

Permanently delete one named snapshot without deleting its box. The box may be running. Snapshot-name rules match `snapshot`.

### `restore`

`raft restore [-h] box name`

Replace a stopped box's files from a named snapshot while retaining instance configuration, limits and MAC address. This discards intervening disk changes; back up needed files first.

### `fork`

`raft fork [-h] --ttl TTL [--cpu CPU] [--memory MEMORY] [--disposable] [--purpose PURPOSE] [--owner OWNER] [--next NEXT] box`

Copy a stopped box on the same host, without its snapshots, and start the copy with a fresh MAC and deadline. Omitted CPU/RAM and the disk inherit from the source; disk cannot be overridden. Persistent unless `--disposable`, regardless of controller defaults. Note flags replace the whole inherited note.

### `forward`

`raft forward [-h] --remote REMOTE --local LOCAL box`

Open a foreground SSH tunnel bound to controller `127.0.0.1`. The guest service must listen on its managed address or `0.0.0.0`. Waits up to 30 seconds for managed IPv4, not service readiness. Ctrl+C closes the tunnel, not the service.

### `desktop`

`raft desktop [-h] [--local LOCAL] box`

Start `raft-desktop.service`, then open a foreground private tunnel. `--local` defaults to 6080; open `http://127.0.0.1:6080/vnc.html`. Does not poll HTTP readiness. Closing the tunnel leaves the desktop running. See [desktop access](../guides/desktop-services.mdx).

### `logs`

`raft logs [-h] box job`

Print the first 1 MiB of retained combined output, even after box deletion. Results expire seven days after completion; keep the exact original handle and job ID.

### `status`

`raft status [-h] box job`

Print retained JSON job state, `exitCode`, timestamps and `droppedBytes`. A successful query does not prove payload success; `completed` with exit code zero does. See [job states](../guides/background-jobs.mdx).

### `cancel`

`raft cancel [-h] box job`

Stop the guest job and child processes. Retained output remains. Cancellation can fail during startup or time out after 120 seconds; inspect state before retrying.

### `backup`

`raft backup [-h] box destination`

Export a stopped persistent box and snapshots to a new private gzip archive. Includes credentials. Parent must exist and support private permissions and hardlinks. Large exports delay host expiry checks. See [backup requirements](../guides/backup-recovery.mdx).

### `usage`

`raft usage [-h] box`

Print running guest CPU/memory counters and filesystem space as JSON. `filesystemScope` distinguishes shared-pool space from the box's own disk; shared counters are not per-box usage.

### `completions`

`raft completions [-h] {bash,zsh,fish}`

Print setup for bash, zsh or fish without host access. In Bash, use `source <(raft completions bash)`. The private `_complete` command serves these scripts, not ordinary workflows.
