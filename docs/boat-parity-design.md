---
title: Design decisions
---

# Architecture and references

Raft is an independent open-source alternative to [boat.dev](https://boat.dev). It pairs an operator CLI with open-source infrastructure without redistributing Boat software or credentials.

## Runtime

[Incus](https://github.com/lxc/incus) manages unprivileged system containers, images, snapshots, clones, and file transfers on native ARM64 and AMD64 Ubuntu hosts with Debian 13 guest images. Containers share the host kernel. KVM is not required, and VM deployment is not implemented.

Host SSH authenticates the operator. Incus listens strictly on its local Unix socket without a public management API. Guest systemd manages detached jobs and the on-demand Xvfb, Openbox, and noVNC desktop. A host timer enforces running TTL.

## Image distribution

Build clean images from upstream Debian bases using the provided builder, then pin the resulting 64-hex fingerprint in controller configuration. The native image workflow attaches temporary templates after build and E2E checks pass (see [Boot performance](./boot-performance.md)).

Reference projects and comparison surfaces:

- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox): Self-hosted sandbox patterns and runtime images.
- [Incus first steps](https://linuxcontainers.org/incus/docs/main/tutorial/first_steps/): Container lifecycle, image servers, and snapshots.
- [Incus backups](https://linuxcontainers.org/incus/docs/main/howto/instances_backup/): Portable container export format.
- [Boat CLI reference](https://docs.boat.dev/cli-reference): Parity comparison surface. Raft tests verify Raft behavior, not Boat service conformance.

See [Runtime contract](./contract.md), [Boat feature parity](./boat-capabilities.md), and [Verification](./verification.md) for behavior and limits.
