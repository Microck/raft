---
title: Documentation site
---

# Documentation site

The Fumadocs site in `website/` compiles the top-level Markdown and MDX files in
`docs/` directly. Edit those files to update both GitHub documentation and the site.
`docs/meta.json` controls the sidebar order. Review reports stay outside the site navigation.

## Run locally

Use Node.js 24 and pnpm 11.25.0.

```sh
cd website
pnpm install --frozen-lockfile
pnpm dev --hostname 127.0.0.1 --port 4300
```

Open `http://127.0.0.1:4300/docs`. The site includes navigation, full-text search,
code highlighting and a table of contents. It uses a fixed dark theme.

## Check and build

```sh
pnpm lint
pnpm types:check
pnpm build
pnpm test
pnpm start --hostname 127.0.0.1 --port 4300
```

The build prepares public benchmark and example-configuration files from `docs/`.
Generated assets, dependencies and Next.js output are ignored by version control.
The site is a separate package from the Python CLI.
`pnpm test` starts an isolated production server on controller loopback and checks
pages, local links, search, Markdown exports, downloads, redirects and 404s.
It does not contact workspace hosts. Set `RAFT_DOCS_TEST_PORT` if port 4391 is in use.

## Machine-readable documentation

- `/llms.txt` lists the documentation pages.
- `/llms-full.txt` exports the complete documentation.
- `/docs/contract.md` exports a single page as Markdown.
- An `Accept: text/markdown` request to a documentation URL returns Markdown.

## Hosting

Build and run the site as a Next.js application on a host of your choice.
Set the listening address and port for your reverse proxy. No hosting provider,
public domain or automatic deployment is configured in this repository.
