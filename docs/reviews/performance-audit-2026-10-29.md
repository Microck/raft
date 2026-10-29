# Performance and discovery audit, 2026-10-29

## Scope and execution

The user approved performance fixes, confirmed dead-code removal, documentation
corrections, a discovery-driven Raft skill, current Boat parity research, and
verification in disposable task-owned workspaces. The follow-up request
authorizes committing, pushing, opening a ready-for-review PR and addressing
review feedback until ready. Production deployment, merging, releases and paid
provisioning remain outside scope.
Paid provisioning/research spending: not authorized.

Initial checkout was clean at `1fd6d62f`; Jujutsu was initialized colocated.
Primary agent: gpt-6-astra, high reasoning effort. Three read-only simplification
reviewers used gpt-6.1-sol with default reasoning. All reviewer jobs are complete.

## Delivered changes

- List and capacity collect their data in one SSH connection per host. Doctor
  runs its sequential checks in one connection and retains the worker lock.
  Existing lifecycle and archive safety checks remain in place.
- Config and doctor accept a location filter, so an unrelated offline host does
  not prevent inspection. Location-filtered default edits are refused.
- Command help explains behavior and operational constraints. All 38 command
  help paths ran without configuration or writes to an empty controller home.
- The skill teaches version/help discovery, source lookup, host inspection,
  ownership and cleanup rather than maintaining a command/software catalog.
  The installed skill symlink now targets this repository's canonical skill;
  the old openboat checkout was not edited. Discovery found run in the checkout
  while the installed 0.6.0 command tree correctly omitted it.
- README examples select persistence explicitly and provide ownership notes.
  Sizing, release notice timing and unreleased run status are corrected.
- The Boat comparison was refreshed from current official documentation,
  including team sharing, environment versions, AI proxying, telemetry,
  automatic snapshots and retained command history.

## Measurements and verification

[Raw timings](../benchmarks/read-latency-2026-10-29.json) record five samples per
command/version on the same ARM64 Incus 6.0.6 host. Median reductions were 44%
for list, 43% for capacity and 76% for doctor. These are controller-visible read
latencies, not boot or guest workload speedups. Update checks were disabled.

Passed in the disposable workspace:

- Ruff lint and format checks across all Python sources and deployment scripts.
- Limits, image fixtures, CI orchestration and feature verifiers.
- Self-update verification through real temporary npm and uv installations.
- Source, wheel and npm packaging and isolated installation checks.
- Website Oxlint, Next.js build including TypeScript, search tests, and 37-page
  export/link/search/download verification with 52 local links.

Real host checks also passed: batched versus independent native inventory and
policy reads; doctor and config scoped away from an unreachable inventory entry;
refusal of location-scoped controller-default edits without creating a file.
The discovery-guided one-shot job returned exit 7, collected its result file and
confirmed cleanup. No new mocks were introduced.

The initial test source archive omitted images and Git checkout metadata.
Correcting that disposable fixture fixed packaging and checkout-detection tests;
no assertions were weakened. A self-update HTTP timeout fixture logged an
expected broken pipe after its client disconnected; the verifier passed.

## Cleanup and review

Task-owned workspaces `paris:rf-bf5bab8180464898` and
`paris:rf-61cc02e89fc54677` are confirmed absent. Paris inventory was empty after
cleanup. No background verification jobs or delegates remain active.

The reuse and efficiency reviewers reported no actionable findings. The quality
reviewer found two duplicated-constant sites; both now use the existing project
and storage constants. Superseded per-call SSH code was removed. Ruff and a
structural/reference scan found no additional safely removable Python functions;
graph candidates were parser callbacks, tests or framework entry points.

The passive update notice still has its documented bounded wait. Removing it
would change whether short commands display release notices. Metadata checks
before mutations remain because they establish ownership and eligibility.

## Evidence limits

- Timings are a five-sample exploratory result from one host and network path.
- Boat capabilities were checked against official documentation, not its live
  infrastructure. Dedicated organization docs describe sharing that older
  overview/API text still describes as creator-private; the comparison notes it.
- Authenticated GitHub inspection confirmed v0.6.0 remains the latest published
  release. These changes and run are development-branch work, not deployed.
- The shared fff index returned stale and unrelated results. Exact local reads
  and the freshly built home-ubuntu-raft graph supplied code evidence instead.
- An unrelated configured host was unreachable; no configuration was changed.
