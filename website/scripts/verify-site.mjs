import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { setTimeout } from 'node:timers/promises';
import { staticClient } from 'fumadocs-core/search/client/orama-static';
import config from '../next.config.mjs';

// Serve the real exported files at the Pages base path, without a Next.js server.
const port = process.env.RAFT_DOCS_TEST_PORT ?? '4391';
const origin = `http://127.0.0.1:${port}`;
const base = `${origin}${config.basePath}`;
const server = spawn('python3', [
  '-u', '-m', 'http.server', port, '--bind', '127.0.0.1', '--directory', '.preview',
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
  for (let attempt = 0; attempt < 100 && !serverLog.includes('Serving HTTP'); attempt++) {
    assert.equal(server.exitCode, null, serverLog);
    await setTimeout(100);
  }
  assert.ok(serverLog.includes('Serving HTTP'), `Server did not become ready: ${serverLog}`);
  const paths = [
    '/docs',
    '/docs/requirements',
    '/docs/operations',
    '/docs/first-box',
    '/docs/guides/docker',
    '/docs/guides/files',
    '/docs/guides/background-jobs',
    '/docs/guides/desktop-services',
    '/docs/guides/snapshots-forks',
    '/docs/guides/backup-recovery',
    '/docs/guides/troubleshooting',
    '/docs/reference/cli',
    '/docs/reference/configuration',
    '/docs/reference/capacity-limits',
    '/docs/contract',
    '/docs/boat-capabilities',
    '/docs/boot-performance',
    '/docs/boat-parity-design',
    '/docs/verification',
    '/docs/publishing',
    '/docs/site-development',
    '/docs/releases/0.2.0',
  ];
  const targets = new Set();
  for (const path of paths) {
    const response = await get(path);
    assert.equal(response.status, 200, path);
    assert.match(response.headers.get('content-type') ?? '', /text\/html/);
    const html = await response.text();
    if (path === '/docs/boot-performance') {
      assert.ok(html.includes('href="https://github.com/Microck/raft/blob/main/.github/workflows/images.yml"'), 'Workflow source link must point to GitHub');
    }
    if (path === '/docs/operations') {
      assert.ok(html.includes('role="tab"'), `${path}: must contain role="tab"`);
      assert.ok(html.includes('class="fd-steps"'), `${path}: must contain class="fd-steps"`);
      assert.equal([...html.matchAll(/<div class="fd-step">/g)].length, 7, `${path}: must contain exactly seven occurrences of <div class="fd-step">`);
    }
    if (path === '/docs/first-box') {
      assert.ok(/<div[^>]*class="[^"]*raft-callout/.test(html), `${path}: must contain raft-callout`);
    }
    assert.equal([...html.matchAll(/<h1(?:\s|>)/g)].length, 1, `${path}: one main heading`);
    for (const [, href] of html.matchAll(/<a\b[^>]*\bhref="([^"]+)"/g)) {
      const target = new URL(href.replaceAll('&amp;', '&'), `${response.url}`);
      if (target.origin === origin) {
        assert.ok(!target.pathname.endsWith('.md'), `${path}: document link opens raw Markdown`);
        assert.ok(target.pathname.startsWith(`${config.basePath}/`), `Link escapes Pages path: ${target.pathname}`);
        targets.add(target.pathname.slice(config.basePath.length));
      }
    }
  }
  for (const path of targets) {
    const response = await get(path);
    await response.arrayBuffer();
    assert.equal(response.status, 200, `Broken local link: ${path}`);
  }
  const missing = await get('/docs/page-that-does-not-exist');
  await missing.arrayBuffer();
  assert.equal(missing.status, 404);
  const home = await (await get('/')).text();
  const headings = [...home.matchAll(/<h1\b[^>]*>([\s\S]*?)<\/h1>/g)];
  assert.equal(headings.length, 1, 'Entry page must have one main heading');
  assert.match(headings[0][1], />Raft</, 'Entry page must display the Raft overview heading');

  assert.match(home, /<img\b[^>]*src="\/raft-logo\.png"/, 'Navigation must display the Raft logo');
  assert.match(home, /<link\b[^>]*rel="icon"[^>]*href="\/raft-logo\.png"/, 'Browser must receive the Raft favicon');
  const logo = await get('/raft-logo.png', 'image/png');
  assert.equal(logo.status, 200, 'Logo and favicon must be served');
  assert.match(logo.headers.get('content-type') ?? '', /image\/png/);
  assert.deepEqual(Buffer.from(await logo.arrayBuffer()), await readFile(new URL('../../.github/assets/raft-logo.png', import.meta.url)), 'Published branding must match the canonical artwork');

  assert.equal(await (await get('/CNAME')).text(), await readFile(new URL('../public/CNAME', import.meta.url), 'utf8'));
  assert.match(await (await get('/fonts/boat-fonts-license.txt')).text(), /SIL OPEN FONT LICENSE/);
  const fonts = new Set([...home.matchAll(/href="([^"]+\.woff2)"/g)].map((match) => match[1]));
  assert.equal(fonts.size, 3, 'Entry page must preload Inter body weights and Funnel Display');
  for (const path of fonts) {
    const response = await fetch(`${origin}${path}`, { headers: requestHeaders });
    assert.equal(response.status, 200, `Missing local font: ${path}`);
    assert.equal(Buffer.from(await response.arrayBuffer()).subarray(0, 4).toString(), 'wOF2', `Invalid WOFF2 font: ${path}`);
  }

  const index = await get('/llms.txt');
  assert.match(index.headers.get('content-type') ?? '', /text\/plain/);
  const indexText = await index.text();
  const indexLinks = [...indexText.matchAll(/\]\(([^)]+)\)/g)].map((match) => match[1]);
  assert.equal(indexLinks.length, paths.length, 'Index must link each documentation page once');
  for (const path of paths) {
    const slug = path === '/docs' ? '' : path.slice('/docs'.length);
    const file = `${config.basePath}/llms.mdx/docs${slug}/content.md`;
    assert.ok(indexLinks.includes(file), `Index omits Markdown for ${path}`);
    const response = await fetch(`${origin}${file}`, { headers: requestHeaders });
    await response.arrayBuffer();
    assert.equal(response.status, 200, file);
  }
  const full = await get('/llms-full.txt');
  const fullText = await full.text();
  assert.match(fullText, /raft limits/);
  assert.match(fullText, /Snapshots, restores and forks require a stopped source/);
  assert.ok(!fullText.includes('Full-code review:'), 'Review reports must not enter the site');

  const markdown = await get('/llms.mdx/docs/contract/content.md');
  assert.equal(markdown.status, 200);
  assert.match(await markdown.text(), /# Raft contract/);
  const negotiated = await get('/docs/contract', 'text/markdown');
  await negotiated.arrayBuffer();
  assert.match(negotiated.headers.get('content-type') ?? '', /text\/html/);

  const searchIndex = await get('/search.json');
  await searchIndex.arrayBuffer();
  assert.equal(searchIndex.status, 200);
  const client = staticClient({ from: `${base}/search.json` });
  const matches = await client.search('limits');
  assert.ok(matches.length > 0, 'Static search must find real page content');
  assert.ok(matches.some((match) => match.url.startsWith('/docs/reference/capacity-limits')), 'Capacity guide missing from search');
  for (const path of ['/docs/incus.example.json', '/docs/benchmarks/arm64-native-ci.json', '/docs/benchmarks/amd64-native-ci.json']) {
    const response = await get(path);
    assert.equal(response.status, 200, path);
    await response.json();
  }
  console.log(`Verified ${paths.length} pages, ${targets.size} local links, static search, exports, downloads and 404s.`);
} finally {
  server.kill('SIGTERM');
}
