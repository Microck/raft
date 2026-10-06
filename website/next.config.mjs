import { fileURLToPath } from 'node:url';
import { createMDX } from 'fumadocs-mdx/next';
import { basePath } from './lib/shared.ts';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  output: 'export',
  basePath,
  trailingSlash: true,
  turbopack: { root: fileURLToPath(new URL('../', import.meta.url)) },
  experimental: { cpus: 2 },
};

export default withMDX(config);
