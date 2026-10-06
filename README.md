# Raft

Raft is an independent, self-hosted open-source alternative to
[boat.dev](https://boat.dev). Create persistent Linux boxes on machines you
control, run builds and Docker, transfer files, save snapshots, fork workspaces
and access a private desktop. Raft is not affiliated with Boat.

The current runtime uses unprivileged ARM64 Incus system containers. They share
the host kernel. Raft does not currently deploy KVM virtual machines or provide
complete Boat feature parity.

## Quick start

Use an ARM64 Ubuntu host with SSH, passwordless sudo, at least 2 CPUs, 8 GiB RAM
and 80 GiB free disk. The controller requires Python 3.11+, uv, SSH and SCP.
Read [installation and operations](docs/operations.md) before provisioning.

```sh
uv sync --locked
uv tool install --editable .
mkdir -p ~/.config/raft
cp docs/incus.example.json ~/.config/raft/incus.json
chmod 600 ~/.config/raft/incus.json
# Edit the configuration with your own SSH target.
python3 deploy/deploy.py --location lab
python3 deploy/build-image.py --location lab
# Set image to the full development-image fingerprint printed by the builder.
raft doctor
box=$(raft new --location lab --ttl 600)
raft exec "$box" -- node --version
raft exec "$box" -- docker info
raft ssh "$box"
raft stop "$box"
raft resume "$box" --ttl 600
raft destroy "$box"
```

Location names come from configuration and use letters, digits, underscores or hyphens. The first configured location is the
default. Expiration stops processes and retains files. Explicit `destroy`
deletes the workspace and its snapshots. Four saved boxes are admitted per host.
Long backup/import operations serialize host lifecycle changes and delay expiry
checks on that host. Use explicit free-space checks before large archives.

## Image and workspace control

The checked-in builder starts from the public Incus Debian 13 image and installs
language runtimes, build tools, Docker, Chromium, FFmpeg and an on-demand noVNC
desktop. It records package inventories under `/opt/raft/`. No prebuilt Raft
image is distributed. Build your own clean image and pin its fingerprint.
Third-party packages retain their own licenses. Optional coding-agent binaries
in the image require separate sign-in; Raft has no managed agent service.

```sh
box=$(raft new --location lab --ttl 1800)
job=$(raft exec "$box" --detach -- bash -lc 'echo hello; sleep 600')
raft status "$box" "$job"
raft logs "$box" "$job"
raft cancel "$box" "$job"
raft extend "$box" --ttl 1800
raft usage "$box"
raft upload "$box" ./source.tar /workspace/source.tar
raft download "$box" /workspace/source.tar ./download.tar
raft stop "$box"
raft snapshot "$box" prepared
copy=$(raft fork "$box" --ttl 600)
raft desktop "$copy" --local 6080
# Ctrl+C closes the private tunnel. Open /vnc.html in your browser.
raft destroy "$copy"
raft backup "$box" ./workspace.tar.gz
recovered=$(raft recover ./workspace.tar.gz --location lab)
raft resume "$recovered" --ttl 600
raft destroy "$recovered"
raft destroy "$box"
```

Backups include files, secrets and native instance configuration. Import only
trusted exports and store them privately. Snapshots and forks require a stopped
source. Stop terminates jobs; it does not checkpoint process memory.

## Feature parity

✅ means Raft implements the workflow, not that its behavior is identical to Boat.
❌ means the capability is absent. The [full capability table](docs/boat-capabilities.md)
records limits and test coverage.

| Boat-style capability | Raft | Limit |
| --- | :---: | --- |
| Create/list/inspect/delete | ✅ | Single operator |
| Persistent stop/resume | ✅ | Shared host kernel |
| TTL and lifetime extension | ✅ | Expiration retains disk |
| CPU/memory sizing and usage | ✅ | Shared-pool disk reporting |
| Root terminal and command execution | ✅ | Host SSH transport |
| Background jobs, logs and cancellation | ✅ | No process checkpoints |
| File upload/download | ✅ | Individual files |
| Snapshots and same-host forks | ✅ | Stopped source |
| Portable backup/recovery | ✅ | Manual archive storage |
| Docker and development tools | ✅ | ARM64; not exact Boat tool versions |
| Private port forwarding | ✅ | Local SSH tunnel |
| Desktop access | ✅ | noVNC; see verification limits |
| Recursive SCP and reverse forwarding | ❌ | Not implemented |
| Public hosting and browser-only streaming | ❌ | Not implemented |
| Strict per-box disk quotas | ❌ | Shared 60 GiB Btrfs pool |
| Separate-kernel VM isolation | ❌ | Container runtime |
| Named environments and managed secrets | ❌ | Manual configuration |
| Sharing, organizations and webhooks | ❌ | Not implemented |
| Dashboard, remote API and SDK | ❌ | CLI only |
| Managed agent conversations | ❌ | Not implemented |
| Automatic deletion and CLI self-update | ❌ | Explicit commands |

## Verification and development

Use `uv sync --locked`, `uv run raft --help` and the real-host E2E suites in
[verification](docs/verification.md). Tests allocate disposable boxes and remove
only their own fixtures. The optional `--restart-incus` check restarts the host Incus daemon;
run it on dedicated test infrastructure. There is no exhaustive Boat differential
suite or security audit.

Read [the contract](docs/contract.md), [architecture and references](docs/boat-parity-design.md)
and the optional [agent skill](skills/raft-cli/SKILL.md).

## License

Raft source is MIT licensed. Third-party tools retain their own licenses.
