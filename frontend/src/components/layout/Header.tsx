interface HeaderProps {
  onMenuClick: () => void;
}

export default function Header({ onMenuClick }: HeaderProps) {
  const docsUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '') + '/docs';

  return (
    <header className="h-14 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]/80 backdrop-blur-md flex items-center px-4 md:px-6 sticky top-0 z-40">
      {/* Menu toggle button on mobile */}
      <button
        onClick={onMenuClick}
        className="p-2 -ml-2 mr-2 rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-card)] md:hidden focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-indigo)]/50"
        aria-label="Open navigation menu"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      <div className="flex-1 min-w-0">
        <h2 className="text-sm font-medium text-[var(--color-text-secondary)] truncate">
          Collaborative Fraud Intelligence Simulator
        </h2>
      </div>
      <div className="flex items-center gap-3 md:gap-4 shrink-0">
        <a
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          API Docs ↗
        </a>
        <div className="w-px h-4 bg-[var(--color-border)]" />
        <span className="text-xs font-mono text-[var(--color-text-muted)]">v0.1.0</span>
      </div>
    </header>
  );
}

