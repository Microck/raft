---
title: CLI command reference
description: Complete command-line reference for the Raft controller CLI.
---

# CLI command reference

Command-line reference for the `raft` controller tool.

Syntax:
```text
raft <subcommand> [options] [arguments]
```

Workspaces are referenced by location-qualified handles formatted as `<location>:<instance-name>` (for example `lab:rf-a1b2c3d4e5f60718`).

## Workspace lifecycle commands

### `new`

Create and launch a new workspace container.

```text
raft new [--location LOCATION] [--ttl TTL] [--cpu {1,2}] [--memory {1GiB,2GiB,4GiB}]
```

- `--location LOCATION`: Target host location in `~/.config/raft/incus.json`. Defaults to first configured location.
- `--ttl TTL`: Lifetime in seconds before automatic stop (`60` to `2592000`). Default: `600`.
- `--cpu {1,2}`: CPU core count managed by Incus. Default: `1`.
- `--memory {1GiB,2GiB,4GiB}`: Memory limit. Default: `2GiB`.

Returns the qualified workspace handle.

### `stop`

Stop a running workspace.

```text
raft stop <box>
```

Terminates guest processes and stops the container. Filesystem contents, packages, and snapshots are preserved on disk.

### `resume`

Start a stopped workspace with a renewed lifetime deadline.

```text
raft resume <box> --ttl TTL
```

- `--ttl TTL`: Lifetime in seconds from resumption (`60` to `2592000`). Required.

### `extend`

Reset the expiration deadline for a running workspace without restarting it.

```text
raft extend <box> --ttl TTL
```

- `--ttl TTL`: Lifetime in seconds from the current host timestamp. Required.

### `destroy`

Permanently delete a workspace container and its snapshots.

```text
raft destroy <box>
```

Stops the container if running, removes all associated Btrfs snapshots, and releases host storage.

## Execution and inspection commands

### `exec`

Execute a command inside a workspace.

```text
raft exec <box> [--detach] [--] <command ...>
```

- `--detach`: Run in background as a guest systemd unit. Returns a job ID immediately.
- `<command ...>`: Command and arguments passed to `execve`. For shell features (pipes, redirects, variables), wrap with `bash -lc '...'`.

Example:
```sh
job=$(raft exec "$box" --detach -- make test)
```

### `ssh`

Open an interactive root terminal session inside the workspace over host SSH and Incus exec.

```text
raft ssh <box>
```

### `info`

Display container metadata from Incus in JSON format, including status, IP addresses, resource limits, and timestamps.

```text
raft info <box>
```

### `usage`

Display active resource consumption for a running workspace: cgroup memory, CPU microseconds, CPU affinity, and shared-pool disk space.

```text
raft usage <box>
```

## Background job commands

### `status`

Check the execution state of a detached background job.

```text
raft status <box> <job>
```

Queries `systemctl show` inside the container and outputs `ActiveState`, `SubState`, and `ExecMainStatus`.

### `logs`

Retrieve journal output from a detached background job using `journalctl --no-pager -o cat -u <job>`.

```text
raft logs <box> <job>
```

### `cancel`

Terminate a running detached background job using `systemctl stop <job>`.

```text
raft cancel <box> <job>
```

## File transfer commands

### `upload`

Transfer a local file from the controller to an absolute path inside the workspace.

```text
raft upload <box> <source> <destination>
```

- `<source>`: Path to local file on controller.
- `<destination>`: Target absolute path inside container.

### `download`

Transfer a file from an absolute path inside the workspace to the local controller.

```text
raft download <box> <source> <destination>
```

- `<source>`: Absolute path to file inside container.
- `<destination>`: Local file destination path on controller.

## Snapshots and cloning commands

### `snapshot`

Create a named point-in-time snapshot of a stopped workspace. Names use alphanumeric characters, underscores, or hyphens up to 64 characters.

```text
raft snapshot <box> <name>
```

### `snapshots`

List existing snapshots and creation timestamps for a workspace.

```text
raft snapshots <box>
```

### `restore`

Roll back a stopped workspace filesystem to a named snapshot using `--diskonly`, preserving container configuration and MAC address.

```text
raft restore <box> <name>
```

### `fork`

Clone a stopped workspace into an independent container on the same host and start it.

```text
raft fork <box> --ttl TTL
```

- `--ttl TTL`: Lifetime in seconds for the clone. Required.

Returns the qualified handle of the clone.

## Port forwarding and desktop commands

### `forward`

Open a private SSH tunnel between a guest port and controller loopback (`127.0.0.1`).

```text
raft forward <box> --remote REMOTE --local LOCAL
```

- `--remote REMOTE`: Port number inside the workspace (bound to `0.0.0.0` or container IP).
- `--local LOCAL`: Port number on controller loopback (`127.0.0.1`).

Runs in foreground; press `Ctrl+C` to close.

### `desktop`

Start `raft-desktop.service` inside the container and open a private tunnel for noVNC.

```text
raft desktop <box> [--local LOCAL]
```

- `--local LOCAL`: Controller port to bind (default: `6080`). Access at `http://127.0.0.1:<LOCAL>/vnc.html`. Press `Ctrl+C` to close.

## Backup and recovery commands

### `backup`

Export a stopped workspace and its snapshots to a compressed archive on the controller.

```text
raft backup <box> <destination>
```

- `<destination>`: Output archive path (e.g. `./workspace.tar.gz`). Must not exist. Created with mode `0600`.

### `recover`

Import a portable backup archive onto a host as a fresh stopped workspace.

```text
raft recover <source> --location LOCATION
```

- `<source>`: Path to archive file on controller.
- `--location LOCATION`: Target host location. Required.

Returns the qualified handle of the imported stopped workspace.

## Host inspection and maintenance commands

### `limits`

Inspect host resource capacity, active allocations, and recommended workspace limits.

```text
raft limits [--location LOCATION] [--json]
```

- `--location LOCATION`: Filter report to a specific host.
- `--json`: Output report as JSON.

### `doctor`

Inspect host connectivity, Incus version, active systemd units (`incus`, `raft-expire.timer`, `raft-network.service`), root filesystem usage, storage pool status, and saved box count across configured hosts.

```sh
raft doctor
```

### `list`

List existing workspace containers across configured hosts.

```text
raft list [--location LOCATION]
```

### `gc`

Trigger the host expiration worker across all configured hosts immediately to stop expired containers.

```sh
raft gc
```
