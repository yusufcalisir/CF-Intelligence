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
      { path: '/benchmarks', label: 'Real Benchmarks & Pilot', icon: '⚡' },
      { path: '/developer', label: 'Developer & API Portal', icon: '📖' },
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
        isExternal: true 
      },

      { 
        href: import.meta.env.VITE_JAEGER_URL ?? 'https://curiousheather2678.grafana.net/explore', 
        label: 'Jaeger Tracing (Tempo)', 
        icon: '🔍', 
        isExternal: true 
      },
      { 
        href: import.meta.env.VITE_PROMETHEUS_URL ?? 'https://curiousheather2678.grafana.net/explore', 
        label: 'Prometheus Metrics', 
        icon: '🔥', 
        isExternal: true 
      },
    ],
  },
];


interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const location = useLocation();

  // Close sidebar on navigation change (for mobile viewport)
  useEffect(() => {
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);


  const visibleSections = NAV_SECTIONS;

  return (
    <>
      {/* Mobile Backdrop Overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden transition-opacity duration-300"
          onClick={onClose}
        />
      )}

      <aside
        className={`fixed top-0 left-0 h-screen w-64 bg-[var(--color-bg-secondary)] border-r border-[var(--color-border)] flex flex-col z-50 transition-transform duration-300 md:translate-x-0 ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {/* Back to site & Logo */}
        <div className="px-3 pt-2 pb-0">
          <a
            href="/"
            className="inline-flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-accent-indigo-light)] transition-colors duration-150"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back to site
          </a>
        </div>
        <div className="p-3 border-b border-[var(--color-border)] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <img 
              src="/logo.svg" 
              className="w-8 h-8 object-contain shrink-0" 
              alt="CFI Logo" 
            />
            <div>
              <h1 className="text-xs font-bold text-[var(--color-text-primary)] leading-tight">
                Fraud Intelligence
              </h1>
              <p className="text-[10px] text-[var(--color-text-muted)]">Federated Simulator</p>
            </div>
          </div>

          {/* Close button on mobile */}
          <button
            onClick={onClose}
            className="p-1 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-card)] md:hidden focus:outline-none"
            aria-label="Close menu"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-2 space-y-1.5 overflow-y-auto">
          {visibleSections.map((section) => (
            <div key={section.label}>
              <p className="px-2 mb-1 text-[9px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                {section.label}
              </p>
              <ul className="space-y-0.5">
                {section.items.map((item) => {
                  if ('isExternal' in item && item.isExternal) {
                    return (
                      <li key={item.label}>
                        <a
                          href={item.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-card)] transition-all duration-200"
                        >
                          <span className="text-sm">{item.icon}</span>
                          {item.label}
                          <span className="text-[9px] text-[var(--color-text-muted)] ml-auto">↗</span>
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
                        className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 ${
                          isActive
                            ? 'bg-[var(--color-accent-indigo)]/15 text-[var(--color-accent-indigo-light)] border border-[var(--color-accent-indigo)]/30'
                            : 'text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-card)]'
                        }`}
                      >
                        <span className="text-sm">{item.icon}</span>
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="p-2 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
          <div className="glass-card p-2.5 space-y-1.5">
            <p className="text-[10px] text-[var(--color-text-muted)] leading-normal">
              Privacy-preserving cross-institution fraud detection via Federated Learning
            </p>
            <div className="flex items-center gap-2 pt-0.5">
              <span className="status-dot status-dot--active" />
              <span className="text-xs font-semibold text-[var(--color-text-secondary)]">System Online</span>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
