import { cp, mkdir, rm, symlink } from 'node:fs/promises';

// Keep docs branding identical to the README's canonical artwork.
await cp(new URL('../../.github/assets/raft-logo.png', import.meta.url), new URL('../public/raft-logo.png', import.meta.url));

// Copy downloadable reference artifacts from their canonical sources.
const publicDocs = new URL('../public/docs/', import.meta.url);
await mkdir(publicDocs, { recursive: true });
await cp(new URL('../../docs/benchmarks/', import.meta.url), new URL('benchmarks/', publicDocs), { recursive: true });
await cp(new URL('../../docs/incus.example.json', import.meta.url), new URL('incus.example.json', publicDocs));

// Serve the custom-domain export at the root for production previews.
if (process.argv.includes('--preview')) {
  const preview = new URL('../.preview', import.meta.url);
  await rm(preview, { recursive: true, force: true });
  await symlink(new URL('../out/', import.meta.url).pathname, preview);
}
