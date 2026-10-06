import { source } from '@/lib/source';
import { DocsBody, DocsPage } from 'fumadocs-ui/layouts/docs/page';
import { notFound } from 'next/navigation';
import { getMDXComponents } from '@/components/mdx';
import type { Metadata } from 'next';
import defaultMdxComponents, { createRelativeLink } from 'fumadocs-ui/mdx';

export const dynamicParams = false;

export default async function Page(props: PageProps<'/docs/[[...slug]]'>) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();
  const MDX = page.data.body;
  const Link = createRelativeLink(source, page, ({ href, ...props }) => {
    // Resolve asset references against the source page before directory-style
    // URLs reach the browser. Fumadocs already resolves documentation links.
    const asset = href && !href.startsWith('/') && !href.startsWith('#') && !URL.canParse(href)
      ? new URL(href, `https://microck.github.io${page.url}`)
      : undefined;
    return <defaultMdxComponents.a {...props} href={asset ? `${asset.pathname}${asset.search}${asset.hash}` : href} />;
  });
  return (
    <DocsPage toc={page.data.toc}>
      <DocsBody>
        <MDX components={getMDXComponents({ a: Link })} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(props: PageProps<'/docs/[[...slug]]'>): Promise<Metadata> {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();
  return { title: page.data.title, description: page.data.description };
}
