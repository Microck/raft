---
name: raft-cli
description: Create and use private persistent Linux workspaces on configured Linux hosts with Raft and Incus. Use for self-hosted builds, tests, terminals, Docker, file transfer, snapshots, and private desktop access. Hosted Boat uses its separate boat-cli skill.
---

# Raft

Raft runs unprivileged native ARM64 or AMD64 Incus system containers. They share the host kernel.
The current deployment does not implement Incus VMs. Do not describe these boxes as VMs or promise cross-architecture emulation.

`raft host add` installs packages and services on a machine. Run it only when the
user explicitly asks to onboard that host, and show its `--dry-run` plan first.

Do not run `raft self-update` unless the user asks; an update in the middle of a
task changes the CLI under it. Report a `raft X is available` stderr notice
instead of acting on it. For pinned or scripted runs, set
`RAFT_NO_UPDATE_CHECK=1` to suppress the notice.

Run `raft locations`, `raft config`, `raft doctor` and `raft list` before allocating.
`raft config` shows the defaults `new` will use on this controller and each
location's note policy: the box note fields its host requires.
Follow each location's `note`: the operator uses it to mark preferred hosts and
hosts to avoid for some work. Pass `--location` explicitly when a note rules out
the default. Do not change notes unless the user asks; `raft note LOCATION TEXT`
sets one and `raft note LOCATION --clear` removes it. Keep the exact
location-qualified handle returned by `new`. There is no implicit current box.

Note every box you create. Box notes follow one template so the next agent can
tell whether a box is in use:

- `--purpose`: what the box is for (required), e.g. `Integration tests for PR 42`
- `--owner`: who uses it, e.g. `codex session in ~/work/app`
- `--next`: what happens next, e.g. `Destroy after the tests pass`

Pass them to `new`, and run `raft note "$box" --purpose ...` when the plan
changes; each call replaces the whole note. If a command fails with
`requires --owner (host policy)` or similar, rerun it with the fields it names;
the error prints the corrected command. Never change or clear a policy with
`raft policy`, or the defaults with `raft config set/unset`, to get past an
error; only do so when the user asks. `fork` and `recover` accept the same note
flags, which replace the whole note on the copy. `raft list` shows every box's
`note`. Read it before you resume, stop, fork or destroy a box. Treat a box whose
note names another owner or a job unrelated to your task as not yours. Forks
keep the source note, so update a fork's note when its job differs.

```sh
box=$(raft new --location lab --ttl 600 --purpose "Run the project tests" --owner "codex task 123" --next "Destroy when the tests finish")
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

## Sizing boxes

Create the boxes your task needs, sized for the work. Raft has no box count
limit and no size budget. Decide what the task needs and why, then ask for it:

- `--cpu N`: any whole number of CPUs up to the host's count (default 1). Use
  more for parallel builds and test suites.
- `--memory SIZE`: any whole GiB up to the host's RAM, e.g. `8GiB` or `32GiB`
  (default `2GiB`). Use more for large compiles, browsers or databases.
- `--disk SIZE`: its own disk, e.g. `30GiB`, instead of the shared 60 GiB pool.
  The box and Docker inside it cannot write past SIZE, and other boxes cannot
  take its space. Use it for Docker builds, large datasets or anything that
  could fill the shared pool. The image needs about 5 GiB, so use at least
  `8GiB`; 10 GiB or more starts fastest. Only hosts deployed with
  `--disk-pool` have own disks; elsewhere `--disk` fails and names the command
  that enables them. Own disks, their snapshots, forks and templates are
  copy-on-write, so copying one is fast, but each reserves its full size in
  the pool.

```sh
box=$(raft new --cpu 4 --memory 16GiB --disk 20GiB --ttl 3600 --purpose "Build and test the monorepo" --owner "codex task 123" --next "Destroy when CI passes")
```

Built-in defaults are the first configured location, one CPU, 2 GiB RAM, the
shared pool and 600 seconds; the operator can change them per controller, so
pass `--location`, `--ttl`, `--cpu`, `--memory` and `--disk` (`--disk shared`
for the shared pool) when the values matter. `new --from` and `fork` keep the
source's CPU, RAM and disk instead of any default; each flag overrides only its
own value. TTL accepts 60 seconds to 30
days. A request larger than the whole host fails with the host's real size;
retry with what it reports. If the own-disk pool has too little unreserved
space for `--disk`, a snapshot or a copy, destroy own-disk boxes or snapshots
the task no longer needs, or pick a smaller disk or the shared pool.
`raft list` shows each box's `disk` (`"shared"` or its size).

`raft capacity --location lab --json` is an advisory estimate of host CPU/RAM
headroom and free disk. It is advice, never a reason to skip creating a box or
to shrink one below what the task needs. Boxes share host CPUs; an estimate of
zero means new boxes may run slower, not that they are refused.

## Persistence and cleanup

Persistent boxes stop and retain disk at expiry. `new --disposable` deletes a
box and its snapshots on stop or expiry. `raft stop "$box"` releases the
container's running processes; `raft resume "$box" --ttl 600` starts it again.
Stop is not pause and does not keep guest RAM allocated. Installed dependencies,
files and secrets remain on disk. For a temporary run, completion requires
successful `destroy`, even after command failure. Stop reusable runners when
finished. Destroy only boxes owned by the current task unless authorized otherwise.

Host systemd timers enforce deadlines every 15 seconds, even when the controller
is offline. `raft gc` runs those expiry workers manually and deletes expired disposable
boxes. `raft prune --older-than 7` previews stopped boxes older than seven days;
`--yes` deletes them. It skips running boxes and boxes without a recorded stop
time. Never apply prune outside the task-owned scope without explicit permission. A powered-off host enforces expiry when it returns.

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
credentials installed there. Forks stay on the same host. `new --from "$box/prepared"` starts from a named
snapshot; `snapshot-delete "$box" prepared` removes it. Resume and fork accept
optional `--cpu` and `--memory` overrides. Omitted values retain the source limits.
Forks and `new --from` keep the source's CPU, RAM and disk unless a flag
overrides them; `new --from "$box/prepared" --disk 60GiB` copies the snapshot onto
a bigger own disk when a box runs out of space. To spawn many agents from one
prepared own-disk snapshot, run `new --from` once per agent: each copy is a thin
snapshot created in seconds.
Forks default to persistent; use `--disposable` explicitly for temporary clones.

`upload/download --recursive` copy directory trees into new destinations without
merging or overwriting. They require a running guest with Python 3.11.8+ and
temporary controller disk space. Escaping links and special files are rejected.

## Long commands and previews

`raft exec "$box" --detach -- <command>` returns a job ID. Use `raft status
"$box" <job>` and `raft logs "$box" <job>` to inspect its systemd state, exit
status and output. `raft cancel "$box" <job>` stops the job and its child processes. Stop/destroy
terminates running jobs; this is not automatic checkpointing of processes.
`raft extend "$box" --ttl 1800` resets a running box's deadline without
restarting its work. Use resume for stopped boxes. `raft usage "$box"` reports
cgroup memory/CPU and disk space; `filesystemScope` says whether that is the
shared pool or the box's own disk.

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
never overwrites another box. A box with its own disk comes back on an own disk
of the same size, so the destination needs own box disks enabled and room for the
box and each of its snapshots. Only import trusted Raft exports; archives retain
native configuration and guest secrets. Store them privately and check free
space on the controller and destination host. Large backups hold the host
lifecycle lock and delay expiry checks. A controller crash can leave an owned
staging archive or imported box; inspect the reported recovery handle before
cleanup. This is manual recovery, not a scheduled backup service.
