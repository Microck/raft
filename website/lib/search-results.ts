import type { SortedResult } from 'fumadocs-core/search';

// Prefer matching page titles over repeated mentions in long reference/history
// pages. Keep all results available through the dialog's explicit expand action.
export function conciseSearchResults(results: SortedResult[], query: string): SortedResult[] {
  const groups = new Map<string, SortedResult[]>();
  for (const result of results) {
    const page = result.url.split('#')[0].replace(/\/$/, '');
    const group = groups.get(page) ?? [];
    group.push(result);
    groups.set(page, group);
  }
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const ranked = [...groups.values()].map((group, index) => {
    const page = group.find(result => result.type === 'page');
    const title = page?.content.replace(/<[^>]+>/g, '').toLowerCase() ?? '';
    const score = terms.filter(term => title.includes(term)).length;
    return { group, index, score, page };
  }).sort((a, b) => b.score - a.score || a.index - b.index);
  return ranked.slice(0, 8).flatMap(({ group, page }) => {
    const sections = group.filter(result => result.type !== 'page');
    const headings = sections.filter(result => result.type === 'heading');
    const selected = (headings.length ? headings : sections).slice(0, 2);
    return page ? [page, ...selected] : selected;
  });
}
