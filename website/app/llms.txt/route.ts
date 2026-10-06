import { source } from '@/lib/source';
import { basePath, getPageMarkdownUrl } from '@/lib/shared';

export const dynamic = 'force-static';

export function GET() {
  // These are public file URLs; Next's client-side base-path handling is absent.
  const pages = source.getPages().map((page) => {
    const title = page.data.title.replace(/([[\\]])/g, '\\$1');
    return `- [${title}](${basePath}${getPageMarkdownUrl(page).url})`;
  });
  return new Response(`# Raft documentation\n\n${pages.join('\n')}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}
