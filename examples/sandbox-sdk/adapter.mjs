import { open, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const commands = ['new', 'list', 'info', 'exec', 'upload', 'download', 'destroy'];
const locationLabel = 'raft.location';
const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = (message) => { throw new Error(message); };
const json = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);

function guestPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.endsWith('/') ||
      value.includes('\0') || value.split('/').includes('..')) {
    fail('Guest path must be an absolute POSIX file path without NUL or .. components');
  }
  return value;
}

function nativeId(handle, location) {
  if (typeof handle !== 'string' || !handle.startsWith(`${location}:`)) {
    fail('Box handle must belong to the requested location');
  }
  const encoded = handle.slice(location.length + 1);
  if (!/^[A-Za-z0-9_-]+$/.test(encoded)) fail('Invalid base64url box ID');
  const id = Buffer.from(encoded, 'base64url').toString('utf8');
  if (!id || Buffer.from(id).toString('base64url') !== encoded) fail('Noncanonical box ID');
  return id;
}

function qualified(id, location) {
  if (typeof id !== 'string' || !id || !id.isWellFormed()) fail('Provider returned an invalid UTF-8 ID');
  return `${location}:${Buffer.from(id).toString('base64url')}`;
}

function validateArgs(action, args, location) {
  if (args.action !== action || args.location !== location) fail('Action or normalized location mismatch');
  const allowed = {
    new: ['ttl', 'cpu', 'memory', 'disposable'],
    list: [], info: [], destroy: [],
    exec: ['command', 'detach'],
    upload: ['source', 'destination', 'recursive'],
    download: ['source', 'destination', 'recursive'],
  }[action];
  for (const [key, value] of Object.entries(args)) {
    if (!['action', 'location', 'box', 'json_output', ...allowed].includes(key) &&
        value !== null && value !== false && value !== undefined) {
      fail(`Unsupported argument: ${key}`);
    }
  }
  if (action === 'new') {
    if (args.disposable !== true) fail('new requires --disposable; persistent stop is not supported');
    if (!Number.isSafeInteger(args.ttl) || args.ttl < 60 || args.ttl > 2592000) fail('TTL must be 60..2592000 seconds');
    const cpu = args.cpu ?? null;
    const memory = args.memory ?? null;
    if ((cpu === null) !== (memory === null)) fail('Explicit sizing requires both --cpu and --memory');
    if (cpu !== null && (!Number.isSafeInteger(cpu) || cpu <= 0 ||
        typeof memory !== 'string' || !/^[1-9][0-9]*GiB$/.test(memory) ||
        !Number.isSafeInteger(Number.parseInt(memory, 10) * 1024))) {
      fail('CPU must be a positive integer and memory must be whole positive GiB');
    }
  }
  if (action === 'exec') {
    if (args.detach) fail('Detached exec is not supported');
    if (!Array.isArray(args.command) || !args.command.length ||
        args.command.some((arg) => typeof arg !== 'string' || arg.includes('\0'))) {
      fail('exec requires nonempty literal argv without NUL');
    }
  }
  if (action === 'upload' || action === 'download') {
    if (args.recursive) fail('Recursive transfers are not supported');
    guestPath(action === 'upload' ? args.destination : args.source);
    const localPath = action === 'upload' ? args.source : args.destination;
    if (typeof localPath !== 'string' || !localPath || localPath.includes('\0')) fail('Local file path is required');
  }
  if (!['new', 'list'].includes(action)) nativeId(args.box, location);
}

async function atomicDownload(destination, bytes) {
  const target = resolve(destination);
  const staging = await mkdtemp(join(dirname(target), '.raft-download-'));
  try {
    const temporary = join(staging, 'file');
    await writeFile(temporary, bytes, { flag: 'wx', mode: 0o600 });
    await rename(temporary, target);
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
}

async function dispatch({ action, request, signal, timeoutMs }) {
  const { args, location, options } = request;
  const { createSandbox, connectSandbox, listSandboxes } = await import('@opencoredev/sandbox-sdk');
  const factory = (await import(pathToFileURL(options.factory).href)).default;
  if (typeof factory !== 'function') fail('Factory module must default-export a function returning a Provider');
  const provider = await factory(options.providerOptions ?? {});
  const labels = { 'sandbox-sdk': '1', [locationLabel]: location };
  const owns = (box) => Object.entries(labels).every(([key, value]) => box.labels?.[key] === value);
  if (signal.aborted) throw signal.reason;

  if (action === 'list') {
    for (const box of await listSandboxes(provider, { labels })) {
      if (owns(box)) json({ box: qualified(box.id, location), state: box.state,
        stopAt: '', deleteAt: '', disk: null, note: null });
    }
    return 0;
  }

  let box;
  let download;
  let exitCode = 0;
  try {
    if (action === 'new') {
      const size = args.cpu == null ? {} : {
        size: { cpu: args.cpu, memoryMb: Number.parseInt(args.memory, 10) * 1024 },
      };
      box = await createSandbox(provider, { ...size, labels, timeoutMs: args.ttl * 1000, signal });
    } else {
      const id = nativeId(args.box, location);
      // Some providers expire/delete during connect. Establish ownership before that call.
      const candidates = await listSandboxes(provider, { labels });
      if (!candidates.some((candidate) => candidate.id === id && owns(candidate))) {
        fail('Sandbox is missing or not owned by this Raft location');
      }
      box = await connectSandbox(provider, id, { recoverSecrets: false });
    }
    // Check even when the provider ignores list filters or connects an arbitrary native ID.
    if (!owns(box)) fail('Sandbox is not owned by this Raft location');
    if (signal.aborted) throw signal.reason;
    switch (action) {
      case 'new':
        // Close successfully before publishing a handle; never auto-dispose an unkeyed sandbox.
        await box.close();
        process.stdout.write(`${qualified(box.id, location)}\n`);
        break;
      case 'info':
        json({ box: qualified(box.id, location), id: box.id, provider: box.provider,
          labels: box.labels, capabilities: [...box.capabilities] });
        break;
      case 'destroy':
        await box.destroy();
        break;
      case 'exec': {
        // RunHandle's OutputBus drops chunks above 1 MiB. Primitive callbacks do not.
        const outcome = await provider.exec(box.id, {
          command: args.command.map(quote).join(' '), cwd: '/workspace', env: {},
          timeoutMs, signal, streaming: true,
          onChunk({ stream, text }) { process[stream].write(text); },
        });
        exitCode = outcome.timedOut ? 124 : (outcome.exitCode ?? 137);
        if (!Number.isInteger(exitCode) || exitCode < 0 || exitCode > 255) fail('Provider returned an invalid exit code');
        break;
      }
      case 'upload': {
        const source = await open(args.source, 'r');
        try {
          if (!(await source.stat()).isFile()) fail('Upload source must be a regular file');
          await box.files.write(args.destination, await source.readFile());
        } finally { await source.close(); }
        break;
      }
      case 'download':
        if ((await box.files.stat(args.source)).type !== 'file') fail('Download source must be a regular file');
        download = await box.files.read(args.source, { as: 'bytes' });
        break;
    }
  } finally {
    await box?.close();
  }
  // A failed remote read or handle close cannot damage an existing local destination.
  if (signal.aborted) throw signal.reason;
  if (download !== undefined) await atomicDownload(args.destination, download);
  return exitCode;
}

async function main() {
  const action = process.argv.at(-1);
  const request = JSON.parse(process.env.RAFT_PLUGIN_REQUEST ?? 'null');
  if (!object(request) || request.protocol !== 1 || !object(request.args) ||
      !object(request.options) || typeof request.location !== 'string' ||
      !request.location || /[:\s]/.test(request.location)) fail('Invalid protocol 1 request');
  if (action === 'describe') {
    json({ protocol: 1, commands });
    return 0;
  }
  if (!commands.includes(action)) fail(`Unsupported action: ${action}`);
  validateArgs(action, request.args, request.location);
  const { options } = request;
  for (const key of Object.keys(options)) {
    if (!['factory', 'providerOptions', 'operationTimeoutMs'].includes(key)) fail(`Unknown adapter option: ${key}`);
  }
  if (typeof options.factory !== 'string' || !isAbsolute(options.factory)) fail('options.factory must be an absolute module filename');
  if (options.providerOptions !== undefined && !object(options.providerOptions)) fail('providerOptions must be an object');
  const timeoutMs = options.operationTimeoutMs ?? 120000;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 2147483647) fail('operationTimeoutMs must be 1..2147483647');
  const controller = new AbortController();
  let cancelledCode;
  let forced;
  const cancel = (code, message) => {
    if (cancelledCode !== undefined) return;
    cancelledCode = code;
    process.stderr.write(`${message}; remote outcome may be uncertain. Inspect before retrying.\n`);
    controller.abort(new Error(message));
    forced = setTimeout(() => process.exit(code), 5000);
    forced.unref();
  };
  const interrupt = () => cancel(130, 'Interrupted');
  const terminate = () => cancel(143, 'Terminated');
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  const deadline = setTimeout(() => cancel(124, 'Adapter operation timed out'), timeoutMs);
  try {
    return await dispatch({ action, request, signal: controller.signal, timeoutMs });
  } finally {
    clearTimeout(deadline);
    if (cancelledCode === undefined) clearTimeout(forced);
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
    if (cancelledCode !== undefined) process.exitCode = cancelledCode;
  }
}

try {
  const code = await main();
  process.exitCode ??= code;
  // Flush all callback writes, including output larger than the writable high-water mark.
  await Promise.all([process.stdout, process.stderr].map((stream) =>
    new Promise((resolve) => stream.write('', resolve))));
} catch (error) {
  process.stderr.write(`sandbox-sdk adapter: ${error.message}\n`);
  process.exitCode ??= 1;
}
