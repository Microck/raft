---
title: Boat feature parity
---

# Boat capability coverage

Raft is an independent open-source alternative to [boat.dev](https://boat.dev).
- ✅ indicates an implemented Raft workflow (does not imply identical Boat behavior).
- ❌ indicates an unsupported or out-of-scope feature.

Tests verify Raft behavior, not hosted Boat infrastructure. See [verification](./verification.mdx) for evidence.

| Capability | Raft | Behavior and verification limits |
| --- | --- | --- |
| Create/list/inspect/delete | ✅ | Qualified handles, explicit locations, native Incus metadata. Single operator; no organization/account API |
| Stop/resume | ✅ | Stops processes, retains disk; resume requires TTL. Shared-kernel containers, not Boat VMs |
| Running lifetime | ✅ | Host-enforced TTL; extend resets deadline without restart. Long locked transactions delay expiry checks |
| Sizing | ✅ | Create-time 1/2 CPUs and 1/2/4 GiB choices. No resizing on resume/fork; Boat offers larger sizes |
| Host capacity / limits | ✅ | `raft limits` reports recommended running counts, active allocations, saved slots and disk headroom. Four saved boxes enforced; no hosted credit/start-rate meter |
| Resize on resume/fork | ❌ | Native admin configuration is outside the Raft CLI contract |
| Usage | ✅ | Cgroup memory, CPU counters, shared pool space. No billing meter or per-box disk accounting |
| Root terminal | ✅ | PTY over host SSH and native Incus exec. No directly exposed guest SSH endpoint |
| Synchronous exec | ✅ | Literal argv and preserved exit code. Shell syntax requires an explicit shell |
| Detached exec | ✅ | Guest systemd unit, status, logs, cancellation. No process checkpoint or restart after stop |
| File transfer | ✅ | Binary file upload/download. No recursive SCP interface |
| Named snapshots | ✅ | Create/list/restore with stopped source, checked inside the lifecycle lock. No running snapshot consistency guarantee |
| Deploy/delete named snapshots | ❌ | No `new --from` or snapshot deletion command; destroy removes the box and its snapshots |
| Fork | ✅ | Same-host stopped filesystem copy. Copies filesystem secrets; no cross-host fork command |
| Portable recovery | ✅ | Stopped native export including snapshots; fresh stopped import. Manual backup storage; no scheduled off-host destination |
| Docker | ✅ | Guest daemon with real image builds. Container nesting uses host kernel |
| Native development images | ✅ | Published ARM64/AMD64 bases, pinned fingerprints and clean-archive inspection; native builds and full-image recovery passed on both |
| Development tools | ✅ | 31 real developer-user checks. Debian/Chromium, not identical Ubuntu/Chrome versions; VS Code, Ghostty and GitHub CLI are not bundled; version-manager coverage differs |
| Private forward | ✅ | Foreground authenticated SSH tunnel to controller loopback. Guest service must listen on its managed NIC; no reverse forwarding |
| Public workspace hosting | ❌ | Missing. Deferred; requires a defined ingress and access policy |
| Desktop | ✅ | On-demand noVNC, framebuffer, pointer and browser transport checks. See verification for desktop interaction coverage |
| Browser-only streaming | ❌ | Missing. Full desktop only; browser confinement deferred |
| Network separation | ✅ | Host/private/metadata and IPv4/IPv6 peer restrictions; filtering must succeed before Incus starts, including socket startup. Not a VM kernel boundary |
| Bounded host storage pool | ✅ | Shared 60 GiB Btrfs pool per host; deployment rejects configured size drift without resizing |
| Strict per-box disk allocation | ❌ | Nested subvolumes prevent claiming strict quotas; no independent Boat-sized disks |
| VM kernel isolation | ❌ | VM deployment not implemented |
| Nested KVM / Android emulator acceleration | ❌ | No guest KVM device or VM mode; Boat documents KVM on its standard hosts |
| Dedicated public IP / external guest SSH | ❌ | Host-mediated private access only |
| Named environments and secrets | ❌ | Files/configuration can be prepared explicitly. No environment registry or secret-injection service |
| Sharing and organizations | ❌ | Missing. Outside the personal single-operator scope |
| Lifecycle webhooks | ❌ | Missing. Deferred until an external consumer needs them |
| Automatic deletion/retention | ❌ | Explicit destroy; expiry retains disk. No delete-on-stop mode |
| Dashboard | ❌ | Missing. CLI and private desktop cover current workflow |
| SDK/API keys | ❌ | Host SSH authentication. No remote Raft service API |
| Stable JSON across all commands | ❌ | JSON exists for list/info/usage/limits; other commands use text or passthrough output |
| Snapshot file browsing/download | ❌ | Full native archives only; no Raft snapshot tree or selected-file interface |
| CLI self-update/completions | ❌ | Standard Python package installation/help. No update channel or dynamic shell completions |
| Managed agents | ❌ | Not implemented. No prompt/events/steer/interrupt/conversation API |

## Reference check

Checked on 2026-10-06 against Boat's [CLI reference](https://docs.boat.dev/cli-reference), [machine capabilities](https://docs.boat.dev/machines), [SSH/file access](https://docs.boat.dev/ssh-access), and [limits](https://docs.boat.dev/pricing).

Raft command parser and test suites were verified at commit `27b08c1`. Both native architectures and disposable AMD64 host tests passed in [run 37496577945](https://github.com/Microck/raft/actions/runs/37496577945). This audit compares documented features, not a live differential test against Boat. Managed agent workflows remain out of scope.

Key workflow gaps are recursive file transfers and resizing on resume/fork. Public hosting, VM isolation, organization sharing, and remote APIs require additional infrastructure. noVNC tests do not establish parity for Boat frame rate, browser confinement or application set.
