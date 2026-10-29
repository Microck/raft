---
title: Raft and Boat
description: Compare Raft’s self-hosted containers with Boat’s managed VMs and workflows.
---

# Raft and Boat

Raft is an independent open-source alternative to [boat.dev](https://boat.dev).
## Choose by operating model

- **Raft:** bring a dedicated Ubuntu host and manage it through host SSH. Boxes
  are unprivileged containers sharing its kernel. You operate storage, upgrades
  and backups; there is no hosted billing, organization account or HTTP API.
- **Boat:** a managed service with full VMs, account/API workflows, public hosting
  and integrated agents. Its pricing, snapshot retention and security model are
  different from Raft's.

Start with [Raft setup](./operations.mdx) to use it, or expand the detailed matrix
when evaluating a specific capability. Similar commands are not a promise of
identical behavior, isolation or cost.

## What remains for parity

The largest missing pieces are a remote API with scoped credentials and SDKs,
managed agent conversations, versioned environments and secrets, public HTTPS
ingress, and team access. These require product and infrastructure work beyond
adding CLI commands.

Operational gaps include automatic snapshots, shared templates, snapshot file
browsing, retained command history, process inspection and telemetry export.
Smaller CLI gaps include reverse tunnels, shell completions, uniform JSON errors,
merging source uploads, friendly names and macOS/Windows controller support.

VM kernel isolation and nested KVM require a different runtime mode. Raft's
current container boundary cannot provide them through a flag. Browser-only
streaming is also separate from the existing full desktop.

<details>
<summary>Detailed capability matrix and verification evidence</summary>

✅ supported · ❌ unavailable through the compared product · ❓ not established by Boat's documentation.

A shared checkmark means both offer the workflow, not identical behavior or isolation.

Tests verify Raft behavior, not hosted Boat infrastructure. See [verification](./verification.mdx) for evidence.

| Capability | Boat | Raft | Raft limits and differences |
| --- | :---: | :---: | --- |
| Self-hosted runtime on operator hardware | ❌ | ✅ | Boat is a hosted service. Raft runs Incus on hosts controlled by the operator |
| Native ARM64 workspaces | ❌ | ✅ | Boat documents all machine sizes as x86_64; Raft also supports native ARM64 |
| Create/list/inspect/delete | ✅ | ✅ | Qualified handles, explicit locations, native Incus metadata. Single operator; no organization/account API |
| Stop/resume | ✅ | ✅ | Stops processes, retains disk; resume requires TTL. Shared-kernel containers, not Boat VMs |
| Running lifetime | ✅ | ✅ | Host-enforced TTL; extend resets deadline without restart. Long locked transactions delay expiry checks |
| Sizing | ✅ | ✅ | Any whole CPU count and GiB of RAM up to the host, plus an optional own disk. Resume/fork support CPU/RAM overrides; Boat offers fixed machine sizes |
| Host capacity estimates | ❌ | ✅ | Raft reports advisory host CPU/RAM budgets, active allocations, headroom and disk space, and enforces no box count. Boat limits describe hosted plan credits and concurrency, not operator-host sizing |
| Hosted credit and start-rate limits | ✅ | ❌ | Boat reports plan credits, concurrency and start rates. Raft has no billing service |
| Resize on resume/fork | ✅ | ✅ | Optional CPU/RAM overrides, retaining omitted limits |
| Usage | ✅ | ✅ | Cgroup memory, CPU counters, and shared-pool or own-disk space. No billing meter |
| Root terminal | ✅ | ✅ | PTY over host SSH and native Incus exec. No directly exposed guest SSH endpoint |
| Synchronous exec | ✅ | ✅ | Literal argv and preserved exit code. Shell syntax requires an explicit shell |
| Disposable foreground CI | ✅ | Preview | Unreleased 0.7.0: `run` orchestrates one fresh box, Git-aware upload, output collection and cleanup. Fixed timeout, no lease renewal/keep mode; host sudo SSH rather than scoped API keys. [CI guide](./guides/ci-jobs.mdx) |
| Detached exec | ✅ | ✅ | Guest systemd unit, status, logs, cancellation. No process checkpoint or restart after stop |
| File transfer | ✅ | ✅ | Binary file upload/download. Recursive tree transfer for running boxes; no directory merges |
| Named snapshots | ✅ | ✅ | Create/list/restore with stopped source, checked inside the lifecycle lock. No running snapshot consistency guarantee |
| Deploy/delete named snapshots | ✅ | ✅ | `new --from LOCATION:BOX/SNAPSHOT` and `snapshot-delete`; templates stay on their source host |
| Fork | ✅ | ✅ | Same-host stopped filesystem copy. Copies filesystem secrets; no cross-host fork command |
| Portable recovery | ❓ | ✅ | Raft exports/imports whole native instances including snapshots. Boat documents snapshot file downloads; importing a portable whole-instance archive is not established. Raft backup storage is manual; no scheduled off-host uploads |
| Docker | ✅ | ✅ | Guest daemon with real image builds. Container nesting uses host kernel |
| Native development images | ✅ | ✅ | Boat: Ubuntu x86_64 machines. Raft: published native Debian ARM64/AMD64 images with pinned fingerprints and clean-archive inspection |
| Development tools | ✅ | ✅ | 31 real developer-user checks. Debian/Chromium, not identical Ubuntu/Chrome versions; VS Code, Ghostty and GitHub CLI are not bundled; version-manager coverage differs |
| Private forward | ✅ | ✅ | Foreground authenticated SSH tunnel to controller loopback. Guest service must listen on its managed NIC; no reverse forwarding |
| Public workspace hosting | ✅ | ❌ | Missing. Deferred; requires a defined ingress and access policy |
| Desktop | ✅ | ✅ | On-demand noVNC, framebuffer, pointer and browser transport checks. See verification for desktop interaction coverage |
| Browser-only streaming | ✅ | ❌ | Missing. Full desktop only; browser confinement deferred |
| Network separation | ❓ | ✅ | Raft tests host/private/metadata and IPv4/IPv6 peer restrictions with startup-gated filtering. Boat's equivalent filtering policy is not established; its VM boundary is listed separately |
| Bounded host storage pools | ❓ | ✅ | Raft configures a shared 60 GiB Btrfs pool and, when enabled, a fully allocated LVM thin pool for own box disks that it never overcommits, and rejects size drift. Boat does not document an operator-managed host storage pool |
| Strict per-box disk allocation | ✅ | ✅ | On hosts with own box disks enabled, `new --disk SIZE` gives a box a fixed-size, copy-on-write ext4 disk that guest and Docker writes cannot exceed. Shared-pool boxes, the default, have no per-box quota |
| VM kernel isolation | ✅ | ❌ | VM deployment not implemented |
| Nested KVM / Android emulator acceleration | ✅ | ❌ | Boat currently documents `/dev/kvm` on every sandbox. Raft has no guest KVM device or VM mode |
| External guest SSH | ✅ | ❌ | Boat supports a public IP or an SSH endpoint; public IPv4 is not guaranteed. Raft uses host-mediated private access |
| Named environments and secrets | ✅ | ❌ | Files/configuration can be prepared explicitly. No environment registry or secret-injection service |
| Organizations | ✅ | ❌ | Boat supports team-visible sandboxes, optional shared access, roles and per-member environment/profile restrictions. Raft is single-operator |
| Lifecycle webhooks | ✅ | ❌ | Missing. Deferred until an external consumer needs them |
| Automatic deletion/retention | ✅ | ✅ | Disposable delete-on-stop/expiry; opt-in stopped-box retention cleanup with preview |
| Dashboard | ✅ | ❌ | Missing. CLI and private desktop cover current workflow |
| SDK/API keys | ✅ | ❌ | Host SSH authentication. No remote Raft service API |
| CLI-wide JSON mode | ✅ | Preview | Boat supports JSON/JSONL for most commands; argument errors may use stderr. Raft provides command-specific JSON/JSONL for locations, list, info, usage, capacity, config and prune, and unreleased global JSONL events for reports, streams, errors and exit codes |
| Snapshot file browsing/download | ✅ | ❌ | Full native archives only; no Raft snapshot tree or selected-file interface |
| CLI self-update | ✅ | ✅ | `raft self-update` upgrades npm global installs through the registry and uv tools from a release wheel checked against `SHA256SUMS`. A daily stderr notice reports new releases. Boat also updates itself after commands; Raft never installs without the command and never changes hosts |
| Dynamic shell completions | ✅ | Preview | Unreleased Bash, Zsh and Fish scripts query the installed parser for commands, options and fixed choices |
| Managed agents | ✅ | ❌ | Not implemented. No prompt/events/steer/interrupt/conversation API |
| Automatic incremental snapshots | ✅ | ❌ | Boat documents saves every minute and on stop. Raft retains the container disk and creates named snapshots only on request; no scheduled off-host snapshot service |
| Share saved templates across accounts | ✅ | ❌ | Boat provides revocable deployment codes and credential cleanup. Raft copies complete filesystems on the same host or imports trusted whole archives; no sharing identity or sanitization service |
| Versioned environments and upgrades | ✅ | ❌ | Boat versions repositories, setup and secrets, with explicit upgrades. Raft has image pins, controller defaults and filesystem templates, not an environment registry |
| AI credential profiles and proxy | ✅ | ❌ | No provider credential manager, sandbox access switch, usage accounting or AI request proxy |
| OpenTelemetry export | ✅ | ❌ | No built-in OTLP metrics or lifecycle-event export |
| Command history while stopped | ✅ | ❌ | Boat stores command output and process samples outside the guest. Raft job logs require a running box and its retained journal |
| Process inspection command | ✅ | ❌ | Raft can execute guest tools such as ps, but has no first-class process/history API or stopped-box process samples |
| Merge source into existing directories | ✅ | ❌ | Boat push replaces matching files in an existing destination. Raft recursive transfer deliberately requires a new destination; Git-filtered upload is currently limited to unreleased run |
| Human-readable names and current/self aliases | ✅ | ❌ | Raft uses exact location-qualified handles and ownership notes; no rename or implicit current-box selection |
| macOS and Windows controllers | ✅ | ❌ | Raft's controller requires Linux; native ARM64/AMD64 guest support is a separate capability |
| Unresponsive-machine recovery | ✅ | ❌ | Boat recover power-cycles an unresponsive VM. Raft recover imports an archive and is not the equivalent operation |

## Reference check

Rechecked on 2026-10-29 against Boat's [CLI reference](https://docs.boat.dev/cli-reference), [machine capabilities](https://docs.boat.dev/machines), [SSH/file access](https://docs.boat.dev/ssh-access), [snapshots](https://docs.boat.dev/snapshots), [environments](https://docs.boat.dev/environments), [environment versions](https://docs.boat.dev/environments/versions), [organizations](https://docs.boat.dev/organizations), [AI proxy API](https://docs.boat.dev/api/reference/ai-credentials/get-ai-credentials-proxy), and [telemetry API](https://docs.boat.dev/api/reference/account/get-opentelemetry-export).

Some Boat overview/API text still describes creator-private resources; the dedicated organization guide and current CLI document shared sandboxes. This matrix follows those specific workflow descriptions. Boat's browser-only view restricts display and input, not access by other code inside its sandbox. These are documented capabilities, not independently tested security guarantees.

Raft's latest published release was verified as [0.6.0](https://github.com/Microck/raft/releases/tag/v0.6.0) on 2026-10-29. `run` and the new help/read-performance changes remain development-branch features until released.

The new workspace-control suite passed on ARM64 and AMD64 at runtime commit `a7fef1f5` in [run 37603576786](https://github.com/Microck/raft/actions/runs/37603576786). The baseline native architecture and disposable AMD64 host tests passed in [run 37496577945](https://github.com/Microck/raft/actions/runs/37496577945). This audit compares documented features, not a live differential test against Boat. Managed agent workflows remain out of scope.

Recursive transfers, resume/fork sizing, snapshot templates/deletion and disposable lifecycle are implemented. Directory transfers require a running box; templates stay on the source host. Public hosting, VM isolation, organization sharing, and remote APIs require additional infrastructure. noVNC tests do not establish parity for Boat frame rate, browser confinement or application set.

CI comparison sources (reviewed 2026-10-22): [Boat CI](https://docs.boat.dev/ci), [CLI run](https://docs.boat.dev/cli-reference#boat-run-command), [scoped API keys](https://docs.boat.dev/api-keys/scopes), and [snapshot contents](https://docs.boat.dev/snapshots/whats-saved). Matching workflows do not imply matching security boundaries or costs.

</details>
