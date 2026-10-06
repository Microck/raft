'use client';

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
        <SearchDialogList items={query.data === 'empty' ? null : query.data} />
      </SearchDialogContent>
    </SearchDialog>
  );
}
