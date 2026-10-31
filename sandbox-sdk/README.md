# @microck/sandbox-sdk-raft

A [Sandbox SDK](https://sandbox-sdk.app) provider for [Raft](https://github.com/Microck/raft).
`createSandbox` makes a Raft box on your own host, and the rest of the Sandbox SDK API
(`run`, files, processes, git, agent tools) works on it unchanged.

```sh
npm install @opencoredev/sandbox-sdk @microck/sandbox-sdk-raft
```

```ts
import { createSandbox } from "@opencoredev/sandbox-sdk";
import { raft } from "@microck/sandbox-sdk-raft";

await using box = await createSandbox(raft({ location: "lab" }));
const result = await box.run("node --version");
console.log(result.stdout);
```

## Requirements

The adapter runs the `raft` CLI on the same machine, so it uses the hosts, SSH access and image
pins Raft already has. Set up Raft first with the [setup guide](../docs/operations.mdx), then check
that `raft new --location lab` works. The adapter needs Raft 0.7.1 or later, which forwards
stdin through `raft exec`, and Node.js 22 or later.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `location` | required | Raft location that creates, lists and finds boxes |
| `ttlSeconds` | `3600` | Box lifetime when `createSandbox` gets no `timeoutMs` |
| `owner` | `"sandbox-sdk"` | `owner` field of the box note |
| `next` | `"Destroy when the sandbox-sdk caller is done"` | `next` field of the box note |
| `bin` | `"raft"` | Raft executable |

`timeoutMs` sets the box lifetime in whole seconds, from 60 s to 30 days. When the lifetime
ends, Raft stops the box and keeps its disk; `raft resume` or `raft destroy` handle it after
that. Boxes are persistent, so `close()` leaves a keyed box for the next request and
`destroy()` deletes it.

Sizes map to whole CPUs and GiB: `small` is 1 CPU and 2 GiB, `medium` 2 CPUs and 4 GiB,
`large` 4 CPUs and 8 GiB. A custom `{ cpu, memoryMb, diskGb }` needs `memoryMb` in multiples
of 1024; `diskGb` gives the box its own disk and needs a host deployed with `--disk-pool`.
`from` takes a Raft snapshot, `LOCATION:BOX/SNAPSHOT`.

## How it maps to Raft

- **Keys and labels** live in the box note: its `purpose` is `sandbox-sdk` followed by the key
  and labels as JSON. `raft list` shows them. Replacing the note with `raft note` hides the box
  from `find` and `list`.
- **Commands** run through `raft exec`. The command and its environment travel on stdin, so
  secrets stay out of SSH and sudo command lines. A timeout or abort kills the command's whole
  process group in the guest.
- **Previews** open a private `raft forward` tunnel and return a `http://127.0.0.1:PORT/` URL
  that works only on this machine. Closing or destroying the sandbox closes its tunnels.
- **Network policy, idle timeouts, pause and snapshots** are not offered. Raft cannot restrict a
  box's network, and its stop and snapshot do not keep running processes. `createSandbox`
  rejects `network` limits and `idleTimeoutMs` with `unsupported` instead of ignoring them.

Every exec is one SSH round trip through the CLI, about 0.9 s on a nearby host. An SSH
`ControlMaster` for the host cuts the connection setup out of that.

## Testing

`npm run test:live` runs the Sandbox SDK conformance suite against a real host. It creates and
destroys boxes.

```sh
RAFT_LOCATION=lab npm run test:live
```
