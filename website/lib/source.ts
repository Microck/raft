import { docsRoute } from './shared';
import { llms, loader } from 'fumadocs-core/source';
import { defineDocs } from 'fumadocs-mdx/macro';
import { metaSchema, pageSchema } from 'fumadocs-core/source/schema';

// Compile repository docs directly so GitHub and the site use the same content.
const docs = defineDocs({
  dir: '../docs',
  docs: {
    files: ['*.md', '*.mdx', '!reviews/**'],
    schema: pageSchema,
    postprocess: { includeProcessedMarkdown: true },
  },
  meta: { files: ['meta.json'], schema: metaSchema },
});

export const source = loader({ baseUrl: docsRoute, source: docs.toFumadocsSource() });
export const docsLlms = llms(source, {
  renderPage: async (page) => page.data.getText('processed'),
});
