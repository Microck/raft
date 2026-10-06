---
title: Documentation site
---

# Documentation site

The Fumadocs site in `website/` compiles Markdown and MDX files in
`docs/` directly. Edit those files to update both GitHub documentation and the site.
`docs/meta.json` and directory `meta.json` files control sidebar order and groups. Review reports stay outside the site navigation.

## Run locally

Use Node.js 24 and pnpm 11.25.0. Python 3 serves the local static preview.

```sh
cd website
pnpm install --frozen-lockfile
pnpm dev --hostname 127.0.0.1 --port 4300
```

Open `http://127.0.0.1:4300/raft/docs/`. The site includes navigation, full-text search,
code highlighting and a table of contents. It uses a fixed dark theme.

## Check and build

```sh
pnpm lint
pnpm types:check
pnpm build
pnpm test
pnpm start
```

The build prepares public benchmark and example-configuration files from `docs/`.
Generated assets, dependencies and Next.js output are ignored by version control.
The site is a separate package from the Python CLI.
`pnpm test` serves the actual static export on controller loopback and checks
pages, local links, client-side search, Markdown exports, downloads and 404s.
It does not contact workspace hosts. Set `RAFT_DOCS_TEST_PORT` if port 4391 is in use.

## Machine-readable documentation

- `/raft/llms.txt` lists the Markdown files for every documentation page.
- `/raft/llms-full.txt` exports the complete documentation.
- `/raft/llms.mdx/docs/contract/content.md` exports the contract as Markdown.

GitHub Pages serves fixed files. Use the explicit Markdown URLs; documentation
pages return HTML regardless of the `Accept` header.

## Hosting

The public site is [microck.github.io/raft](https://microck.github.io/raft/).
Next.js exports static files to `website/out/` with the `/raft` base path.
Search downloads a build-time index and runs in the browser. There is no runtime
server or account requirement.

The documentation workflow builds and tests pull requests. On pushes to `main`,
it uploads the verified export and deploys it through GitHub Pages Actions. The
repository Pages publishing source must be **GitHub Actions**. Maintainers can
also run the workflow manually. Other forks should set `basePath` in
`website/lib/shared.ts` to their repository path and enable Pages.

For a local production preview, `pnpm start` serves the same export at
`http://127.0.0.1:4300/raft/`. The generated files can also be served by any static
HTTP host at that path. Publishing the documentation does not provide public
ingress to Raft workspaces.
