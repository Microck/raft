---
title: CLI command reference
description: Complete command-line reference for the Raft controller CLI.
---

# CLI command reference

This document describes all subcommands, arguments, and options provided by the `raft` command-line tool.

Every command follows the format:
```text
raft <subcommand> [options] [arguments]
```

Workspaces are referenced by location-qualified handles in the format `<location>:<instance-name>`, such as `lab:rf-a1b2c3d4e5f60718`.

## Workspace lifecycle commands

### `new`

Create and launch a new workspace container.

```text
raft new [--location LOCATION] [--ttl TTL] [--cpu {1,2}] [--memory {1GiB,2GiB,4GiB}]
```

- `--location LOCATION`: Target host location defined in `~/.config/raft/incus.json`. Defaults to the first configured location.
- `--ttl TTL`: Initial lifetime in seconds before the container automatically stops. Accepts values from `60` to `2592000` (30 days). Defaults to `600` (10 minutes).
- `--cpu {1,2}`: CPU core count and affinity managed by Incus. Defaults to `1`.
- `--memory {1GiB,2GiB,4GiB}`: Memory limit for the container. Defaults to `2GiB`.

Returns the qualified workspace handle (for example `lab:rf-a1b2c3d4e5f60718`).

### `stop`

Stop a running workspace.

```text
raft stop <box>
```

Terminates all guest processes and stops the container. Filesystem contents, installed packages, and snapshots are preserved on disk. Does not delete the container.

### `resume`

Start a previously stopped workspace with a renewed lifetime deadline.

```text
raft resume <box> --ttl TTL
```

- `--ttl TTL`: Lifetime in seconds from the time of resumption. Required. Accepts values from `60` to `2592000`.

### `extend`

Reset the expiration deadline for a currently running workspace without restarting it.

```text
raft extend <box> --ttl TTL
```

- `--ttl TTL`: New lifetime in seconds from the current host timestamp. Required.

### `destroy`

Permanently delete a workspace container and its snapshots.

```text
raft destroy <box>
```

Stops the container if currently running, deletes all associated Btrfs snapshots, and releases storage on the host.

## Execution and inspection commands

### `exec`

Execute a command inside a workspace.

```text
raft exec <box> [--detach] [--] <command ...>
```

- `--detach`: Run the command in the background as a guest systemd unit. Returns a job identifier immediately.
- `<command ...>`: The command and its arguments. Arguments following `--` are passed directly to `execve`. For shell syntax (pipes, redirects, environment variables), wrap with `bash -lc '...'`.

Example running a detached job:
```sh
job=$(raft exec "$box" --detach -- make test)
```

### `ssh`

Open an interactive root terminal session inside the workspace.

```text
raft ssh <box>
```

Allocates a pseudo-terminal (PTY) over host SSH and Incus exec. Does not expose a guest SSH port.

### `info`

Display detailed metadata for a workspace.

```text
raft info <box>
```

Outputs JSON-formatted container metadata from Incus, including status, IP addresses, CPU and memory limits, and creation timestamps.

### `usage`

Display active resource consumption for a running workspace.

```text
raft usage <box>
```

Reports cgroup memory consumption, cumulative CPU microseconds, effective CPU affinity, and shared-pool disk space. Requires a running container.

## Background job commands

### `status`

Check the execution state of a detached background job.

```text
raft status <box> <job>
```

Queries `systemctl show` inside the container and outputs `ActiveState`, `SubState`, and `ExecMainStatus`. Does not report PID or process start times.

### `logs`

Retrieve standard output and standard error from a detached background job.

```text
raft logs <box> <job>
```

Prints recorded journal output from the guest systemd unit using `journalctl --no-pager -o cat -u <job>`. Outputs existing records without a pager and without streaming or follow mode.

### `cancel`

Terminate a running detached background job.

```text
raft cancel <box> <job>
```

Calls `systemctl stop <job>`, terminating processes within the job's systemd control group.

## File transfer commands

### `upload`

Transfer a local file from the controller to a path inside the workspace.

```text
raft upload <box> <source> <destination>
```

- `<source>`: Path to local file on controller.
- `<destination>`: Target absolute path inside container.

### `download`

Transfer a file from the workspace to the local controller.

```text
raft download <box> <source> <destination>
```

- `<source>`: Absolute path to file inside container.
- `<destination>`: Local file destination path on controller.

## Snapshots and cloning commands

### `snapshot`

Create a named point-in-time snapshot of a stopped workspace.

```text
raft snapshot <box> <name>
```

The workspace must be stopped before creating a snapshot. Snapshot names must use letters, digits, underscores, or hyphens up to 64 characters.

### `snapshots`

List all existing snapshots for a workspace.

```text
raft snapshots <box>
```

### `restore`

Roll back a stopped workspace filesystem to a named snapshot.

```text
raft restore <box> <name>
```

Replaces disk contents with snapshot state while preserving the container instance configuration and network MAC address.

### `fork`

Clone a stopped workspace into an independent container on the same host.

```text
raft fork <box> --ttl TTL
```

- `--ttl TTL`: Lifetime in seconds for the newly created clone. Required.

Returns the qualified handle of the newly created clone.

## Port forwarding and desktop commands

### `forward`

Open a private SSH tunnel between the controller and a service inside the workspace.

```text
raft forward <box> --remote REMOTE --local LOCAL
```

- `--remote REMOTE`: Port number inside the workspace. The service must be listening on the container's network interface or `0.0.0.0`.
- `--local LOCAL`: Port number on the controller loopback interface (`127.0.0.1`).

Runs in the foreground. Press `Ctrl+C` to close the tunnel.

### `desktop`

Launch the graphical desktop stack inside the container and open a private tunnel.

```text
raft desktop <box> [--local LOCAL]
```

- `--local LOCAL`: Controller port to bind for the noVNC client. Defaults to `6080`.

Access the desktop in a local browser at `http://127.0.0.1:<LOCAL>/vnc.html`. Press `Ctrl+C` to disconnect.

## Backup and recovery commands

### `backup`

Export a stopped workspace and all its snapshots into a compressed archive.

```text
raft backup <box> <destination>
```

- `<destination>`: Path to output archive (e.g. `./workspace.tar.gz`). Must not already exist. Created with mode `0600`.

### `recover`

Import a portable backup archive onto a host as a fresh stopped workspace.

```text
raft recover <source> --location LOCATION
```

- `<source>`: Path to local archive file on controller.
- `--location LOCATION`: Target host location where the archive will be imported. Required.

Returns the qualified handle of the imported stopped workspace.

## Host inspection and maintenance commands

### `limits`

Inspect host resource capacity, active allocations, and recommended workspace limits.

```text
raft limits [--location LOCATION] [--json]
```

- `--location LOCATION`: Filter report to a specific host. If omitted, reports all configured locations.
- `--json`: Output results as JSON.

### `doctor`

Inspect host connectivity, Incus project status, active systemd units, and storage pools.

```sh
raft doctor
```

Iterates over all configured locations and outputs Incus version, active state of host systemd units (`incus`, `raft-expire.timer`, `raft-network.service`), root filesystem usage (`df -h /`), `raft-data` pool information, and saved box count.

### `list`

List existing workspace containers across configured hosts.

```text
raft list [--location LOCATION]
```

Lists workspaces across all configured locations unless `--location` is specified.

### `gc`

Trigger the host expiration worker immediately.

```sh
raft gc
```

Runs the expiration worker across all configured locations. Checks running workspace lifetimes and transitions expired containers to stopped state.
