---
name: raft-cli
description: Create and use private persistent Linux workspaces on configured Linux hosts with Raft and Incus. Use for self-hosted builds, tests, terminals, Docker, file transfer, snapshots, and private desktop access. Hosted Boat uses its separate boat-cli skill.
---

# Raft

Raft runs unprivileged native ARM64 or AMD64 Incus system containers. They share the host kernel.
The current deployment does not implement Incus VMs. Do not describe these boxes as VMs or promise cross-architecture emulation.

Run `raft doctor` and `raft list` before allocating. Keep the exact
location-qualified handle returned by `new`. There is no implicit current box.

```sh
box=$(raft new --location lab --ttl 600)
raft exec "$box" -- timeout 240 bash -lc 'git clone <url> /workspace/project && cd /workspace/project && <test-command>'
raft download "$box" /workspace/project/report.json ./report.json
raft destroy "$box"
```

The default development image has systemd, a guest Docker daemon, Node/npm/pnpm,
Bun, Deno, Python/pip/venv/uv, Go, Rust, Java, Kotlin, Scala, Ruby, PHP,
Erlang/Elixir, .NET, R, C/C++ tools, Git, Chromium and FFmpeg. Coding agents are not included in new images; install
your preferred agent when needed and authenticate separately. Never copy host
credentials into a box without authorization. Inspect `/opt/raft/` inside the
box for recorded package versions. Do not assume every language has every
version manager or that agents have an authenticated account.

Exec passes literal arguments; use `bash -lc` for shell syntax. A failed command
retains its exit status. `raft ssh "$box"` opens a root terminal through native
Incus execution over verified host SSH. It does not expose a guest SSH port.

Defaults are the first configured location, one CPU, 2 GiB RAM and 600 seconds. TTL accepts 60 seconds
to 30 days. The controller admits at most four saved boxes per host; stopped
boxes count because their disk still occupies space. Use `--memory 4GiB`
and `--cpu 2` when needed. Btrfs bounds workspace storage to a 60 GiB pool per host.
It does not guarantee strict per-box disk quotas with nested subvolumes. Check host disk before large builds or forks.

## Persistence and cleanup

Expiry stops execution and retains disk. `raft stop "$box"` releases the
container's running processes; `raft resume "$box" --ttl 600` starts it again.
Stop is not pause and does not keep guest RAM allocated. Installed dependencies,
files and secrets remain on disk. For a temporary run, completion requires
successful `destroy`, even after command failure. Stop reusable runners when
finished. Destroy only boxes owned by the current task unless authorized otherwise.

Host systemd timers enforce deadlines every 15 seconds, even when the controller
is offline. `raft gc` runs those expiry workers manually; it does not delete saved
boxes. A powered-off host enforces expiry when it returns.

Stop a box before a consistent disk snapshot or fork:

```sh
raft stop "$box"
raft snapshot "$box" prepared
raft snapshots "$box"
copy=$(raft fork "$box" --ttl 600)
raft restore "$box" prepared
```

Restore replaces disk contents with the snapshot and retains the destination's
current instance configuration, including its MAC and resource limits. Use it
only when the user requested that rollback. A fork copies filesystem data, including any
credentials installed there. Forks stay on the same host.

## Long commands and previews

`raft exec "$box" --detach -- <command>` returns a job ID. Use `raft status
"$box" <job>` and `raft logs "$box" <job>` to inspect its systemd state, exit
status and output. `raft cancel "$box" <job>` stops the job and its child processes. Stop/destroy
terminates running jobs; this is not automatic checkpointing of processes.
`raft extend "$box" --ttl 1800` resets a running box's deadline without
restarting its work. Use resume for stopped boxes. `raft usage "$box"` reports
cgroup memory/CPU and shared-pool disk space, not a per-box disk quota.

`raft forward "$box" --remote 8080 --local 8080` opens a foreground private
tunnel. `raft desktop "$box" --local 6080` starts the desktop on demand and
opens its private tunnel. Use the printed localhost address, with `vnc.html`
for the viewer. Ctrl+C closes the tunnel. The desktop service stays inside the
box until the box stops or its service is explicitly stopped.

Public hosting, reverse tunnels, browser-only confinement, agent conversation
orchestration and KVM-backed VMs are not implemented. Agent binaries can be run
through exec after authorized sign-in. Do not claim `boat prompt` parity.

## Portable backups

Stop before exporting a private archive, including snapshots:

```sh
raft stop "$box"
raft backup "$box" ./workspace.tar.gz
recovered=$(raft recover ./workspace.tar.gz --location second-host)
raft resume "$recovered" --ttl 600
```

Recovery creates a fresh stopped handle and clears the inherited deadline. It
never overwrites another box. Only import trusted Raft exports; archives retain
native configuration and guest secrets. Store them privately and check free
space on the controller and destination host. Large backups hold the host
lifecycle lock and delay expiry checks. A controller crash can leave an owned
staging archive or imported box; inspect the reported recovery handle before
cleanup. This is manual recovery, not a scheduled backup service.
