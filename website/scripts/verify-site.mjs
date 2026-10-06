import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { setTimeout } from 'node:timers/promises';

// Exercise the real production server, without mocks or access to any Raft host.
const port = process.env.RAFT_DOCS_TEST_PORT ?? '4391';
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [
  'node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', port,
], { cwd: new URL('../', import.meta.url), stdio: ['ignore', 'pipe', 'pipe'] });
let serverLog = '';
server.stdout.on('data', (chunk) => { serverLog += chunk; });
server.stderr.on('data', (chunk) => { serverLog += chunk; });
const requestHeaders = { 'User-Agent': 'OpenAI File Downloader, XaiImageApiFetch/1.0' };

async function get(path, accept = 'text/html') {
  return fetch(`${base}${path}`, {
    headers: { ...requestHeaders, Accept: accept },
    signal: AbortSignal.timeout(10_000),
  });
}

try {
  // Wait for the server's readiness message so an existing listener cannot pass checks.
  for (let attempt = 0; attempt < 100 && !serverLog.includes('Ready in'); attempt++) {
    assert.equal(server.exitCode, null, serverLog);
    await setTimeout(100);
  }
  assert.ok(serverLog.includes('Ready in'), `Server did not become ready: ${serverLog}`);
  const paths = [
    '/docs', '/docs/operations', '/docs/boat-capabilities', '/docs/boot-performance',
    '/docs/contract', '/docs/verification', '/docs/publishing',
    '/docs/boat-parity-design', '/docs/site-development',
  ];
  const targets = new Set();
  for (const path of paths) {
    const response = await get(path);
    assert.equal(response.status, 200, path);
    assert.match(response.headers.get('content-type') ?? '', /text\/html/);
    assert.match(response.headers.get('vary') ?? '', /Accept/i, `${path}: HTML cache negotiation`);
    const html = await response.text();
    if (path === '/docs/boot-performance') {
      assert.ok(html.includes('href="https://github.com/Microck/raft/blob/main/.github/workflows/images.yml"'), 'Workflow source link must point to GitHub');
    }
    assert.equal([...html.matchAll(/<h1(?:\s|>)/g)].length, 1, `${path}: one main heading`);
    for (const [, href] of html.matchAll(/<a\b[^>]*\bhref="([^"]+)"/g)) {
      const target = new URL(href.replaceAll('&amp;', '&'), `${base}${path}`);
      if (target.origin === base) {
        assert.ok(!target.pathname.endsWith('.md'), `${path}: document link opens raw Markdown`);
        targets.add(target.pathname);
      }
    }
  }
  for (const path of targets) {
    assert.equal((await get(path)).status, 200, `Broken local link: ${path}`);
  }
  assert.equal((await get('/docs/page-that-does-not-exist')).status, 404);
  assert.equal((await get('/')).url, `${base}/docs`);

  const index = await get('/llms.txt');
  assert.match(index.headers.get('content-type') ?? '', /text\/plain/);
  const indexText = await index.text();
  for (const path of paths) assert.ok(indexText.includes(`](${path})`), `Index omits ${path}`);
  const full = await get('/llms-full.txt');
  const fullText = await full.text();
  assert.match(fullText, /raft limits/);
  assert.match(fullText, /Snapshots, restores and forks require a stopped source/);
  assert.ok(!fullText.includes('Full-code review:'), 'Review reports must not enter the site');

  for (const path of ['/docs/contract', '/docs/contract.md']) {
    const response = await get(path, 'text/markdown');
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type') ?? '', /text\/markdown/);
    assert.match(await response.text(), /# Raft contract/);
    if (!path.endsWith('.md')) assert.match(response.headers.get('vary') ?? '', /Accept/i);
  }
  const search = await get('/api/search?query=limits');
  assert.equal(search.status, 200);
  const matches = await search.json();
  assert.ok(Array.isArray(matches) && matches.length > 0, 'Search must find real page content');
  assert.ok(matches.some((match) => match.url.startsWith('/docs/operations')), 'Capacity guide missing from search');
  for (const path of ['/docs/incus.example.json', '/docs/benchmarks/arm64-native-ci.json', '/docs/benchmarks/amd64-native-ci.json']) {
    const response = await get(path);
    assert.equal(response.status, 200, path);
    await response.json();
  }
  console.log(`Verified ${paths.length} pages, ${targets.size} local links, search, exports, downloads, redirects and 404s.`);
} finally {
  server.kill('SIGTERM');
}
