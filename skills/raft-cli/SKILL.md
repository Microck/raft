---
name: raft-cli
description: Create and operate private Linux workspaces with Raft. Discover the installed CLI, host policies and guest tools before choosing commands for builds, tests, files, jobs or desktop access. Hosted Boat uses its separate skill.
---

# Raft

## Discover before choosing a workflow

1. Run `raft --version` and `raft --help`. The installed command tree is the
   capability source. Do not infer support from Boat, a newer release, or this
   skill. Never update the CLI merely to make an example work.
2. Match the task to a command in that help. Read `raft COMMAND --help` and
   nested help until you reach the operation. Read its arguments, defaults,
   output and constraints before invoking it. Revisit help when the version
   changes or the task needs a different operation.
3. If help cannot answer a material question, inspect the matching installed
   source or that version's Raft command reference and runtime contract. In a
   checkout these are `docs/reference/cli.md` and `docs/contract.md`; published
   sources are at https://github.com/Microck/raft. Current website documentation
   can describe unreleased code. Report unsupported capabilities instead of
   inventing flags or silently using native Incus to bypass Raft.

This skill defines the operating procedure. Command help and runtime inspection
supply the changing feature set, sizes, limits and software inventory.

## Select a host and establish ownership

1. Read `raft locations` for configured hosts and operator notes. Follow those
   notes when choosing a location; do not change them to fit the task.
2. Read help for `config`, `capacity`, `doctor` and `list`. Inspect the chosen
   host's defaults, required note fields, retention, health, capacity and boxes.
   Use location filtering when supported so an unrelated offline host does not
   prevent inspection. On older versions without it, report the limitation;
   do not rewrite the operator's inventory.
3. Read existing box notes before using a box. A handle or familiar name does
   not establish ownership. Operate only on task-owned boxes unless authorized
   otherwise, and keep the exact location-qualified handle returned by creation.
4. Give each new box a purpose, owner and next action using the flags discovered
   in help. Update its note when the plan changes. Obey host policy errors;
   never weaken policy or defaults to get past them.
5. Size the box for the workload using the host's actual capacity and storage
   capabilities. Capacity is advice, not a box-count quota. Check free disk
   before large uploads, builds, snapshots or copies.

## Choose lifecycle and verify work

- For bounded one-shot work, check whether the installed CLI provides a command
  that owns creation, execution, collection and cleanup. Read its timeout and
  failure semantics before choosing it. Otherwise manage that lifecycle explicitly.
- For reusable work, select persistence explicitly and inspect retention.
  Stopping ends processes; it does not preserve memory. A disposable box can
  disappear on stop, and a persistent box can expire under stopped-box retention.
  Snapshots inside a box do not protect it against deletion of that box.
- Probe the needed guest service after creation; a returned handle does not
  prove readiness. Inspect tool versions inside the guest and its `/opt/raft/`
  inventory instead of assuming an image has every compiler, agent or login.
- Exec takes literal arguments. Use an explicit shell for shell syntax. Preserve
  the command's exit status and collect artifacts before cleanup, including on
  failure. After an uncertain transport failure, inspect the exact handle or job
  before rerunning a payload that might already have executed.
- For temporary work, confirm destruction even after a failed command. For
  reusable work, stop when finished and report the handle and retention deadline.
  An SSH error or an eventual TTL is not proof of immediate cleanup.

## Preserve the boundaries

Raft workspaces are native-architecture containers sharing the host kernel.
Do not promise VM isolation or cross-architecture execution. Inspect the actual
runtime before claiming any isolation or resource limit.

Host onboarding, deployment, upgrades and global cleanup affect more than a
single workspace. Perform them only within explicit authorization; inspect a
supported dry-run or preview first. Do not change daily-driver hosts or install
updates as a side effect of an ordinary workspace task.

Snapshots, forks, transfers and backups can carry credentials. Copy only the
required files and never inject host credentials without authorization. Git
filtering is not a secret scanner. Import only trusted backups and store them
privately. Restore only an explicitly requested rollback.

For a tunnel or desktop, follow current help for listener addresses and service
binding. Report the address actually printed. A private tunnel is not public
hosting, and closing it does not necessarily stop the guest service.
