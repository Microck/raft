// Test-only Provider: real files and host shell processes, not an isolation boundary.
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { constants } from 'node:os';
import { readFile, writeFile, mkdir, readdir, lstat, rm, access } from 'node:fs/promises';
import { join, resolve, relative, isAbsolute, dirname } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { SandboxError } from '@opencoredev/sandbox-sdk';

export default async function filesystemProvider({ root }) {
  if (typeof root !== 'string' || !isAbsolute(root) ||
      (await readFile(join(root, '.raft-sdk-smoke'), 'utf8')) !== 'disposable\n') {
    throw new Error('Test Provider requires a marked disposable root');
  }
  const boxes = join(root, 'boxes');
  await mkdir(boxes, { recursive: true });
  const directory = (id) => join(boxes, Buffer.from(id).toString('base64url'));
  const metadata = (id) => join(directory(id), 'info.json');
  const guest = (id, path) => {
    const base = join(directory(id), 'files');
    const target = resolve(base, `.${path}`);
    const rel = relative(base, target);
    if (!path.startsWith('/') || rel === '..' || rel.startsWith('../') || isAbsolute(rel)) {
      throw new Error('Path escapes test workspace');
    }
    return target;
  };
  const connect = async (id) => {
    try { return JSON.parse(await readFile(metadata(id), 'utf8')); }
    catch (cause) {
      if (cause.code === 'ENOENT') throw new SandboxError('Unknown disposable test sandbox', { code: 'not_found' });
      throw cause;
    }
  };
  const stat = async (id, path) => {
    await connect(id);
    const value = await lstat(guest(id, path));
    return { path, type: value.isSymbolicLink() ? 'symlink' : value.isDirectory() ? 'directory' : 'file',
      size: value.size, modifiedAt: value.mtimeMs };
  };
  return {
    name: 'raft-disposable-filesystem', keyMode: 'best-effort', capabilities: new Set(),
    async create(input) {
      // Exercise IDs that cannot safely be printed raw on one CLI line.
      const id = `test/${randomUUID()}:雪\n`;
      await mkdir(guest(id, '/workspace'), { recursive: true });
      const info = { id, labels: input.labels, state: 'running', createdAt: Date.now() };
      await writeFile(metadata(id), JSON.stringify(info), { flag: 'wx' });
      await writeFile(join(directory(id), 'creation.json'), JSON.stringify(input));
      return info;
    },
    async find(key) {
      return (await this.list({})).find((box) => box.key === key);
    },
    connect,
    async list(labels) {
      const entries = await readdir(boxes);
      const rows = await Promise.all(entries.map(async (entry) =>
        JSON.parse(await readFile(join(boxes, entry, 'info.json'), 'utf8'))));
      return rows.filter((box) => Object.entries(labels).every(([key, value]) => box.labels[key] === value));
    },
    async destroy(id) {
      await connect(id);
      await rm(directory(id), { recursive: true });
    },
    async close(id) { await access(metadata(id)); },
    native(id) { return { directory: directory(id) }; },
    async previewUrl() { throw new SandboxError('Test Provider has no network preview', { code: 'unsupported' }); },
    async exec(id, input) {
      await connect(id);
      if (input.signal?.aborted) return { exitCode: null, timedOut: true };
      return await new Promise((fulfill, reject) => {
        // This is the only process group signaled by the smoke. It is newly spawned here.
        const child = spawn('/bin/sh', ['-c', input.command], {
          cwd: guest(id, input.cwd), env: { ...process.env, ...input.env },
          detached: true, stdio: ['pipe', 'pipe', 'pipe'],
        });
        let timedOut = false;
        const cancel = () => {
          timedOut = true;
          try { process.kill(-child.pid, 'SIGKILL'); }
          catch (error) { if (error.code !== 'ESRCH') reject(error); }
        };
        const timer = setTimeout(cancel, input.timeoutMs);
        input.signal?.addEventListener('abort', cancel, { once: true });
        for (const stream of ['stdout', 'stderr']) {
          const decoder = new StringDecoder('utf8');
          child[stream].on('data', (bytes) => {
            const text = decoder.write(bytes);
            if (text) input.onChunk({ stream, text });
          });
          child[stream].on('end', () => {
            const text = decoder.end();
            if (text) input.onChunk({ stream, text });
          });
        }
        const cleanup = () => {
          clearTimeout(timer);
          input.signal?.removeEventListener('abort', cancel);
        };
        child.once('error', (error) => { cleanup(); reject(error); });
        child.once('close', (code, signal) => {
          cleanup();
          fulfill({ exitCode: code ?? (signal ? 128 + constants.signals[signal] : null), timedOut });
        });
        child.stdin.on('error', (error) => { if (error.code !== 'EPIPE') reject(error); });
        child.stdin.end(input.stdin ?? '');
      });
    },
    files: {
      writeCreatesParents: true, writeSupportsMode: true,
      async read(id, path) { await connect(id); return await readFile(guest(id, path)); },
      async write(id, path, bytes, options) {
        await connect(id);
        await mkdir(dirname(guest(id, path)), { recursive: true });
        await writeFile(guest(id, path), bytes, options);
      },
      stat,
      async list(id, path) {
        return await Promise.all((await readdir(guest(id, path))).map((name) => stat(id, `${path}/${name}`)));
      },
      async mkdir(id, path) { await connect(id); await mkdir(guest(id, path), { recursive: true }); },
      async remove(id, path, options) { await connect(id); await rm(guest(id, path), options); },
    },
  };
}
