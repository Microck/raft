# Boat capability coverage

Raft is an independent open-source alternative to [boat.dev](https://boat.dev).
✅ means an implemented Raft workflow. It does not mean identical Boat behavior.
❌ means missing or intentionally outside the current scope. Tests verify Raft,
not the hosted Boat service. See [verification](verification.md) for evidence.

| Capability | Raft | Behavior and verification limits |
| --- | --- | --- |
| Create/list/inspect/delete | ✅ | Qualified handles, explicit locations, native Incus metadata. Single operator; no organization/account API |
| Stop/resume | ✅ | Stops processes, retains disk; resume requires TTL. Shared-kernel containers, not Boat VMs |
| Running lifetime | ✅ | Host-enforced TTL; extend resets deadline without restart. Long locked transactions delay expiry checks |
| Sizing | ✅ | 1/2 CPUs and 1/2/4 GiB choices. Limited by configured host capacity |
| Usage | ✅ | Cgroup memory, CPU counters, shared pool space. No billing meter or per-box disk accounting |
| Root terminal | ✅ | PTY over host SSH and native Incus exec. No directly exposed guest SSH endpoint |
| Synchronous exec | ✅ | Literal argv and preserved exit code. Shell syntax requires an explicit shell |
| Detached exec | ✅ | Guest systemd unit, status, logs, cancellation. No process checkpoint or restart after stop |
| File transfer | ✅ | Binary file upload/download. No recursive SCP interface |
| Named snapshots | ✅ | Create/list/restore with stopped source. No running snapshot consistency guarantee |
| Fork | ✅ | Same-host stopped filesystem copy. Copies filesystem secrets; no cross-host fork command |
| Portable recovery | ✅ | Stopped native export including snapshots; fresh stopped import. Manual backup storage; no scheduled off-host destination |
| Docker | ✅ | Guest daemon with real image builds. Container nesting uses host kernel |
| Development tools | ✅ | Real developer-user program checks. No exhaustive Boat version or version-manager match |
| Private forward | ✅ | Foreground authenticated SSH tunnel to controller loopback. Guest service must listen on its managed NIC; no reverse forwarding |
| Public hosting | ❌ | Missing. Deferred; requires a defined ingress and access policy |
| Desktop | ✅ | On-demand noVNC, framebuffer, pointer and browser transport checks. See verification for desktop interaction coverage |
| Browser-only streaming | ❌ | Missing. Full desktop only; browser confinement deferred |
| Network separation | ✅ | Host/private/metadata and IPv4/IPv6 peer restrictions. Not a substitute for a VM kernel boundary |
| Storage isolation | ✅ | Shared 60 GiB Btrfs pool per host. Nested subvolumes prevent claiming strict per-box quotas |
| VM kernel isolation | ❌ | VM deployment not implemented |
| Named environments and secrets | ❌ | Files/configuration can be prepared explicitly. No environment registry or secret-injection service |
| Sharing and organizations | ❌ | Missing. Outside the personal single-operator scope |
| Lifecycle webhooks | ❌ | Missing. Deferred until an external consumer needs them |
| Automatic deletion/retention | ❌ | Explicit destroy; expiry retains disk. No delete-on-stop mode |
| Dashboard | ❌ | Missing. CLI and private desktop cover current workflow |
| SDK/API keys | ❌ | Host SSH authentication. No remote Raft service API |
| CLI self-update/completions | ❌ | Standard Python package installation/help. No update channel or dynamic shell completions |
| Managed agents | ❌ | Not implemented. No prompt/events/steer/interrupt/conversation API |

