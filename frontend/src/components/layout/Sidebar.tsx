import { useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';

const NAV_SECTIONS = [
  {
    label: 'Live Consortium Operations',
    items: [
      { path: '/dashboard', label: 'Dashboard', icon: '◈' },
      { path: '/operations', label: 'Live Operations', icon: '📡' },
    ],
  },
  {
    label: 'AML Intelligence',
    items: [
      { path: '/investigation', label: 'Investigation', icon: '📊' },
      { path: '/alerts', label: 'Alerts', icon: '🚨' },
      { path: '/cases', label: 'Cases', icon: '📋' },
      { path: '/rules', label: 'Policy Rules', icon: '🛡️' },
      { path: '/psi', label: 'Fuzzy PSI Matching', icon: '🤝' },
      { path: '/security', label: 'Security & Compliance', icon: '🔒' },
      { path: '/scenarios', label: 'Scenarios', icon: '▶️' },
      { path: '/graph', label: 'Entity Graph', icon: '🕸️' },
    ],
  },
  {
    label: 'Enterprise Platform',
    items: [
      { path: '/onboarding', label: 'Onboard Bank', icon: '🏛️' },
      { path: '/coordinator', label: 'FL Coordinator Suite', icon: '🛰️' },
      { path: '/privacy-defense', label: 'Privacy Defense Suite', icon: '🔐' },
    ],
  },
  {
    label: 'Observability',
    items: [
      { path: '/observability', label: 'Observability & Drift', icon: '📊' },
      {
        href: import.meta.env.VITE_GRAFANA_URL ?? 'https://curiousheather2678.grafana.net/d/cfi-overview/cfi-platform-overview',
        label: 'Grafana Dashboards',
        icon: '📈',
        isExternal: true,
      },
      {
        href: import.meta.env.VITE_JAEGER_URL ?? 'https://curiousheather2678.grafana.net/explore',
        label: 'Jaeger Tracing (Tempo)',
        icon: '🔍',
        isExternal: true,
      },
      {
        href: import.meta.env.VITE_PROMETHEUS_URL ?? 'https://curiousheather2678.grafana.net/explore',
        label: 'Prometheus Metrics',
        icon: '🔥',
        isExternal: true,
      },
    ],
  },
];

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

const S = {
  aside: (isOpen: boolean): React.CSSProperties => ({
    position: 'fixed',
    top: 0,
    left: 0,
    height: '100vh',
    width: '256px',
    backgroundColor: '#090d1c',
    borderRight: '1px solid rgba(99,102,241,0.15)',
    display: 'flex',
    flexDirection: 'column',
    zIndex: 50,
    transform: isOpen ? 'translateX(0)' : undefined,
    transition: 'transform 0.3s ease',
  }),
  sectionLabel: {
    padding: '0 0.5rem',
    marginBottom: '0.25rem',
    fontSize: '9px',
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.08em',
    color: '#64748b',
  },
  navItem: (active: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: '0.625rem',
    padding: '0.375rem 0.625rem',
    borderRadius: '0.5rem',
    fontSize: '0.75rem',
    fontWeight: 500,
    textDecoration: 'none',
    transition: 'background 0.15s, color 0.15s',
    background: active ? 'rgba(99,102,241,0.15)' : 'transparent',
    color: active ? '#818cf8' : '#94a3b8',
    border: active ? '1px solid rgba(99,102,241,0.25)' : '1px solid transparent',
  }),
};

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const location = useLocation();

  useEffect(() => {
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  return (
    <>
      {/* Mobile backdrop */}
      {isOpen && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.65)',
            backdropFilter: 'blur(4px)',
            zIndex: 40,
          }}
          className="md:hidden"
        />
      )}

      <aside style={S.aside(isOpen)} className="md:translate-x-0">
        {/* Top bar — back link + logo */}
        <div style={{ padding: '0.625rem 0.75rem', borderBottom: '1px solid rgba(99,102,241,0.12)' }}>
          {/* Back to landing */}
          <Link
            to="/"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              fontSize: '10px',
              color: '#64748b',
              textDecoration: 'none',
              marginBottom: '0.5rem',
              transition: 'color 0.15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = '#818cf8')}
            onMouseLeave={e => (e.currentTarget.style.color = '#64748b')}
            title="Back to landing page"
          >
            <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            Back to site
          </Link>

          {/* Logo row */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
              <img
                src="/favicon.svg"
                style={{ width: 32, height: 32, borderRadius: '50%', border: '1px solid rgba(99,102,241,0.3)', objectFit: 'cover' }}
                alt="CFI Logo"
              />
              <div>
                <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#e2e8f0', lineHeight: 1.2 }}>
                  Fraud Intelligence
                </div>
                <div style={{ fontSize: '10px', color: '#64748b' }}>Federated Simulator</div>
              </div>
            </div>
            {/* Mobile close */}
            <button
              onClick={onClose}
              className="md:hidden"
              style={{ padding: '0.25rem', background: 'transparent', border: 'none', color: '#64748b', cursor: 'pointer', borderRadius: '0.375rem' }}
              aria-label="Close menu"
            >
              <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Navigation */}
        <nav style={{ flex: 1, padding: '0.5rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <p style={S.sectionLabel}>{section.label}</p>
              <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {section.items.map((item) => {
                  if ('isExternal' in item && item.isExternal) {
                    return (
                      <li key={item.label}>
                        <a
                          href={item.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={S.navItem(false)}
                          onMouseEnter={e => {
                            (e.currentTarget as HTMLElement).style.background = 'rgba(99,102,241,0.08)';
                            (e.currentTarget as HTMLElement).style.color = '#e2e8f0';
                          }}
                          onMouseLeave={e => {
                            (e.currentTarget as HTMLElement).style.background = 'transparent';
                            (e.currentTarget as HTMLElement).style.color = '#94a3b8';
                          }}
                        >
                          <span style={{ fontSize: '0.875rem' }}>{item.icon}</span>
                          {item.label}
                          <span style={{ fontSize: '9px', color: '#64748b', marginLeft: 'auto' }}>↗</span>
                        </a>
                      </li>
                    );
                  }

                  const path = (item as any).path;
                  const isActive = location.pathname === path;
                  return (
                    <li key={path}>
                      <Link
                        to={path}
                        style={S.navItem(isActive)}
                        onMouseEnter={e => {
                          if (!isActive) {
                            (e.currentTarget as HTMLElement).style.background = 'rgba(99,102,241,0.08)';
                            (e.currentTarget as HTMLElement).style.color = '#e2e8f0';
                          }
                        }}
                        onMouseLeave={e => {
                          if (!isActive) {
                            (e.currentTarget as HTMLElement).style.background = 'transparent';
                            (e.currentTarget as HTMLElement).style.color = '#94a3b8';
                          }
                        }}
                      >
                        <span style={{ fontSize: '0.875rem' }}>{item.icon}</span>
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}

          {/* Federated Network Status */}
          <div style={{ paddingTop: '0.75rem', marginTop: '0.25rem', borderTop: '1px solid rgba(99,102,241,0.1)' }}>
            <p style={{ ...S.sectionLabel, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Federated Network</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981', display: 'inline-block' }} className="animate-pulse" />
                <span style={{ fontSize: '8px', color: '#64748b', fontWeight: 400, textTransform: 'normal' as any }}>Live</span>
              </span>
            </p>
            <div style={{ margin: '0 4px', padding: '0.5rem', borderRadius: '0.5rem', background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(99,102,241,0.1)' }}>
              {[['Bank A (Global)', true], ['Bank B (Regional)', true], ['Bank C (Local)', true]].map(([name, online]) => (
                <div key={name as string} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '10px', marginBottom: '4px' }}>
                  <span style={{ color: '#94a3b8' }}>{name}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', color: '#10b981', fontSize: '9px' }}>{online ? 'Online' : 'Offline'}</span>
                </div>
              ))}
            </div>
          </div>
        </nav>

        {/* Footer */}
        <div style={{ padding: '0.625rem', borderTop: '1px solid rgba(99,102,241,0.1)' }}>
          <div className="glass-card" style={{ padding: '0.5rem' }}>
            <p style={{ fontSize: '9px', color: '#64748b', lineHeight: 1.5, margin: 0 }}>
              Privacy-preserving cross-institution fraud detection via Federated Learning
            </p>
            <div style={{ marginTop: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
              <span className="status-dot status-dot--active" />
              <span style={{ fontSize: '10px', color: '#94a3b8' }}>System Online</span>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
