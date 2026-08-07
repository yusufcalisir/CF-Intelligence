interface HeaderProps {
  onMenuClick: () => void;
}

export default function Header({ onMenuClick }: HeaderProps) {
  const docsUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '') + '/docs';

  return (
    <header
      style={{
        height: '56px',
        borderBottom: '1px solid rgba(99,102,241,0.15)',
        background: 'rgba(9,13,28,0.85)',
        backdropFilter: 'blur(12px)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 1.5rem',
        position: 'sticky',
        top: 0,
        zIndex: 40,
        gap: '0.75rem',
      }}
    >
      {/* Mobile hamburger */}
      <button
        onClick={onMenuClick}
        style={{
          padding: '0.375rem',
          marginLeft: '-0.375rem',
          marginRight: '0.5rem',
          borderRadius: '0.5rem',
          border: 'none',
          background: 'transparent',
          color: 'var(--color-text-secondary)',
          cursor: 'pointer',
          display: 'none',
        }}
        className="md:hidden"
        aria-label="Open navigation menu"
      >
        <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      {/* Title */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            fontSize: '0.8125rem',
            fontWeight: 500,
            color: 'var(--color-text-secondary)',
            letterSpacing: '0.01em',
          }}
        >
          Collaborative Fraud Intelligence Simulator
        </span>
      </div>

      {/* Right side */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexShrink: 0 }}>
        <a
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            fontSize: '0.75rem',
            color: 'var(--color-text-muted)',
            textDecoration: 'none',
            transition: 'color 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--color-text-primary)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--color-text-muted)')}
        >
          API Docs ↗
        </a>
        <div
          style={{
            width: '1px',
            height: '16px',
            background: 'var(--color-border)',
          }}
        />
        <span
          style={{
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono)',
            color: 'var(--color-text-muted)',
          }}
        >
          v0.1.0
        </span>
      </div>
    </header>
  );
}
