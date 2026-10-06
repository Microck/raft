<p align="center">
  <img src=".github/assets/raft-logo.png" width="220" alt="raft logo">
</p>

<h1 align="center">raft</h1>

<p align="center">
  <a href="https://github.com/Microck/raft/actions/workflows/check.yml"><img src="https://img.shields.io/github/actions/workflow/status/Microck/raft/check.yml?branch=main&style=flat-square&label=ci&color=000000" alt="ci badge"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-mit-000000?style=flat-square" alt="license badge"></a>
</p>

---

`raft` is an independent, self-hosted open-source alternative to
[boat.dev](https://boat.dev). it gives you persistent Linux boxes on machines you
control, with command execution, Docker, file transfer, snapshots, forks and a
private desktop. Raft is not affiliated with Boat.

the current runtime uses unprivileged ARM64 Incus system containers that share
the host kernel. KVM virtual machines and complete Boat feature parity are not
implemented. the table below lists what is available.

[setup](docs/operations.md) | [feature parity](docs/boat-capabilities.md) | [verification](docs/verification.md) | [agent skill](skills/raft-cli/SKILL.md)

## quickstart

use an ARM64 Ubuntu host with SSH, passwordless sudo, at least 2 CPUs, 8 GiB RAM
and 80 GiB free disk. the controller requires Python 3.11+, uv, SSH and SCP.
read [installation and operations](docs/operations.md) before provisioning.

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

location names come from configuration and use letters, digits, underscores or
hyphens. the first configured location is the default. expiration stops processes
and retains files. `destroy` deletes the workspace and its snapshots. each host
admits four saved boxes, including stopped boxes.

long backup/import operations serialize host lifecycle changes and delay expiry
checks on that host. check free space before creating large archives.

## image and workspace control

the checked-in builder starts from the public Incus Debian 13 image and installs
language runtimes, build tools, Docker, Chromium, FFmpeg and an on-demand noVNC
desktop. it records package inventories under `/opt/raft/`. no prebuilt Raft
image is distributed. build your own clean image and pin its fingerprint.
third-party packages retain their own licenses. optional coding-agent binaries
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

backups include files, secrets and native instance configuration. import only
trusted exports and store them privately. snapshots and forks require a stopped
source. stop terminates jobs; it does not checkpoint process memory.

## feature parity

✅ means Raft implements the workflow, not that its behavior is identical to Boat.
❌ means the capability is absent. the [full capability table](docs/boat-capabilities.md)
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

## verification and development

use `uv sync --locked`, `uv run raft --help` and the real-host E2E suites in
[verification](docs/verification.md). tests allocate disposable boxes and remove
only their own fixtures. the optional `--restart-incus` check restarts the host
Incus daemon. run it on dedicated test infrastructure. there is no exhaustive Boat differential
suite or security audit.

read [the contract](docs/contract.md), [architecture and references](docs/boat-parity-design.md)
and the optional [agent skill](skills/raft-cli/SKILL.md).

## license

[MIT](LICENSE). third-party tools retain their own licenses.
