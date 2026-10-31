import { spawn, type ChildProcess } from "node:child_process";
import { randomUUID } from "node:crypto";
import { connect as tcpConnect, createServer } from "node:net";
import { StringDecoder } from "node:string_decoder";
import { setTimeout as sleep } from "node:timers/promises";
import { SandboxError, type ErrorCode } from "@opencoredev/sandbox-sdk";
import type {
  CreateInput,
  FileInfo,
  Labels,
  OutputChunk,
  Preview,
  Provider,
  SandboxInfo,
} from "@opencoredev/sandbox-sdk/provider";

/**
 * The adapter drives the `raft` CLI on this machine, so it reuses the Raft configuration and SSH
 * access the CLI already has. It needs a Raft CLI that forwards stdin to `raft exec` (0.7.1+).
 */
export interface RaftOptions {
  /** Raft location (from `raft locations`) that creates, lists and finds this provider's boxes. */
  location: string;
  /**
   * Lifetime of a new box in seconds when `createSandbox` has no `timeoutMs`. Raft stops a
   * persistent box when its lifetime ends. Default: 3600.
   */
  ttlSeconds?: number;
  /** `owner` field of the box note. Some host policies require it. Default: `"sandbox-sdk"`. */
  owner?: string;
  /** `next` field of the box note. Default: `"Destroy when the sandbox-sdk caller is done"`. */
  next?: string;
  /** Raft executable. Default: `"raft"` on PATH. */
  bin?: string;
}

/** `box.native`: the Raft handle, usable with any `raft` command. */
export interface RaftNative {
  handle: string;
  bin: string;
}

export type RaftProvider = Provider<RaftNative, "size">;

const PROVIDER = "raft";
/** Box notes written by this adapter start with this word, followed by `{ key, labels }` JSON. */
const NOTE_PREFIX = "sandbox-sdk ";
const MIN_TTL = 60;
const MAX_TTL = 2_592_000;
const presets = {
  small: { cpu: 1, memoryGiB: 2 },
  medium: { cpu: 2, memoryGiB: 4 },
  large: { cpu: 4, memoryGiB: 8 },
} as const;

function failure(message: string, code: ErrorCode, sandboxId?: string) {
  return new SandboxError(message, { code, provider: PROVIDER, sandboxId });
}

/** Maps Raft CLI and SSH failure text to SDK error codes. */
function codeFor(stderr: string): ErrorCode {
  // "Instance not found" comes from Incus when a concurrent destroy removed the box first.
  if (/box not found|Instance not found/.test(stderr)) return "not_found";
  if (/No such file or directory|FileNotFoundError/.test(stderr)) return "file_not_found";
  if (/Connection timed out|Could not resolve|Connection refused|banner exchange|Permission denied \(/.test(stderr))
    return "unavailable";
  if (/requires --|host policy|must be|cannot be/.test(stderr)) return "invalid_argument";
  return "provider";
}

const quote = (value: string) => `'${value.replaceAll("'", `'\\''`)}'`;

/** Python helpers run in the guest; every Raft image ships python3. Paths arrive as argv. */
const STAT = `import json, os, stat, sys
def info(p):
    s = os.lstat(p)
    kind = "symlink" if stat.S_ISLNK(s.st_mode) else "directory" if stat.S_ISDIR(s.st_mode) else "file"
    return {"path": p, "type": kind, "size": s.st_size, "modifiedAt": s.st_mtime * 1000}
`;
const WRITE = `import os, sys
p = sys.argv[1]
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "wb") as f:
    f.write(sys.stdin.buffer.read())
if sys.argv[2]:
    os.chmod(p, int(sys.argv[2], 8))
`;
const REMOVE = `import os, shutil, sys
p = sys.argv[1]
if sys.argv[2] == "true" and os.path.isdir(p) and not os.path.islink(p):
    shutil.rmtree(p)
elif os.path.isdir(p) and not os.path.islink(p):
    os.rmdir(p)
else:
    os.remove(p)
`;

interface Note {
  purpose?: string;
}

function parseNote(note: Note | null | undefined): { key?: string; labels: Labels } | undefined {
  if (!note?.purpose?.startsWith(NOTE_PREFIX)) return undefined;
  try {
    const parsed = JSON.parse(note.purpose.slice(NOTE_PREFIX.length)) as { key?: string; labels?: Labels };
    return { key: parsed.key, labels: parsed.labels ?? {} };
  } catch {
    return undefined;
  }
}

function state(status: string): SandboxInfo["state"] {
  if (status === "Running") return "running";
  if (status === "Frozen") return "paused";
  return "stopped";
}

function resources(size: CreateInput["size"]): string[] {
  if (size === undefined) return [];
  const chosen =
    typeof size === "string" ? presets[size] : { cpu: size.cpu, memoryGiB: size.memoryMb / 1024 };
  if (!Number.isInteger(chosen.cpu) || chosen.cpu < 1 || !Number.isInteger(chosen.memoryGiB) || chosen.memoryGiB < 1)
    throw failure("Raft sizes boxes in whole CPUs and whole GiB of memory (memoryMb a multiple of 1024)", "unsupported");
  const args = ["--cpu", String(chosen.cpu), "--memory", `${chosen.memoryGiB}GiB`];
  if (typeof size === "object" && size.diskGb !== undefined) {
    if (!Number.isInteger(size.diskGb) || size.diskGb < 1)
      throw failure("Raft own disks take whole GiB", "unsupported");
    args.push("--disk", `${size.diskGb}GiB`);
  }
  return args;
}

/** A free loopback port for `raft forward`. */
function freePort(): Promise<number> {
  const { promise, resolve, reject } = Promise.withResolvers<number>();
  const server = createServer();
  server.once("error", reject);
  server.listen(0, "127.0.0.1", () => {
    const address = server.address();
    server.close(() => (typeof address === "object" && address ? resolve(address.port) : reject(new Error("no port"))));
  });
  return promise;
}

/** Whether something accepts TCP connections on a loopback port. */
function accepts(port: number): Promise<boolean> {
  const { promise, resolve } = Promise.withResolvers<boolean>();
  const socket = tcpConnect(port, "127.0.0.1");
  socket.once("connect", () => {
    socket.destroy();
    resolve(true);
  });
  socket.once("error", () => resolve(false));
  return promise;
}

/** Every open tunnel in this process, closed when it exits so no `raft forward` outlives it. */
const tunnels = new Set<ChildProcess>();
process.once("exit", () => {
  for (const child of tunnels) child.kill();
});

/** Waits up to 3 s for the exec's PID file, in case cancellation raced startup, then kills its group. */
const KILL_GROUP = `import os, sys, time
p = sys.argv[1]
for _ in range(300):
    try:
        pid = int(open(p).read())
        break
    except (FileNotFoundError, ValueError):
        time.sleep(0.01)
else:
    sys.exit(0)
try:
    os.killpg(pid, 9)
except ProcessLookupError:
    pass
try:
    os.remove(p)
except FileNotFoundError:
    pass
`;

/** Sandbox SDK provider for one Raft location. */
export function raft(options: RaftOptions): RaftProvider {
  const bin = options.bin ?? "raft";
  const ttlDefault = options.ttlSeconds ?? 3600;
  const owner = options.owner ?? "sandbox-sdk";
  const next = options.next ?? "Destroy when the sandbox-sdk caller is done";
  /** Open `raft forward` tunnels by `handle port`. */
  const forwards = new Map<string, { child: ChildProcess; url: string }>();

  /** Starts one raft command. Infrastructure arguments only: commands, env and file data go over stdin. */
  function start(args: string[], stdin?: Uint8Array) {
    const child = spawn(bin, args, {
      env: { ...process.env, RAFT_NO_UPDATE_CHECK: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    const { promise: exited, resolve, reject } = Promise.withResolvers<number | null>();
    child.once("error", (error) =>
      reject(failure(`Could not run ${bin}: ${error.message}. Install the Raft CLI or set bin`, "unavailable")),
    );
    child.once("close", resolve);
    child.stdin.on("error", () => {});
    child.stdin.end(stdin);
    return { child, exited };
  }

  /** Runs a raft command to completion and returns stdout; a nonzero exit becomes a SandboxError. */
  async function call(args: string[], sandboxId?: string, stdin?: Uint8Array): Promise<Buffer> {
    const { child, exited } = start(args, stdin);
    const stdout: Buffer[] = [];
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr.on("data", (chunk: Buffer) => (stderr += chunk.toString()));
    const code = await exited;
    if (code !== 0) {
      const detail = stderr.trim().replace(/^raft: /, "") || `exit ${code}`;
      throw failure(`raft ${args[0]} failed: ${detail}`, codeFor(stderr), sandboxId);
    }
    return Buffer.concat(stdout);
  }

  const guest = (id: string, argv: string[], stdin?: Uint8Array) => call(["exec", id, "--", ...argv], id, stdin);
  const python = (id: string, script: string, args: string[], stdin?: Uint8Array) =>
    guest(id, ["python3", "-c", script, ...args], stdin);

  async function info(id: string): Promise<SandboxInfo> {
    const value = JSON.parse((await call(["info", id], id)).toString()) as {
      status: string;
      created_at: string;
      config: Record<string, string>;
    };
    const note = value.config["user.raft.note"];
    const meta = parseNote(note ? (JSON.parse(note) as Note) : undefined);
    return {
      id,
      key: meta?.key,
      labels: meta?.labels ?? {},
      state: state(value.status),
      // Age checks such as reapSandboxes compare against this machine's clock; a host clock
      // running ahead would otherwise put a new box's creation in the future.
      createdAt: Math.min(Date.parse(value.created_at), Date.now()),
    };
  }

  /** Boxes at this location that carry an adapter note. */
  async function boxes() {
    const lines = (await call(["list", "--location", options.location])).toString().split("\n");
    return lines
      .filter(Boolean)
      .map((line) => JSON.parse(line) as { box: string; note: Note | null })
      .flatMap(({ box, note }) => {
        const meta = parseNote(note);
        return meta ? [{ box, ...meta }] : [];
      });
  }

  function closeForwards(id: string) {
    for (const [name, forward] of forwards)
      if (name.startsWith(`${id} `)) {
        forward.child.kill();
        tunnels.delete(forward.child);
        forwards.delete(name);
      }
  }

  const provider: RaftProvider = {
    name: PROVIDER,
    // Raft has no unique box names; the key lives in the box note.
    keyMode: "best-effort",
    capabilities: new Set(["size"] as const),

    async create(input) {
      if (input.network !== undefined && input.network !== "all")
        throw failure("Raft cannot restrict a box's network", "unsupported");
      if (input.idleTimeoutMs !== undefined)
        throw failure("Raft has no idle timeout; use timeoutMs, which sets the box lifetime", "unsupported");
      const ttl = input.timeoutMs === undefined ? ttlDefault : Math.ceil(input.timeoutMs / 1000);
      if (!Number.isInteger(ttl) || ttl < MIN_TTL || ttl > MAX_TTL)
        throw failure(`Raft box lifetimes run from ${MIN_TTL} s to ${MAX_TTL} s`, "invalid_argument");
      const args = [
        "new",
        "--location",
        options.location,
        "--ttl",
        String(ttl),
        ...resources(input.size),
        // Persistent: close() keeps a keyed box for the next request; destroy() deletes it.
        "--persistent",
        "--purpose",
        NOTE_PREFIX + JSON.stringify({ key: input.key, labels: input.labels }),
        "--owner",
        owner,
        "--next",
        next,
      ];
      if (input.from !== undefined) args.push("--from", input.from);
      if (input.signal?.aborted) throw failure("Creation aborted", "aborted");
      const createdAt = Date.now();
      const id = (await call(args)).toString().trim();
      if (input.signal?.aborted) {
        await provider.destroy(id);
        throw failure("Creation aborted", "aborted", id);
      }
      return { id, key: input.key, labels: input.labels, state: "running", createdAt };
    },

    async find(key) {
      const match = (await boxes()).find((box) => box.key === key);
      return match ? info(match.box) : undefined;
    },

    connect: info,

    async list(labels) {
      const matching = (await boxes()).filter((box) =>
        Object.entries(labels).every(([name, value]) => box.labels[name] === value),
      );
      return Promise.all(matching.map((box) => info(box.box)));
    },

    async destroy(id) {
      closeForwards(id);
      try {
        await call(["destroy", id], id);
      } catch (error) {
        if (!(error instanceof SandboxError) || error.code !== "not_found") throw error;
      }
    },

    async exec(id, input) {
      if (input.signal?.aborted) return { exitCode: null, timedOut: true };
      const pidfile = `/tmp/.sandbox-sdk-exec-${randomUUID()}`;
      // The script, including env values, goes on stdin as one base64 line so secrets never
      // appear in SSH or sudo command lines. The wrapper reads exactly that line; the rest of
      // stdin belongs to the command. setsid makes the wrapper a process group leader, so a
      // timeout can kill everything the command started.
      const script = [
        ...Object.entries(input.env).map(([name, value]) => `export ${name}=${quote(value)}`),
        `cd -- ${quote(input.cwd)} || exit 1`,
        input.command,
      ].join("\n");
      const wrapper = `IFS= read -r s; printf %s "$$" > ${pidfile}; trap 'rm -f ${pidfile}' EXIT; eval "$(printf %s "$s" | base64 -d)"`;
      const stdin = Buffer.concat([
        Buffer.from(Buffer.from(script).toString("base64") + "\n"),
        Buffer.from(input.stdin ?? ""),
      ]);
      const { child, exited } = start(["exec", id, "--", "setsid", "--wait", "sh", "-c", wrapper], stdin);
      const decoders = { stdout: new StringDecoder("utf8"), stderr: new StringDecoder("utf8") };
      const emit = (stream: OutputChunk["stream"], chunk: Buffer) => {
        const text = decoders[stream].write(chunk);
        if (text) input.onChunk({ stream, text });
      };
      child.stdout.on("data", (chunk: Buffer) => emit("stdout", chunk));
      child.stderr.on("data", (chunk: Buffer) => emit("stderr", chunk));

      const { promise: cancelled, resolve: cancel } = Promise.withResolvers<"cancelled">();
      const onAbort = () => cancel("cancelled");
      input.signal?.addEventListener("abort", onAbort, { once: true });
      const timer = setTimeout(onAbort, input.timeoutMs);
      try {
        const outcome = await Promise.race([exited, cancelled]);
        if (outcome === "cancelled") {
          await python(id, KILL_GROUP, [pidfile]);
          child.kill();
          await exited;
          return { exitCode: null, timedOut: true };
        }
        for (const stream of ["stdout", "stderr"] as const) {
          const text = decoders[stream].end();
          if (text) input.onChunk({ stream, text });
        }
        return { exitCode: outcome, timedOut: false };
      } finally {
        clearTimeout(timer);
        input.signal?.removeEventListener("abort", onAbort);
      }
    },

    files: {
      writeCreatesParents: true,
      writeSupportsMode: true,
      listOrder: "name",
      async read(id, path) {
        return new Uint8Array(await guest(id, ["cat", "--", path]));
      },
      async write(id, path, data, writeOptions) {
        await python(id, WRITE, [path, writeOptions?.mode?.toString(8) ?? ""], data);
      },
      async stat(id, path) {
        return JSON.parse((await python(id, `${STAT}print(json.dumps(info(sys.argv[1])))`, [path])).toString()) as FileInfo;
      },
      async list(id, path) {
        const script = `${STAT}print(json.dumps([info(os.path.join(sys.argv[1], n)) for n in sorted(os.listdir(sys.argv[1]))]))`;
        return JSON.parse((await python(id, script, [path])).toString()) as FileInfo[];
      },
      async remove(id, path, removeOptions) {
        await python(id, REMOVE, [path, String(removeOptions.recursive)]);
      },
      async mkdir(id, path) {
        await guest(id, ["mkdir", "-p", "--", path]);
      },
    },

    /** Opens a private `raft forward` tunnel to the port and returns its loopback URL. */
    async previewUrl(id, port): Promise<Preview> {
      const name = `${id} ${port}`;
      const open = forwards.get(name);
      if (open && open.child.exitCode === null) return { url: open.url };
      const local = await freePort();
      const { child, exited } = start(["forward", id, "--remote", String(port), "--local", String(local)]);
      let stderr = "";
      child.stderr.on("data", (chunk: Buffer) => (stderr += chunk.toString()));
      tunnels.add(child);
      child.once("close", () => tunnels.delete(child));
      // A spawn failure leaves exitCode null; the time limit then reports it through stderr.
      exited.catch(() => {});
      // raft forward itself waits up to 30 s for a new box's DHCP lease before tunnelling.
      for (let attempt = 0; attempt < 450 && child.exitCode === null; attempt++) {
        if (await accepts(local)) {
          const url = `http://127.0.0.1:${local}/`;
          forwards.set(name, { child, url });
          return { url };
        }
        await sleep(100);
      }
      child.kill();
      throw failure(`raft forward failed: ${stderr.trim() || "the tunnel did not open within 45 s"}`, codeFor(stderr), id);
    },

    native(id) {
      return { handle: id, bin };
    },

    async close(id) {
      closeForwards(id);
    },
  };

  return provider;
}
