import { RootProvider } from 'fumadocs-ui/provider/next';
import type { Metadata } from 'next';
import localFont from 'next/font/local';
import StaticSearch from '@/components/search';
import './global.css';

const inter = localFont({
  src: [
    { path: '../public/fonts/inter-regular.woff2', weight: '400', style: 'normal' },
    { path: '../public/fonts/inter-bold.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-inter',
  display: 'swap',
});

const funnelDisplay = localFont({
  src: '../public/fonts/funnel-display-regular.woff2',
  weight: '400',
  variable: '--font-funnel-display',
  display: 'swap',
});

export const metadata: Metadata = {
  metadataBase: new URL('https://raft.micr.dev'),
  title: { default: 'Raft documentation', template: '%s | Raft' },
  description: 'Self-hosted persistent Linux workspaces with Incus. An independent open-source alternative to boat.dev.',
  icons: { icon: { url: '/raft-logo.png', type: 'image/png' } },
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={`${inter.variable} ${funnelDisplay.variable}`} suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider search={{ SearchDialog: StaticSearch }} theme={{ defaultTheme: 'light', enableSystem: false }}>{children}</RootProvider>
      </body>
    </html>
  );
}
