---
title: Publishing
---

# Publishing a clean source release

Public releases must contain only reusable code, examples, and documentation. Keep controller configuration, SSH keys, instance archives, journals, and private reports outside the repository.

## Release assets and image publishing

Release `main` through the `native images` workflow with a semantic `release_tag`, such as `v0.2.0`. The tag must match both Python and npm versions. Clean upstream Debian bases provide the images.

The workflow builds and inspects Python and npm packages, tests their installed executables outside the checkout, and publishes `@microck/raft` using the repository's `NPM_TOKEN` secret with npm provenance. Only then does it create the GitHub release with the wheel, source archive, npm tarball and verified image assets. Published versions are immutable; after partial publication, fix the failure and use a new patch version.

```sh
gh workflow run images.yml --ref main -f release_tag=v0.2.0
```

- **Validation gating**: Publication requires native lifecycle and full-image recovery jobs alongside fresh AMD64 KVM provisioning and reboot verification.
- **Asset immutability**: The workflow refuses existing tags and never overwrites release assets.
- **Provenance records**: Release assets include `SHA256SUMS`, architecture manifests, package inventories, and boot reports. Manifests identify the source commit, CI run, and Incus image fingerprint.

## Repository history hygiene

Scan **all Git history**, branches, and tags before changing repository visibility. A clean working tree does not remove sensitive data from commit history.

- **Manual audit**: Secret scanners do not catch every identifier. Inspect old commits for host addresses, usernames, local paths, and private reports.
- **Fresh repository snapshot**: If commit history contains sensitive data, publish a clean snapshot in a fresh public repository. History rewrites can leave orphaned commits accessible in GitHub cache (see GitHub guidance on [removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).
- **Keep source private**: Do not make an existing private repository public merely because its current working tree is clean.

## Release verification commands

Run security scanning, linting, formatting checks, and package builds before tagging:

```sh
gitleaks dir --redact --no-banner .
gitleaks git --redact --no-banner .
uv sync --locked
uvx ruff check raft.py deploy/*.py
uvx ruff format --check raft.py deploy/*.py
uv build
```

## Image distribution rules

Do not upload local workspace backups or development exports as release assets. Distributable images must be built from clean upstream bases, inspected for credentials, and accompanied by fingerprints, manifests, and licenses.

Review the package contents and capability table before release. State container isolation accurately without claiming VM isolation or exact Boat compatibility. Raft is an independent alternative to boat.dev and is not affiliated with that service.
