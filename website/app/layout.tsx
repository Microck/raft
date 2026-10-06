import { RootProvider } from 'fumadocs-ui/provider/next';
import type { Metadata } from 'next';
import localFont from 'next/font/local';
import StaticSearch from '@/components/search';
import './global.css';

const spaceGrotesk = localFont({
  src: [
    { path: '../public/fonts/space-grotesk-regular.woff2', weight: '400', style: 'normal' },
    { path: '../public/fonts/space-grotesk-medium.woff2', weight: '500', style: 'normal' },
    { path: '../public/fonts/space-grotesk-bold.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-space-grotesk',
  display: 'swap',
});

export const metadata: Metadata = {
  metadataBase: new URL('https://raft.micr.dev'),
  title: { default: 'Raft documentation', template: '%s | Raft' },
  description: 'Self-hosted persistent Linux workspaces with Incus. An independent open-source alternative to boat.dev.',
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={`dark ${spaceGrotesk.variable}`} suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider search={{ SearchDialog: StaticSearch }} theme={{ forcedTheme: 'dark', enableSystem: false }}>{children}</RootProvider>
      </body>
    </html>
  );
}
