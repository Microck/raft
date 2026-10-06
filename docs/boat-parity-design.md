# Architecture and references

Raft is an independent open-source alternative to [boat.dev](https://boat.dev).
It combines an operator CLI with maintained open-source infrastructure. It is
not affiliated with Boat and does not redistribute Boat software or credentials.

## Runtime

[Incus](https://github.com/lxc/incus) manages unprivileged system containers,
images, snapshots, copies and file transfer. Containers share the host kernel.
The supported deployment uses native ARM64 or AMD64 Ubuntu hosts and Debian 13 guest images.
KVM is unnecessary for these containers. Incus supports virtual machines, but
Raft's current deployment does not implement a VM workflow.

Host SSH authenticates the operator. Incus listens through its local Unix socket;
Raft does not expose a public management API. Guest systemd runs detached jobs
and the on-demand Xvfb/Openbox/noVNC desktop. A host timer enforces running TTL.

## Image distribution

Build a clean image from the public Incus Debian image using the checked-in
builder. Pin the resulting immutable fingerprint in controller configuration.
The native image workflow attaches short-lived clean ARM64 and AMD64 templates
after its build and E2E checks pass. See [image validation and boot performance](boot-performance.md).
Package versions are recorded inside each image; Debian package updates can
change subsequent builds.

The reference projects separate installation, image construction and examples:

- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) documents a
  self-hosted quick start and runtime images.
- [Incus first steps](https://linuxcontainers.org/incus/docs/main/tutorial/first_steps/)
  explains image servers, containers and snapshots.
- [Incus backups](https://linuxcontainers.org/incus/docs/main/howto/instances_backup/)
  documents portable instance exports.
- [Boat CLI reference](https://docs.boat.dev/cli-reference) provides the comparison
  surface. Raft tests verify Raft behavior, not Boat service conformance.

See [the contract](contract.md), [capability table](boat-capabilities.md) and
[verification](verification.md) for implemented behavior and limits.
