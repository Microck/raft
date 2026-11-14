import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSandbox, connectSandbox, listSandboxes } from '@opencoredev/sandbox-sdk';
import { memory } from '@opencoredev/sandbox-sdk/memory';

const here = dirname(fileURLToPath(import.meta.url));
const root = await mkdtemp(join(tmpdir(), 'raft-sdk-smoke-'));
const options = { factory: join(here, 'smoke-provider.mjs'), providerOptions: { root } };
const labels = { 'raft.location': 'sdk' };

function invoke(action, args = {}, settings = {}) {
  const location = settings.location ?? 'sdk';
  const request = { protocol: 1, location, options: settings.options ?? options,
    args: action === 'describe' ? {} : { action, location, json_output: false, ...args } };
  return new Promise((fulfill, reject) => {
    const stdout = [];
    const stderr = [];
    const child = spawn(process.execPath, [join(here, 'adapter.mjs'), action], {
      env: { ...process.env, RAFT_PLUGIN_REQUEST: JSON.stringify(request) },
      stdio: ['ignore', 'pipe', 'pipe'], timeout: 15000,
    });
    child.stdout.on('data', (chunk) => {
      stdout.push(chunk);
      settings.onStdout?.(chunk, child);
    });
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    child.on('error', reject);
    child.on('close', (status, signal) => fulfill({ status, signal,
      stdout: Buffer.concat(stdout).toString(), stderr: Buffer.concat(stderr).toString() }));
  });
}

async function success(action, args, settings) {
  const result = await invoke(action, args, settings);
  assert.equal(result.status, 0, `${action}: ${result.stderr}`);
  assert.equal(result.stderr, '');
  return result.stdout;
}

async function rejected(action, args, pattern, settings) {
  const result = await invoke(action, args, settings);
  assert.equal(result.status, 1, `${action}: ${result.stderr}`);
  assert.match(result.stderr, pattern);
  assert.equal(result.stdout, '');
}

try {
  await writeFile(join(root, '.raft-sdk-smoke'), 'disposable\n');

  // Published SDK lifecycle/files/run, with its built-in in-memory implementation.
  const provider = memory();
  const created = await createSandbox(provider, { labels, timeoutMs: 60000 });
  try {
    await created.files.write('/workspace/value', new Uint8Array([0, 255, 10]));
    await created.close();
    const attached = await connectSandbox(provider, created.id, { recoverSecrets: false });
    try {
      assert.deepEqual(await attached.files.read('/workspace/value', { as: 'bytes' }), new Uint8Array([0, 255, 10]));
      assert.equal((await attached.run("printf '%s' 'literal $value'")).stdout, 'literal $value');
      assert.deepEqual((await listSandboxes(provider, { labels })).map((box) => box.id), [created.id]);
      await attached.destroy();
    } finally { await attached.close(); }
  } finally { await provider.destroy(created.id); }
  assert.deepEqual(await listSandboxes(provider, { labels }), []);

  // Describe must not load even a factory that would fail or require secrets.
  const forbidden = join(root, 'must-not-import.mjs');
  await writeFile(forbidden, "throw new Error('Factory loaded during handshake');\n");
  const handshake = JSON.parse(await success('describe', {}, { options: { factory: forbidden } }));
  assert.deepEqual(handshake, { protocol: 1,
    commands: ['new', 'list', 'info', 'exec', 'upload', 'download', 'destroy'] });
  assert.deepEqual((await readdir(root)).sort(), ['.raft-sdk-smoke', 'must-not-import.mjs']);

  const creation = { disposable: true, ttl: 600, cpu: null, memory: null, disk: null,
    template: null, purpose: null, owner: null, next: null };
  const handle = (await success('new', creation)).trim();
  assert.match(handle, /^sdk:[A-Za-z0-9_-]+$/);
  const nativeId = Buffer.from(handle.slice(4), 'base64url').toString();
  assert.match(nativeId, /^test\/.+:雪\n$/);
  const storage = join(root, 'boxes', handle.slice(4));
  const input = JSON.parse(await readFile(join(storage, 'creation.json'), 'utf8'));
  assert.equal(input.timeoutMs, 600000);
  assert.equal(Object.hasOwn(input, 'size'), false);
  const metadata = JSON.parse(await success('info', { box: handle }));
  assert.equal(metadata.id, nativeId);
  assert.equal(metadata.labels['raft.location'], 'sdk');
  assert.equal(metadata.labels['sandbox-sdk'], '1');
  assert.deepEqual(JSON.parse((await success('list')).trim()),
    { box: handle, state: 'running', stopAt: '', deleteAt: '', disk: null, note: null });

  const literals = ['', 'space here', "quote'\"", '$HOME; touch SHOULD_NOT_EXIST', '`echo unsafe`', 'line\nbreak', '雪'];
  const literalOutput = await success('exec', { box: handle, detach: false,
    command: ['printf', '%s\n', ...literals] });
  assert.equal(literalOutput, literals.join('\n') + '\n');
  assert.equal((await readdir(join(storage, 'files', 'workspace'))).includes('SHOULD_NOT_EXIST'), false);

  const length = 2 * 1024 * 1024 + 31;
  const large = await invoke('exec', { box: handle, command: [process.execPath, '-e',
    `process.stdout.write('s'.repeat(${length})); process.stderr.write('e'.repeat(${length})); process.exitCode=19;`] });
  assert.equal(large.status, 19);
  assert.equal(large.stdout, 's'.repeat(length));
  assert.equal(large.stderr, 'e'.repeat(length));

  // The guest cannot finish until the controller sees the first streamed chunk.
  const release = join(root, 'release');
  let released;
  const streaming = await invoke('exec', { box: handle, command: [process.execPath, '-e',
    `const fs=require('node:fs'); process.stdout.write('ready\\n'); const t=setInterval(()=>{if(fs.existsSync(${JSON.stringify(release)})){clearInterval(t);process.stdout.write('done\\n')}},10);`] },
  { onStdout(chunk) { if (!released && chunk.toString().includes('ready')) released = writeFile(release, 'go'); } });
  await released;
  assert.equal(streaming.status, 0, streaming.stderr);
  assert.equal(streaming.stdout, 'ready\ndone\n');

  const source = join(root, 'source');
  const destination = join(root, 'download');
  const payload = Buffer.from(Array.from({ length: 262144 }, (_, index) => index % 256));
  await writeFile(source, payload);
  await success('upload', { box: handle, source, destination: '/workspace/file', recursive: false });
  await success('download', { box: handle, source: '/workspace/file', destination, recursive: false });
  assert.deepEqual(await readFile(destination), payload);
  await writeFile(destination, 'keep existing');
  await rejected('download', { box: handle, source: '/workspace/missing', destination }, /ENOENT/);
  assert.equal(await readFile(destination, 'utf8'), 'keep existing');
  await rejected('download', { box: handle, source: '/workspace', destination }, /regular file/);
  assert.equal(await readFile(destination, 'utf8'), 'keep existing');

  await rejected('new', { ...creation, disposable: false }, /--disposable/);
  await rejected('new', { ...creation, disk: '10GiB' }, /Unsupported argument: disk/);
  await rejected('new', { ...creation, purpose: 'test note' }, /Unsupported argument: purpose/);
  await rejected('new', { ...creation, template: 'sdk:x/snapshot' }, /Unsupported argument: template/);
  await rejected('new', { ...creation, cpu: 1 }, /both --cpu and --memory/);
  await rejected('new', { ...creation, cpu: 3, memory: '3GiB' }, /cannot enforce size/);
  await rejected('exec', { box: handle, command: ['true'], detach: true }, /Detached exec/);
  assert.equal(await success('list', { json_output: true }), await success('list'));
  await rejected('upload', { box: handle, source, destination: '/workspace/file', recursive: true }, /Recursive/);
  await rejected('download', { box: handle, source: '/workspace/file', destination, recursive: true }, /Recursive/);
  for (const path of ['relative', '/workspace/../file', '/workspace/file\0bad', '/workspace/']) {
    await rejected('upload', { box: handle, source, destination: path }, /absolute POSIX/);
    await rejected('download', { box: handle, source: path, destination }, /absolute POSIX/);
  }

  // A forged prefix must not bypass the labels, even for destructive/file actions.
  const forged = handle.replace(/^sdk:/, 'other:');
  for (const action of ['info', 'exec', 'upload', 'download', 'destroy']) {
    const args = { box: forged };
    if (action === 'exec') args.command = ['true'];
    if (action === 'upload') Object.assign(args, { source, destination: '/workspace/file' });
    if (action === 'download') Object.assign(args, { source: '/workspace/file', destination });
    await rejected(action, args, /not owned by this Raft location/, { location: 'other' });
  }
  assert.equal(await success('list', {}, { location: 'other' }), '');
  await rejected('info', { box: forged }, /must belong/);
  await rejected('info', { box: 'sdk:bad=' }, /Invalid base64url/);

  // Cancellation controls only the disposable exec group created by smoke-provider.
  const interrupted = await invoke('exec', { box: handle, command: ['/bin/sh', '-c',
    "printf 'interrupt-ready\\n'; sleep 30 & wait"] },
  { onStdout(chunk, child) { if (chunk.toString().includes('interrupt-ready')) child.kill('SIGINT'); } });
  assert.equal(interrupted.status, 130, interrupted.stderr);
  assert.match(interrupted.stderr, /Interrupted/);
  const timedOut = await invoke('exec', { box: handle, command: ['/bin/sh', '-c', 'sleep 30 & wait'] },
    { options: { ...options, operationTimeoutMs: 300 } });
  assert.equal(timedOut.status, 124, timedOut.stderr);

  await success('destroy', { box: handle });
  assert.equal(await success('list'), '');
  await rejected('info', { box: handle }, /missing or not owned/);
  assert.deepEqual(await readdir(join(root, 'boxes')), []);

  console.log('PASS: SDK memory API; cross-process lifecycle, ownership, literal/large/live exec, binary transfers, failure safety, refusals and isolated cancellation');
} finally {
  await rm(root, { recursive: true, force: true });
}
