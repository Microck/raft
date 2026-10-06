# Full-code review: 2026-10-06

Reviewed snapshot: `198bb1dfad60630b47a8fe40f4cd69e7d5c60162`.
Scope: the full CLI, deployment scripts, image build, verification tools, services,
workflows, packaging and agent skill. Two independent read-only reviews used
`docs/contract.md` as the specification and the applicable coding instructions as
standards. Findings below refer to this pinned snapshot, before documentation-site changes.

This is a code review, not a claim that every runtime path passed a new E2E run.
No host configuration changed during the original review.
The six distinct findings have since been fixed; see the follow-up verification below.

## Standards

### P1: stopped-state checks happen outside the lifecycle lock

Locations: [raft.py](../../raft.py), snapshot line 661, restore line 669,
fork line 675 in the reviewed snapshot.

Each command checks the previously fetched box status before acquiring the host
lifecycle lock. A concurrent resume can start the source before snapshot, restore
or copy runs. This violates the documented stopped-source requirement and the
correctness rule that validation and enforcement must agree.

Check the current state inside the same locked transaction as the operation.
The backup path already follows this approach. Evidence: code inspection of the
status read, branch and lock boundary; the race was not reproduced on a live host.

### P1: repeat deployment does not apply changed firewall rules

Locations: [deploy/deploy.py](../../deploy/deploy.py), lines 145-146;
[raft-network.service](../../deploy/raft-network.service), line 9.

Deployment overwrites the firewall script, then calls `systemctl enable --now`.
The oneshot service has `RemainAfterExit=yes`. Starting an already active service
does not rerun its script, so updated rules remain unapplied while deployment
reports that the private bridge is ready. Apply and verify the installed rules
on redeployment. Evidence: code and unit inspection; no host service was restarted.

### P1: firewall ordering does not gate guest autostart

Location: [raft-network.service](../../deploy/raft-network.service), line 3.

The firewall service runs after Incus. Incus can restore running guests before
filtering is installed. No dependency gates guest startup on the firewall.
The existing reboot checks wait for the firewall to become active before probing,
so they cannot detect exposure during startup. Install persistent filtering before
guest autostart or explicitly gate startup on it. This is a structural risk found
by inspection, not a demonstrated boot-time exploit.

### P2: native E2E workflow misses deployment and expiry changes

Location: [images.yml](../../.github/workflows/images.yml), push path filter.

Changes to `deploy/deploy.py`, `deploy/raft-expire.py` and the expiry service/timer
do not trigger native fresh-host, reboot and TTL checks. Package checks do not
exercise those host behaviors. Include these sources in the native workflow's
trigger list. Evidence: comparison of source ownership with the workflow paths.

No separate heuristic refactor was warranted by the review.

## Spec

### P1: stopped-source requirement can be bypassed by concurrent resume

Locations: [raft.py](../../raft.py), snapshot, restore and fork branches.

Contract: “Snapshots, restores and forks require a stopped source for consistent disk contents.”

The stopped check is outside the lifecycle lock. A concurrent resume can invalidate
it before disk operations begin. Validate stopped state inside the locked operation.
Evidence: code inspection; no live race experiment.

### P1: redeployment can leave the network contract unenforced

Locations: [deploy/deploy.py](../../deploy/deploy.py) and
[raft-network.service](../../deploy/raft-network.service).

Contract: “firewall rules reject guest access to host services, cloud metadata, private address ranges.”

`enable --now` does not reapply changed firewall rules when the persistent oneshot
is already active. A redeployed host can retain obsolete filtering. Reapply and
verify rules before reporting success. Evidence: code and service inspection.

### P2: clean-image inspection misses non-root SSH credentials

Location: [verify-image.py](../../deploy/verify-image.py), credential check near line 35.

Contract: “The development image omits ... account credentials.”

The inspector rejects `rootfs/root/.ssh/` but accepts a private-key path such as
a private key beneath a non-root user’s `.ssh` directory. It also skips nonregular archive entries
before inspecting credential paths. A real temporary tar fixture containing the
non-root private-key path passed verification and printed `Clean image verified`.
This proves a gap in the inspector, not that a published image contains credentials.
Inspect SSH credential paths across user homes and relevant archive entry types.

### P2: an oversized existing owned pool passes deployment validation

Location: [deploy/deploy.py](../../deploy/deploy.py), pool validation near line 108.

Contract: “Btrfs storage is bounded to a 60 GiB pool per host.”

Validation checks the Btrfs driver but not the configured pool size. An existing
owned pool configured above 60 GiB passes the shown checks. Reject contract drift
with explicit recovery instructions rather than silently altering storage.
Evidence: code inspection, not a destructive live pool-resize test.

## Counts

Standards: four findings, with P1 lifecycle consistency and firewall enforcement
as the highest severity. Spec: four findings, with P1 lifecycle consistency and
firewall enforcement as the highest severity. The two axes intentionally retain
overlapping findings rather than combining their counts.

## Documentation-site follow-up

The new site received separate standards and spec follow-up reviews. Three site
issues were corrected before delivery: bare Markdown links opening raw text,
a repository workflow link resolving to an absent local route, and missing
`Vary: Accept` on HTML responses. Canonical document links now begin with `./`,
source-file links use GitHub, and both negotiated representations set the header.

Verification: production build, strict TypeScript and Oxlint passed. A real
production-server test checked nine pages, 16 local link targets, search results,
Markdown exports, JSON downloads, the root redirect and unknown-page 404s.
Browser checks covered desktop and 390-pixel mobile layouts, sidebar navigation,
Ctrl+K search and result navigation. Computed body colors were black and white;
each page had one H1 and browser error inspection was empty. Wide parity tables
scroll within their container rather than widening the mobile page.

The simplification pass ran in the parent context across reuse, quality and
efficiency. No behavior-preserving refactor was warranted. No runtime review
finding above was fixed, and no workspace host was modified by site checks.

## Runtime-fix follow-up

All six distinct findings are addressed by the follow-up patch. Both review axes
reported no additional actionable findings after inspecting the fixes and tests.

| Finding | Fix | Verification |
| --- | --- | --- |
| Stopped-source race | Ownership and state are checked inside the same locked transaction as snapshot, restore, fork and backup | Real queued snapshot/restore/fork operations reject a source started first under the host lock on two ARM64 hosts |
| Stale rules on redeploy | Reloadable firewall oneshot explicitly reapplies owned chains transactionally, preserving unrelated chains and the running Incus daemon | Isolated first/repeat application passed; fresh-host CI checks injected stale-rule removal, unrelated-chain retention and unchanged daemon start timestamp |
| Guest autostart before filtering | Incus requires the firewall and starts after successful installation, including socket activation | Installed dependency verified on two hosts; fresh-host CI tests failed filtering and post-reboot start timestamps |
| Missing native CI triggers | All `deploy/**` changes trigger the native workflow | Workflow path inspection; the fix push triggers both native architectures and the fresh-host job |
| Non-root image credentials | Check credential paths and link targets before skipping nonregular entries | 15 real tar cases passed, including generated non-root private keys, symlinks, hardlinks, FIFO entries and clean controls |
| Oversized owned storage | Reject an existing pool whose configured size differs from `60GiB`, with recovery guidance and no resize | Existing 60 GiB pools pass; fresh-host CI grows a disposable pool to 61 GiB and verifies rejection without resizing |

The extended lifecycle suite passed on two configured ARM64 hosts. Each completed
all 31 developer-tool checks, six sizing combinations, the deterministic race
cases, Docker, files, jobs, snapshots, forks, restore, private tunnels, desktop,
network restrictions and scheduled TTL stop/resume. Each removed its fixtures.
Lint, formatting, compilation and release archive/privacy checks passed.

Native ARM64/AMD64 rebuilds and fresh-host failure/reboot/storage checks run in CI.
Their final result is recorded after the workflow completes; local ARM64 checks
alone do not certify the disposable-host behavior.
