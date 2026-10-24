'use client';

import { useState } from 'react';
import { conciseSearchResults } from '@/lib/search-results';
import { useDocsSearch } from 'fumadocs-core/search/client';
import { staticClient } from 'fumadocs-core/search/client/orama-static';
import {
  SearchDialog, SearchDialogClose, SearchDialogContent, SearchDialogHeader,
  SearchDialogIcon, SearchDialogInput, SearchDialogList, SearchDialogOverlay,
  type SharedProps,
} from 'fumadocs-ui/components/dialog/search';
import { basePath } from '@/lib/shared';

// Pages serves the exported index; queries never require a runtime API.
const client = staticClient({ from: `${basePath}/search.json` });

export default function StaticSearch(props: SharedProps) {
  const { search, setSearch, query } = useDocsSearch({ client });
  const [expandedQuery, setExpandedQuery] = useState<string | null>(null);
  const allResults = query.data === 'empty' ? null : query.data;
  const concise = allResults ? conciseSearchResults(allResults, search) : null;
  const expanded = expandedQuery === search;
  const hasMore = !!allResults && !!concise && concise.length < allResults.length;
  return (
    <SearchDialog {...props} search={search} onSearchChange={setSearch} isLoading={query.isLoading}>
      <SearchDialogOverlay />
      <SearchDialogContent>
        <SearchDialogHeader>
          <SearchDialogIcon />
          <SearchDialogInput />
          <SearchDialogClose />
        </SearchDialogHeader>
        {query.error ? <p role="alert">Search could not load. Reload the page and try again.</p> : null}
        <SearchDialogList items={expanded ? allResults : concise} />
        {hasMore && <button type="button" className="m-3 rounded border px-3 py-2 text-sm focus-visible:outline" onClick={() => setExpandedQuery(expanded ? null : search)}>
          {expanded ? 'Show fewer sections' : 'Show all matching sections'}
        </button>}
      </SearchDialogContent>
    </SearchDialog>
  );
}
