<p align="center">
  <img src=".github/assets/raft-logo.png" width="220" alt="Raft logo">
</p>

# Raft

<a href="https://github.com/Microck/raft/releases"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/release/Microck/raft.svg?variant=secondary&amp;size=xs&amp;logo=false&amp;mode=dark"><img src="https://shieldcn.dev/github/release/Microck/raft.svg?variant=secondary&amp;size=xs&amp;logo=false&amp;mode=light" alt="release"></picture></a>
<a href="https://github.com/Microck/raft/actions/workflows/check.yml"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/ci/Microck/raft.svg?workflow=check.yml&amp;branch=main&amp;variant=secondary&amp;size=xs&amp;logo=false&amp;mode=dark"><img src="https://shieldcn.dev/github/ci/Microck/raft.svg?workflow=check.yml&amp;branch=main&amp;variant=secondary&amp;size=xs&amp;logo=false&amp;mode=light" alt="ci status"></picture></a>
<a href="LICENSE"><picture><source media="(prefers-color-scheme: dark)" srcset="https://shieldcn.dev/github/license/Microck/raft.svg?variant=secondary&amp;size=xs&amp;logo=false&amp;mode=dark"><img src="https://shieldcn.dev/github/license/Microck/raft.svg?variant=secondary&amp;size=xs&amp;logo=false&amp;mode=light" alt="license"></picture></a>

Run persistent Linux boxes on Ubuntu hosts you control. You can run commands and Docker builds, copy files, save snapshots, and open a private desktop. Boxes are unprivileged Incus containers sharing the host kernel, not VMs.

Raft is an independent, self-hosted alternative to [Boat](https://boat.dev), with no affiliation.

## Install

You need a Linux controller with Python 3.11.8+, npm and OpenSSH, plus a dedicated ARM64 or AMD64 Ubuntu 22.04 or 24.04 host. The host needs at least 2 CPUs, 8 GiB RAM, 80 GiB free disk, key-based SSH and passwordless sudo. Use trusted workloads only: your SSH identity controls the host.

```sh
npm install -g @microck/raft
raft --version
```

## Create your first box

Replace the SSH target with your dedicated host. Verify its host key before accepting it:

```sh
ssh ubuntu@192.0.2.10 true
raft host add lab --ssh ubuntu@192.0.2.10 --dry-run
raft host add lab --ssh ubuntu@192.0.2.10
box=$(raft new --location lab --ttl 600 --persistent --purpose "Explore Raft" --owner "Local operator" --next "Destroy after exploring")
raft exec "$box" -- node --version
raft ssh "$box"
```

Exit the guest shell, then run `raft stop "$box"` on your controller. Your files remain, but processes stop. Run `raft resume "$box" --ttl 600` to start it again. A host retention policy can delete stopped boxes; `raft config --location lab` shows that policy.

When you no longer need the files or snapshots, delete only this box with `raft destroy "$box"`. Disposable boxes delete on stop or expiry instead.

## Read the docs

[Set up your host](https://raft.micr.dev/docs/operations/) · [Find a task](https://raft.micr.dev/docs/guides/) · [Look up a command](https://raft.micr.dev/docs/reference/cli/) · [Troubleshoot](https://raft.micr.dev/docs/guides/troubleshooting/)

For automation, see the [agent skill](skills/raft-cli/SKILL.md) and [Sandbox SDK provider](sandbox-sdk/README.md). For development and test evidence, see the [runtime contract](docs/contract.md) and [verification guide](docs/verification.mdx).

[MIT license](LICENSE). Third-party tools retain their own licenses.
