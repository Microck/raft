---
title: Boot performance
---

# Native images and boot performance

Raft builds Debian 13 system container images for native ARM64 and AMD64 hosts. The installer, base image, and Node archive match the host CPU architecture. These are separate native images, not emulation layers.

## Build and verify

Run the image builder on each native host. To preserve an existing image, specify a new alias such as `--alias raft-dev-next`. Building a candidate does not modify the pinned fingerprint in controller configuration.

After publication, the builder creates and deletes a cache-preparation instance to populate Incus's storage volume before reporting readiness. Imported archives or use after cache eviction can still require a slow first unpack.

The [native images workflow](https://github.com/Microck/raft/blob/main/.github/workflows/images.yml) runs on GitHub `ubuntu-24.04` and `ubuntu-24.04-arm` runners. It builds each image, runs extended lifecycle tests and all 31 developer tool checks, measures startup, and exports clean templates with SHA-256 checksums.

Successful CI runs attach seven-day artifacts (`raft-dev-amd64` and `raft-dev-arm64`), which require a GitHub login to download. Small `boot-results-amd64` and `boot-results-arm64` artifacts contain only reports.

Versioned public images are published under [releases](https://github.com/Microck/raft/releases) after recovery and fresh-host validation; public release assets do not expire on the seven-day CI schedule. Each archive must be imported into a host of the matching CPU architecture.

The CI runner uses a sparse Btrfs pool on an ephemeral VM and does not certify the 80 GiB production free-disk preflight. Pinned images retain their contents until you configure a new fingerprint.

## Benchmark method

```sh
python3 deploy/benchmark-boot.py --location lab --samples 5 --desktop > boot.json
```

The benchmark creates and destroys dedicated test boxes with 1 CPU and 2 GiB RAM. Each sample measures creation, then stops and resumes the same box. It records CLI return, command execution, and Docker API readiness from request initiation.

Desktop timing measures on-demand requests requiring both noVNC HTTP 200 and an RFB protocol greeting. Reports include median, minimum, maximum, nearest-rank p95, SSH round-trip time, image size, and systemd boot critical chain.

Measurements assume the image is pre-downloaded. A first use after import or cache eviction can still unpack Incus's optimized storage volume. Timings are software-dependent, varying with installed packages and services, image state, host hardware, load, and transport overhead.

Small sample sets are exploratory; with three or five samples, nearest-rank p95 is effectively their maximum. They do not measure image downloads, host reboots, process-memory restoration, or remote browser rendering.

## Baseline measured on 2026-10-06

Three-sample medians from two 2-CPU ARM64 hosts with pre-warmed image volumes. Raw samples are preserved in [host A](benchmarks/arm64-baseline-a.json) and [host B](benchmarks/arm64-baseline-b.json).

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

Network transport latency dominates the delta between hosts. This is not an ARM64 versus AMD64 benchmark, nor a comparison against hosted Boat.

## Measured changes on the same ARM64 host

- **SSH key generation**: Generates only the advertised Ed25519 host key on first boot, reducing SSH unit startup on host A from 1.898 s to 0.202 s.
- **Desktop polling**: Waits for an active X server response rather than sleeping for a fixed second.
- **Image footprint**: Omits coding-agent packages. The candidate ARM64 image is 1,920,185,312 bytes versus 2,626,705,196 bytes for baseline (about 27% smaller).
- **Volume pre-warming**: Pre-warming the storage volume avoids an initial 82.1 s unpack delay on first use.

Three samples per image on host A give the following comparison. The [candidate report](benchmarks/arm64-candidate-a.json) preserves all samples.

| Measurement | Baseline | Candidate |
| --- | ---: | ---: |
| Compressed image | 2.45 GiB | 1.79 GiB |
| SSH unit, first sample | 1.898 s | 0.202 s |
| Create has Docker ready, median | 3.941 s | 4.302 s |
| Resume has Docker ready, median | 3.917 s | 3.973 s |
| Desktop startup inside guest, median | 1.431 s | 0.537 s |

Warm Docker readiness remains about 4 seconds. These results do not show an overall warm boot speedup; SSH key generation and desktop startup improved.

Desktop benchmarks measure three internal stop/start cycles inside the guest from `systemctl start` to HTTP 200 and RFB greeting, showing about 62% less startup time in [local measurements](benchmarks/arm64-desktop-local.json). Local desktop timing excludes SSH delay; controller-visible timing includes network transport overhead and shows a smaller change.

The candidate passed lifecycle and headed-browser E2E suites on Ubuntu 22 ARM64, and extended suites with all 31 tool checks on Ubuntu 24 ARM64 and AMD64. Fresh-host testing resolved Chromium user-namespace policies and bridge peer filtering.

## Native CI comparison

[Verified native run](https://github.com/Microck/raft/actions/runs/37447415532), 2026-10-06. Five samples per architecture on 4-CPU runners with 1 guest CPU and 2 GiB guest RAM:

| Controller-visible median | ARM64 runner | AMD64 runner |
| --- | ---: | ---: |
| Create returns handle | 0.425 s | 0.659 s |
| Create accepts a command | 0.866 s | 1.375 s |
| Create has Docker ready | 2.280 s | 3.035 s |
| Resume accepts a command | 0.951 s | 1.489 s |
| Resume has Docker ready | 2.626 s | 3.049 s |
| Desktop starts after create | 1.115 s | 1.592 s |
| Compressed image | 1.79 GiB | 1.86 GiB |

Raw samples and p95 distributions are recorded in [ARM64 report](benchmarks/arm64-native-ci.json) and [AMD64 report](benchmarks/amd64-native-ci.json). Measurements reflect runner hardware and transport differences, not inherent architecture performance.

Native CI validates builds, workspaces, and clean exports. Imports must match host CPU architecture; cross-architecture recovery is not supported.
