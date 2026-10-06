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

Open `http://127.0.0.1:4300/docs/`. The site includes navigation, full-text search,
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

- `/llms.txt` lists the Markdown files for every documentation page.
- `/llms-full.txt` exports the complete documentation.
- `/llms.mdx/docs/contract/content.md` exports the contract as Markdown.

GitHub Pages serves fixed files. Use the explicit Markdown URLs; documentation
pages return HTML regardless of the `Accept` header.

## Hosting

The public site is [raft.micr.dev](https://raft.micr.dev/). GitHub Pages serves
`website/out/` at the domain root. Search runs in the browser using a build-time index.

- Pull requests build and test the site. Pushes to `main` deploy the verified export.
- Set the repository Pages publishing source to **GitHub Actions**.
- For a fork, replace `website/public/CNAME` and the `metadataBase` URL in
  `website/app/layout.tsx` with your domain, then configure Pages and DNS.

For a local production preview, `pnpm start` serves the same export at
`http://127.0.0.1:4300/`. Any static HTTP host can serve the exported files.
Publishing the documentation does not provide public
ingress to Raft workspaces.

## Typography

Space Grotesk is bundled locally in `website/public/fonts/`, with its SIL Open
Font License in `OFL.txt`. Moji downloaded the regular, medium and bold WOFF2
files from Fontsource. `next/font/local` loads them without a third-party font request.
Code blocks keep the monospace font.
