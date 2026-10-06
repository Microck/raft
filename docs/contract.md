---
title: Runtime contract
---

# Raft contract

Raft manages persistent unprivileged native-architecture Incus system containers on the
configured ARM64 or AMD64 Linux hosts. Images must match the host architecture;
foreign-architecture emulation is not supported. Containers share the host kernel.
Incus virtual machines require usable KVM and are not silently substituted.

Versioned image releases contain clean native ARM64 and AMD64 templates,
SHA256 checksums, package inventories and build provenance. Publication requires
native lifecycle/recovery checks and a clean AMD64 Ubuntu VM provisioning and
reboot check. The test VM uses KVM and a sparse 100 GiB virtual disk; it does
not certify underlying physical storage capacity or ARM64 host reboot behavior.

- Deployment refuses conflicting storage/network names and incomplete owned
  infrastructure rather than silently overwriting or repairing it.
- `new`, `list`, `info`, `exec`, `ssh`, `upload`, `download`, `stop`, `resume`,
  `destroy`, `snapshot`, `snapshots`, `restore`, `fork`, `forward`, `desktop`,
  `status`, `logs`, `cancel`, `usage`, `extend`, `backup`, `recover`, `limits`, `doctor`
  and `gc` cover the single-operator workflow.
- `info` and `list` read native instance metadata, without querying guest process
  counters during shutdown. Tunnels read the running guest's managed NIC directly.
- Configuration requires named SSH targets; names use letters, digits, underscores
  or hyphens. Every handle explicitly includes location and instance name. Creation uses
  the immutable local development image fingerprint, not an updating alias.
- Default first configured location, 1 CPU, 2 GiB RAM and 600-second running lifetime. CPU choices
  1/2; memory 1/2/4 GiB; lifetime 60 seconds to 30 days. Four saved boxes per
  host is the admission limit, including stopped boxes. Running TTL starts after
  launch/start completes, using the host clock; cold image preparation does not
  consume it. Native Incus admin calls
  bypass CLI policy. Creation and expiry are serialized by host flock.
- `limits [--location NAME] [--json]` inspects hosts without changing them.
  It reports effective host CPUs, total/available RAM, saved and active box counts,
  configured active CPU/memory limits and host/shared-pool disk space. It recommends
  total and additional running boxes for each supported size, reserving one CPU
  and the larger of 2 GiB or 10% of host RAM. Total recommendations assume an
  otherwise empty dedicated host and are capped at four. Additional recommendations
  subtract active configured limits and use current available RAM minus the host
  reserve. Every state except Stopped counts as active, including Frozen.
  New-box recommendations also respect the remaining saved-box slots. These
  CPU/memory estimates are advisory snapshots, not admission guarantees or a disk
  capacity estimate. Shared-pool and host free space below 5 GiB produce warnings;
  image unpack, snapshots, Docker data and backup growth still need a disk check.
  The four-saved-box cap remains enforced for create, fork and recover.
- Stop terminates guest processes and retains disk. Resume requires explicit
  TTL. `extend --ttl` resets a running box's deadline from the current host time
  without restarting it; stopped boxes must use resume. Expiry stops rather than
  deletes, on the host timer's 15-second interval plus graceful shutdown time.
  Long lifecycle and backup transactions hold the expiry lock and delay checks.
  A host outage delays enforcement until
  startup. Stopped boxes occupy disk and do not reserve guest RAM.
- Destroy removes the instance and snapshots and confirms absence. It is
  mandatory completion for disposable runs. Never infer absence from SSH failure.
- Native Incus exec and file APIs travel over verified private host SSH. No
  public Incus listener, guest password or controller secret is injected.
  Interactive SSH is a terminal over host SSH and Incus execution.
- Guest root is mapped to an unprivileged host UID. Nesting and syscall
  interception support guest Docker; the host Docker socket is never mounted.
- The dedicated bridge allows DNS/DHCP and public outbound internet. Interface-
  scoped firewall rules reject guest access to host services, cloud metadata,
  private address ranges. A native nftables bridge rule blocks all peer frames
  on `rfbr0` without depending on bridge-netfilter sysctls. IPv6, including link-local peer traffic,
  is blocked on this bridge. Forwarding listeners bind controller
  loopback. Guest services must listen on the managed NIC, not guest loopback.
  The desktop is accessible through this private tunnel.
- Btrfs storage is bounded to a 60 GiB pool per host and supports efficient
  clones and snapshots. Do not claim strict per-box quota enforcement: nested
  subvolumes can evade qgroup accounting. Native compressed image cache lives
  outside this pool, so `doctor` still reports host disk.
- Snapshots, restores and forks require a stopped source for consistent disk
  contents. Restore rolls back disk contents only, retaining the destination's
  instance configuration, resource limits and eth0 MAC address. Fork copies files
  including guest secrets
  but receives a fresh Incus runtime identity and network identity.
- Detached commands are guest systemd units with journal output and an exit
  status. Stop terminates them; process checkpointing is not provided.
- Desktop is an on-demand Xvfb/Openbox/noVNC stack. Its dedicated X display
  has no login manager; clipboard exchange is available without a login-manager delay. Closing its tunnel does not
  stop the desktop service. It is full desktop access, not browser confinement.
- The development image omits coding-agent packages and account credentials.
  Operators can install their preferred agent. Managed agent orchestration is
  not implemented. Guest SSH advertises a fresh Ed25519 host key.
- `cancel` stops an ordinary detached command's systemd control group.
- `usage` reports running-guest cgroup memory, cumulative CPU microseconds,
  effective CPU affinity and shared-pool filesystem space. Disk values are not
  per-box accounting and this command does not report billing.
- `backup` exports a stopped box, including snapshots, to a controller archive
  using native portable Incus export. The destination is created atomically and
  must not already exist. Owner-only mode `0600` is enforced before export
  starts; storage without private permission and hard-link support is rejected.
  `recover` imports an archive under a fresh qualified
  handle into the selected host, obeys admission, clears the old expiry and
  gives eth0 a new MAC address. It leaves the box stopped. Recovery never
  overwrites an existing box. Archives can
  contain secrets and must be stored privately; no scheduled upload is implied.
  Import only trusted Raft exports; native archive configuration is not sanitised.

Raft only manages its own labelled workspaces. VM deployment is not implemented.
