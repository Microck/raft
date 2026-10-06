import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: { title: 'raft / docs' },
    githubUrl: 'https://github.com/Microck/raft',
    themeSwitch: { enabled: true },
  };
}
