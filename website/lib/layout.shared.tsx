import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import Image from 'next/image';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <span className="inline-flex items-center gap-2">
          <Image src="/raft-logo.png" alt="" width={36} height={36} unoptimized className="size-9 object-contain" />
          <span>raft / docs</span>
        </span>
      ),
    },
    githubUrl: 'https://github.com/Microck/raft',
    themeSwitch: { enabled: true },
  };
}
