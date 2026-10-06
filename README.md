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

the current runtime uses unprivileged native-architecture Incus system containers that share
the host kernel. KVM virtual machines and complete Boat feature parity are not
implemented. the table below lists what is available.

[setup](docs/operations.md) | [feature parity](docs/boat-capabilities.md) | [verification](docs/verification.md) | [agent skill](skills/raft-cli/SKILL.md)

## quickstart

start with the [setup guide](docs/operations.md). it covers installing the CLI,
configuring SSH, provisioning Incus and building your image.

| requirement | minimum |
| --- | --- |
| host | dedicated ARM64 or AMD64 Ubuntu host, 2 CPUs, 8 GiB RAM, 80 GiB free disk |
| host access | SSH and passwordless sudo |
| controller | Linux, Python 3.11+, uv, SSH and SCP |

once your host and image are configured:

```sh
box=$(raft new --location lab --ttl 600)
raft exec "$box" -- node --version
raft ssh "$box"
raft stop "$box"
```

`stop` and expiration retain your files. `resume` starts the box again;
`destroy` deletes it and its snapshots. each host admits four saved boxes,
including stopped boxes. location names come from your configuration.

## image and workspace control

the image builder starts from public Debian 13 and adds language runtimes,
build tools, Docker, Chromium, FFmpeg and a noVNC desktop. build your own image
and pin its fingerprint, or use a verified native CI artifact from
[image builds and boot measurements](docs/boot-performance.md).

see the operations guide for [background jobs and files](docs/operations.md#jobs-and-files),
[snapshots and forks](docs/operations.md#snapshots-and-forks),
[private services and desktop access](docs/operations.md#private-services)
and [backup and recovery](docs/operations.md#backups-and-troubleshooting).

snapshots and forks require a stopped source. stop terminates jobs without
saving process memory. backups include guest secrets; keep them private and
import only trusted exports. long backups can delay host expiry checks.

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
| Docker and development tools | ✅ | Native ARM64/AMD64; [image tests](docs/boot-performance.md) |
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
