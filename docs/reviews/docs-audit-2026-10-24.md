# Documentation audit — 2026-10-24

## Scope and comparison

Reviewed the 34 existing published-source documentation pages, including seven
release-note pages, and added three navigation hubs (37 pages total). Review
reports are deliberately excluded from the public site; the repository-only
`setup-explained.html` is not part of the site's generated routes.

Compared Boat's complete 206-entry documentation index and representative
quickstart, concepts, setup, snapshots, background tasks, CI, security, desktop,
hosting and troubleshooting material. This is a documentation comparison, not a
claim to have tested Boat's service or every one of its 206 pages.

Primary references: https://docs.boat.dev/llms.txt,
https://docs.boat.dev/quickstart, https://docs.boat.dev/concepts,
https://docs.boat.dev/long-running-tasks/background-commands,
https://docs.boat.dev/troubleshooting. The useful pattern is task-first pages and
progressive disclosure, not copying Boat's managed-service promises into Raft.

## Findings

1. The main sidebar mixed first-run tasks with architecture, publishing and test
   histories. New guide/maintainer/release hubs separate audiences without moving
   established page URLs.
2. Onboarding linked nonexistent 0.7.0 packages and images. GitHub's latest release
   and npm both reported 0.6.0; the v0.7.0 release endpoint returned 404. Stable
   examples now pin 0.6.0 and development-only CI is labeled and commit-pinned.
3. Several persistent tutorials inherited a disposable controller default, and
   one sequence resumed a box immediately before forking a source that must be
   stopped. The examples now state the required lifecycle explicitly.
4. Backup instructions asked for permissions on a destination that must not
   exist. Verified against raft.py: the temporary archive is created and checked
   privately before no-overwrite publication. Wording now matches that behavior.
5. Search flooded the dialog with repeated paragraphs from reference/history
   pages. Matching page titles now rank first, initial results show up to eight
   pages with two sections each, and an explicit action restores every match.

## Verification

- Production static build, type checking and lint passed.
- All 37 routes, local links and fragment targets, search index, Markdown exports,
  asset downloads and 404 behavior passed the exported-site checks.
- 120 shell examples passed Bash syntax checks. Help for new/fork/run was checked.
  This is not execution against Incus; no hosts, credentials or workspaces changed.
- Real cloud-browser interactions exercised navigation, advanced setup disclosure
  and search. The separate automated Chromium suite could not download a valid
  browser archive in this environment; no passing result is claimed for that suite.
- Historical benchmark and runtime evidence remains labeled with its original
  dates/limits. This docs-only work does not re-certify runtime behavior.

## Page-by-page disposition

| Page | Review / change |
| --- | --- |
| `boat-capabilities.md` | Renamed parity to comparison; added a short operating-model summary; folded the detailed matrix and marked run as preview. |
| `boat-parity-design.md` | Retained architecture rationale; added description and grouped under Maintainers. |
| `boot-performance.mdx` | Retained dated samples and limitations; added search description and moved under Reference. |
| `contract.md` | Marked maintainer audience and unreleased CI contract; clarified persistent-only stop retention; moved navigation into Reference. |
| `first-box.mdx` | Made persistence explicit; separated destructive cleanup from stop/resume; reduced the next-step list. |
| `guides/background-jobs.mdx` | Retained start/status/log/cancel flow and the important exit-status versus completion distinction. |
| `guides/backup-recovery.mdx` | Clarified persistent source and private archive creation; removed impossible pre-existing destination permission instruction. |
| `guides/ci-jobs.mdx` | Marked unreleased 0.7.0; replaced nonexistent npm installation with a reviewed full source commit in an isolated environment. |
| `guides/config-policy.mdx` | Retained defaults/host-policy separation and destructive-retention warnings; reachable from the operator guide group. |
| `guides/desktop-services.mdx` | Retained tunnel foreground behavior, binding requirements and exact noVNC path. |
| `guides/docker.mdx` | Retained daemon checks, runnable build example, storage guidance and pruning warning. |
| `guides/files.mdx` | Resolved running-only wording versus supported stopped single-file transfers. |
| `guides/index.mdx` | Added a task-oriented Develop / Automate / Operate hub. |
| `guides/long-running-services.mdx` | Kept restart semantics and TTL limits; made the persistent creation example independent of controller defaults. |
| `guides/project-setup.mdx` | Kept the upload/setup/template sequence; made persistent template source explicit. |
| `guides/scripting-ci.mdx` | Kept output/exit-code tables and retry safety; made persistent cleanup example explicit. |
| `guides/snapshots-forks.mdx` | Warned before stopping disposable sources and corrected the sequential resume-then-fork example. |
| `guides/troubleshooting.mdx` | Retained symptom/error sections and safe no-wildcard recovery guidance; checked command references and anchors. |
| `guides/upgrades.mdx` | Updated stale 0.2.0 pins to published 0.6.0; kept component separation and backup/redeploy warnings. |
| `index.mdx` | Replaced the 12-row task catalogue with a short setup path and focused task links; explicit persistent example and release status. |
| `maintainers/index.mdx` | Added an audience-specific hub instead of four competing top-level implementation links. |
| `operations.mdx` | Pinned real 0.6.0 assets/packages; kept guided onboarding primary and folded manual steps without changing URLs or anchors. |
| `publishing.md` | Retained maintainer release gates; added description and moved out of the onboarding navigation. |
| `reference/capacity.mdx` | Corrected fork-after-resume ordering and persistent source; retained detailed accounting as reference, not onboarding. |
| `reference/cli.md` | Kept the complete command lookup and anchors; labeled run as unreleased rather than a stable command. |
| `reference/configuration.mdx` | Retained schema, required image fingerprint, precedence and SSH behavior; checked examples and links. |
| `releases/0.2.0.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.2.1.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.3.0.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.4.0.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.5.0.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.6.0.md` | Retained historical release content; added an explicit historical description and a release-notes navigation home. |
| `releases/0.7.0.md` | Marked as unreleased; removed claims that packages/images already exist. |
| `releases/index.mdx` | Added explicit published versus unreleased status and links to historical notes. |
| `requirements.mdx` | Clarified product caps versus real CPU/RAM/storage limits; kept the dedicated-host warning. |
| `site-development.md` | Retained build/export guidance; added description and grouped under Maintainers. |
| `verification.mdx` | Corrected obsolete shared-pool-only statement; retained dated evidence and uncertainty; grouped under Maintainers. |
