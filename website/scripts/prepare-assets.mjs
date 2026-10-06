import { cp, mkdir } from 'node:fs/promises';

// Copy downloadable reference artifacts from their canonical sources.
const publicDocs = new URL('../public/docs/', import.meta.url);
await mkdir(publicDocs, { recursive: true });
await cp(new URL('../../docs/benchmarks/', import.meta.url), new URL('benchmarks/', publicDocs), { recursive: true });
await cp(new URL('../../docs/incus.example.json', import.meta.url), new URL('incus.example.json', publicDocs));
