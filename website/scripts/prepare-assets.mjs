import { cp, mkdir, rm, symlink } from 'node:fs/promises';
import { basePath } from '../lib/shared.ts';

// Copy downloadable reference artifacts from their canonical sources.
const publicDocs = new URL('../public/docs/', import.meta.url);
await mkdir(publicDocs, { recursive: true });
await cp(new URL('../../docs/benchmarks/', import.meta.url), new URL('benchmarks/', publicDocs), { recursive: true });
await cp(new URL('../../docs/incus.example.json', import.meta.url), new URL('incus.example.json', publicDocs));

// Mount the export under its real Pages path for static production previews.
if (process.argv.includes('--preview')) {
  const preview = new URL('../.preview/', import.meta.url);
  await rm(preview, { recursive: true, force: true });
  await mkdir(preview);
  await symlink(new URL('../out/', import.meta.url).pathname, new URL(basePath.slice(1), preview));
}
