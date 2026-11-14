# Optional sandbox-sdk plugin

This executable implements Raft plugin protocol 1 with the published
`@opencoredev/sandbox-sdk` 1.0.3. Node.js and provider dependencies stay in this
directory, outside Raft's Python installation. Requires Node.js 22+ and pnpm
11.25.0. No build step is needed.

```sh
cd examples/sandbox-sdk
pnpm install --frozen-lockfile
pnpm smoke
```

The smoke uses the SDK's real `memory()` provider in one process and a test-only
filesystem Provider across separate adapter processes. It uses a fresh temporary
directory, real shell commands and real file transfers, then deletes only that
directory. It contacts no cloud provider or Docker daemon. The filesystem Provider
is not a security sandbox and must not be used for untrusted code.

## Choose a provider

Create an absolute-path ES module whose default export returns a SDK `Provider`.
The export may be async. It receives `options.providerOptions` as its only argument.
Any compatible provider can be selected; there is no adapter allowlist. Install
optional provider packages beside your factory when that provider requires them.
For example, a factory beside this README can select SDK's Docker provider:

```js
// /absolute/path/to/examples/sandbox-sdk/my-provider.mjs
import { local } from '@opencoredev/sandbox-sdk/local';
export default function createProvider(options) {
  return local(options);
}
```

This example does not install, start or configure Docker. Using it requires your
own authorized daemon and image. Other factories can return any SDK Provider.
Factories and their imported modules are trusted executable code. Keep credentials
in the provider's secret store or inherited environment, not in configuration.
Factories must not print logs on stdout, which carries protocol results.

Register the adapter, then set the registered location's options in Raft's
controller configuration before creating a box:

```sh
raft plugin add sdk -- node /absolute/path/to/examples/sandbox-sdk/adapter.mjs
```

```json
{
  "sdk": {
    "plugin": ["/absolute/path/to/node", "/absolute/path/to/examples/sandbox-sdk/adapter.mjs"],
    "options": {
      "factory": "/absolute/path/to/examples/sandbox-sdk/my-provider.mjs",
      "providerOptions": {},
      "operationTimeoutMs": 120000
    }
  }
}
```

`factory` is required for normal actions and must be an absolute module filename.
`providerOptions` defaults to `{}`. `operationTimeoutMs` defaults to 120000 and
bounds each whole invocation, including factory loading and handle cleanup.
Unknown adapter options fail. `describe` imports neither the factory nor the SDK,
needs no credentials, and only prints the static handshake.

```sh
box=$(raft new --location sdk --disposable)
raft list --location sdk
raft info "$box"
raft exec "$box" -- printf '%s\n' 'literal $argument'
raft upload "$box" ./input.txt /workspace/input.txt
raft download "$box" /workspace/input.txt ./output.txt
raft destroy "$box"
```

## Supported behavior

- `new` requires explicit `--disposable`. It passes `timeoutMs = ttl * 1000` to
  `createSandbox`; TTL defaults to 600 seconds. Omitted CPU and memory leave SDK
  defaults untouched. Explicit sizing requires both CPU and memory, maps GiB to
  SDK `memoryMb` using 1024 MiB per GiB, and requires the provider's `size`
  capability. Disk sizing, snapshot sources and box notes are rejected.
- `list` prints JSONL with `box`, `state`, `stopAt`, `deleteAt`, `disk` and `note`.
  Deadlines are empty and disk/note are null because the common SDK contract does
  not report authoritative deadlines or storage, or implement Raft's note template.
- `info` prints JSON metadata with the native ID, qualified handle, provider,
  labels and capabilities.
- `exec` requires a provider with a POSIX shell and `/workspace` working directory.
  It POSIX-shell-quotes every argv element and streams every text chunk from
  `Provider.exec` to its corresponding output stream. It returns the guest exit
  code. The SDK's higher-level `box.run()` iterator is deliberately not used:
  version 1.0.3 drops old chunks when its 1 MiB subscription queue fills. Stdin is
  inherited by the adapter, but the SDK's primitive closes guest stdin; interactive
  terminals and stdin forwarding are not supported. Detached exec is rejected.
- `upload` and `download` transfer one regular file as bytes, in memory. Guest
  paths must be absolute POSIX file paths without NUL or `..` components. Recursive
  transfers and directory/symlink downloads are rejected. An upload source must
  be a regular file. A download stages bytes in a private sibling directory and
  renames only after a successful read and handle close, leaving any existing
  destination unchanged on failure. Parent directories must already exist.
- `destroy` deletes only after checking both SDK and Raft location labels in
  filtered SDK metadata, then connecting and rechecking the handle. The first
  check matters because some providers expire/delete during connect. Every box
  operation applies these checks; list requests and independently filters labels.
  Labels prevent accidental cross-location access, not hostile access by someone
  who controls the provider account or trusted factory.

IDs are reversible UTF-8 base64url encodings of native provider IDs. Handles stay
on one line, and reconnect in separate CLI processes without a local ID registry.
Native provider state must itself survive processes. SDK's `memory()` does not.
Creation calls `close()` explicitly, not `await using`, which would destroy an
unkeyed sandbox. No persistent stop, resume, SSH, forwarding, snapshots, background
jobs or maintenance commands are advertised. Global `raft --json` is supported by
Raft's byte-stream event wrapper; the adapter ignores `args.json_output` metadata
and always writes normal command output, never nested event envelopes.

## Expiry and failure limits

`--disposable` acknowledges that this adapter cannot promise Raft's persistent
stop or Incus expiry worker. It is not proof of provider-side expiry. SDK providers
may enforce, cap, round, ignore or lazily check `timeoutMs`; check the selected
provider's implementation and account policy. Explicitly destroy boxes when done.
Cloud billing and cleanup remain the operator's responsibility.

In published SDK 1.0.3, Docker's `local()` stores a deadline in Docker labels and
checks it on `connect()`. Its `list()` does not expire containers and there is no
independent expiry worker in that implementation. A short-lived CLI process does
not stay alive to enforce TTL. Other versions or factories may use a client-owned
worker; such a worker dies with the CLI process unless separately supervised.
Do not rely on this example for unattended Docker expiry.

The invocation deadline and SIGINT/SIGTERM abort SDK create/exec requests, allow
five seconds for cleanup, then exit with 124, 130 or 143. Other SDK operations lack
a common abort signal. A disconnected, interrupted or timed-out mutation may
have completed remotely; this adapter never retries it. Inspect provider state
before retrying. An exec timeout returns 124; a provider's null exit status without
a timeout returns 137. Normal errors return 1. Providers must honor the SDK
contract to kill the whole guest process group on exec timeout/abort. Adapter
cleanup closes handles, not sandboxes. No local timer can guarantee remote cleanup
when a provider stops responding.

## Primary API sources

All source links are pinned to the npm 1.0.3 publication's `gitHead`:

- [Published package metadata](https://registry.npmjs.org/@opencoredev/sandbox-sdk/1.0.3)
- [createSandbox, connectSandbox, listSandboxes](https://github.com/opencoredev/sandbox-sdk/blob/f2e2267751f6cb751ea46b8bffbd09aa238edca1/packages/sdk/src/index.ts)
- [Provider primitives and labels](https://github.com/opencoredev/sandbox-sdk/blob/f2e2267751f6cb751ea46b8bffbd09aa238edca1/packages/sdk/src/provider.ts)
- [RunOptions, close and disposal](https://github.com/opencoredev/sandbox-sdk/blob/f2e2267751f6cb751ea46b8bffbd09aa238edca1/packages/sdk/src/types.ts)
- [Bounded output iterator](https://github.com/opencoredev/sandbox-sdk/blob/f2e2267751f6cb751ea46b8bffbd09aa238edca1/packages/sdk/src/run.ts)
- [Docker lifecycle implementation](https://github.com/opencoredev/sandbox-sdk/blob/f2e2267751f6cb751ea46b8bffbd09aa238edca1/packages/sdk/src/providers/local/index.ts)
