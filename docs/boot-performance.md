---
title: Boot performance
---

# Native images and boot performance

Raft builds Debian 13 system-container images for native ARM64 and AMD64 hosts.
The installer, base-image selection and Node archive follow the host package
architecture. These are separate image fingerprints, not emulation or a single
architecture-independent root filesystem.

## Build and verify

Use the normal image builder on each native host. To keep an existing image,
pass a fresh alias such as `--alias raft-dev-next`. Creating the candidate does
not change the immutable fingerprint in your controller configuration. After
publication, the builder initializes and deletes a private cache-preparation
instance. This prepares Incus's optimized image volume before the builder
reports readiness. Imported archives can still have a slow first unpack.

The [native images workflow](https://github.com/Microck/raft/blob/main/.github/workflows/images.yml) runs on GitHub's
native `ubuntu-24.04` and `ubuntu-24.04-arm` VMs. It builds each image, runs the
extended lifecycle suite and all 31 development-tool checks, measures startup
and exports the clean template with SHA256 checksums. Successful runs attach
seven-day artifacts named `raft-dev-amd64` and `raft-dev-arm64`. Small
`boot-results-amd64` and `boot-results-arm64` artifacts contain just the reports. GitHub artifact
downloads require a GitHub login. Versioned public images are published under
[releases](https://github.com/Microck/raft/releases) after recovery and fresh-host
validation, with checksums, manifests and inventories. Release assets do not
expire on the CI artifact schedule. Each archive must be imported into a host
of the same architecture.

The CI fixture uses a sparse Btrfs pool on an ephemeral VM. It does not certify
the production deployer's 80 GiB free-space preflight or fresh-host provisioning.
The builder omits coding-agent binaries. Existing pinned images retain their
previous contents until you explicitly select another fingerprint.

## Benchmark method

```sh
python3 deploy/benchmark-boot.py --location lab --samples 5 --desktop > boot.json
```

The benchmark creates and destroys only its own boxes. Each sample measures new
creation, then stops and resumes that same box. Guests have one CPU and 2 GiB RAM.
It reports CLI return, successful command execution and a successful Docker API
response, all measured from the start of the controller request. Desktop timing
starts with its separate on-demand request and requires both noVNC HTTP and a
real VNC protocol greeting. The report includes median, minimum, maximum and
nearest-rank p95, SSH round-trip time, image size and systemd's boot critical chain.

The image is already downloaded. A first use after import or cache eviction can
still unpack Incus's optimized storage volume; newer reports record whether
that volume exists at the start. The builder explicitly prepares this volume.
These numbers do not measure image download, a hard host reboot, process-memory
restoration or remote browser rendering. Small samples are exploratory; p95
from three or five samples is effectively their maximum.

## Baseline measured on 2026-10-06

These are three-sample medians from two ARM64 hosts with two host CPUs. Both
already had their optimized image volumes. Anonymous reports retain the raw
samples: [host A](benchmarks/arm64-baseline-a.json) and
[host B](benchmarks/arm64-baseline-b.json).

| Controller-visible operation | ARM64 host A | ARM64 host B |
| --- | ---: | ---: |
| Create returns handle | 0.864 s | 2.906 s |
| Create accepts a command | 1.918 s | 6.936 s |
| Create has Docker ready | 3.941 s | 10.753 s |
| Resume returns | 1.056 s | 3.889 s |
| Resume accepts a command | 1.851 s | 8.213 s |
| Resume has Docker ready | 3.917 s | 11.653 s |
| Desktop starts after create | 2.602 s | 7.631 s |
| Desktop starts after resume | 2.430 s | 7.669 s |
| Separate host SSH request | 0.397 s | 3.556 s |

Different transport latency dominates much of this difference. It is not an
ARM64 versus AMD64 comparison. A host in a different network or a GitHub runner
is not a matched hardware benchmark. No hosted Boat speed comparison was run.

## Measured changes on the same ARM64 host

- Only the advertised Ed25519 SSH host key is generated on first boot. The
  baseline generated unused RSA and ECDSA keys too; host A's SSH unit took 1.898 s.
- Desktop startup waits for a real X server response instead of sleeping for a
  fixed second. It still fails explicitly if Xvfb never becomes ready.
- Coding-agent packages are omitted from new images. The candidate ARM64 image
  is 1,920,185,312 bytes, versus 2,626,705,196 bytes for the baseline. This is
  about 27% smaller; steady-state boot improvement must be measured separately.
- The builder prepares the optimized image volume. The candidate's first use
  before this preparation took 82.1 s. Preparation pays that cost during setup,
  while retaining the same immutable image and normal creation path.

Three samples per image on host A give the following comparison. The
[candidate report](benchmarks/arm64-candidate-a.json) preserves all samples.

| Measurement | Baseline | Candidate |
| --- | ---: | ---: |
| Compressed image | 2.45 GiB | 1.79 GiB |
| SSH unit, first sample | 1.898 s | 0.202 s |
| Create has Docker ready, median | 3.941 s | 4.302 s |
| Resume has Docker ready, median | 3.917 s | 3.973 s |
| Desktop startup inside guest, median | 1.431 s | 0.537 s |

Warm Docker readiness remains about four seconds. These measurements do not
show an overall boot speedup. SSH key generation and desktop startup improved.
The desktop comparison measures three real stop/start cycles inside each guest,
from `systemctl start` until HTTP 200 and an RFB greeting. It excludes SSH delay.
Its [raw report](benchmarks/arm64-desktop-local.json) shows about 62% less startup
time. Controller-visible desktop measurements include transport overhead and
show a smaller change.

The candidate passed the full lifecycle and headed-browser E2E suite on an
Ubuntu 22 ARM64 host. Native Ubuntu 24 ARM64 and AMD64 also passed the extended
suite with all 31 tool checks and six resource combinations. Fresh-host testing
found and fixed Chromium's user-namespace policy and peer frames bypassing IP
filtering. New images load a guest-local Chromium AppArmor rule; the host
firewall filters Raft peer frames directly at the bridge hook.

## Native CI comparison

[Verified native run](https://github.com/Microck/raft/actions/runs/37447415532),
2026-10-06. Five samples per architecture, four host CPUs, one guest CPU and
2 GiB guest RAM. Both optimized image volumes existed before sampling.

| Controller-visible median | ARM64 runner | AMD64 runner |
| --- | ---: | ---: |
| Create returns handle | 0.425 s | 0.659 s |
| Create accepts a command | 0.866 s | 1.375 s |
| Create has Docker ready | 2.280 s | 3.035 s |
| Resume accepts a command | 0.951 s | 1.489 s |
| Resume has Docker ready | 2.626 s | 3.049 s |
| Desktop starts after create | 1.115 s | 1.592 s |
| Compressed image | 1.79 GiB | 1.86 GiB |

These are different runner hardware and transport measurements. They do not
establish that ARM64 is inherently faster. Raw samples and p95 are in the
[ARM64 report](benchmarks/arm64-native-ci.json) and
[AMD64 report](benchmarks/amd64-native-ci.json). Committed reports omit verbose
systemd unit traces; downloadable CI reports retain them.

Native CI validates builds, real workspaces and clean exports. Portable recovery
between different architectures is not tested or supported.
