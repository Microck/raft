import { RootProvider } from 'fumadocs-ui/provider/next';
import type { Metadata } from 'next';
import StaticSearch from '@/components/search';
import './global.css';

export const metadata: Metadata = {
  title: { default: 'Raft documentation', template: '%s | Raft' },
  description: 'Self-hosted persistent Linux workspaces with Incus. An independent open-source alternative to boat.dev.',
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider search={{ SearchDialog: StaticSearch }} theme={{ forcedTheme: 'dark', enableSystem: false }}>{children}</RootProvider>
      </body>
    </html>
  );
}
