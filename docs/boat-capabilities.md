---
title: Boat feature parity
---

# Boat capability coverage

Raft is an independent open-source alternative to [boat.dev](https://boat.dev).
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
| Sizing | ✅ | ✅ | 1/2 CPUs and 1/2/4 GiB choices. Resume/fork support sizing overrides; Boat offers larger sizes |
| Host capacity recommendations | ❌ | ✅ | Raft reports host CPU/RAM budgets, active allocations, saved slots and disk headroom. Boat limits describe hosted plan credits and concurrency, not operator-host sizing. Raft enforces four saved boxes per host |
| Hosted credit and start-rate limits | ✅ | ❌ | Boat reports plan credits, concurrency and start rates. Raft has no billing service |
| Resize on resume/fork | ✅ | ✅ | Optional CPU/RAM overrides, retaining omitted limits |
| Usage | ✅ | ✅ | Cgroup memory, CPU counters, shared pool space. No billing meter or per-box disk accounting |
| Root terminal | ✅ | ✅ | PTY over host SSH and native Incus exec. No directly exposed guest SSH endpoint |
| Synchronous exec | ✅ | ✅ | Literal argv and preserved exit code. Shell syntax requires an explicit shell |
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
| Bounded host storage pool | ❓ | ✅ | Raft configures a shared 60 GiB Btrfs host pool and rejects size drift. Boat does not document an operator-managed host storage pool |
| Strict per-box disk allocation | ✅ | ❌ | Nested subvolumes prevent claiming strict quotas; no independent Boat-sized disks |
| VM kernel isolation | ✅ | ❌ | VM deployment not implemented |
| Nested KVM / Android emulator acceleration | ✅ | ❌ | Boat documents KVM on standard hosts; overflow cloud VMs lack it. Raft has no guest KVM device or VM mode |
| External guest SSH | ✅ | ❌ | Boat supports a public IP or an SSH endpoint; public IPv4 is not guaranteed. Raft uses host-mediated private access |
| Named environments and secrets | ✅ | ❌ | Files/configuration can be prepared explicitly. No environment registry or secret-injection service |
| Organizations | ✅ | ❌ | Boat shares billing and plan limits; resources stay with their creator. Raft is single-operator |
| Lifecycle webhooks | ✅ | ❌ | Missing. Deferred until an external consumer needs them |
| Automatic deletion/retention | ✅ | ✅ | Disposable delete-on-stop/expiry; opt-in stopped-box retention cleanup with preview |
| Dashboard | ✅ | ❌ | Missing. CLI and private desktop cover current workflow |
| SDK/API keys | ✅ | ❌ | Host SSH authentication. No remote Raft service API |
| CLI-wide JSON mode | ✅ | ❌ | Boat supports JSON/JSONL for most commands; argument errors may use stderr. Raft provides JSON for list/info/usage/limits only |
| Snapshot file browsing/download | ✅ | ❌ | Full native archives only; no Raft snapshot tree or selected-file interface |
| CLI self-update/completions | ✅ | ❌ | Standard Python package installation/help. No update channel or dynamic shell completions |
| Managed agents | ✅ | ❌ | Not implemented. No prompt/events/steer/interrupt/conversation API |

## Reference check

Checked on 2026-10-06 against Boat's [CLI reference](https://docs.boat.dev/cli-reference), [machine capabilities](https://docs.boat.dev/machines), [SSH/file access](https://docs.boat.dev/ssh-access), [snapshots](https://docs.boat.dev/box/snapshots), and [billing and limits](https://docs.boat.dev/billing).

Raft command parser and test suites were verified at commit `27b08c1`. Both native architectures and disposable AMD64 host tests passed in [run 37496577945](https://github.com/Microck/raft/actions/runs/37496577945). This audit compares documented features, not a live differential test against Boat. Managed agent workflows remain out of scope.

Recursive transfers, resume/fork sizing, snapshot templates/deletion and disposable lifecycle are implemented. Directory transfers require a running box; templates stay on the source host. Public hosting, VM isolation, organization sharing, and remote APIs require additional infrastructure. noVNC tests do not establish parity for Boat frame rate, browser confinement or application set.
