import { useRealTimeFraudStream } from '../../hooks/useRealTimeFraudStream';
import { Activity, Radio } from 'lucide-react';

interface HeaderProps {
  onMenuClick: () => void;
}

export default function Header({ onMenuClick }: HeaderProps) {
  const docsUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '') + '/docs';
  const { status, latencyMs, totalStreamedTransactions } = useRealTimeFraudStream();

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

      <div className="flex-1 min-w-0 flex items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-100 truncate">
          Collaborative Fraud Intelligence Platform
        </h2>

        {/* Real-time WebSocket status badge */}
        <div className="hidden sm:flex items-center gap-2">
          {status === 'connected' ? (
            <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
              Live WS ({latencyMs}ms)
            </span>
          ) : status === 'mock_active' ? (
            <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30" title="WebSocket disconnected. Serving deterministic simulated sandbox telemetry.">
              <Radio className="w-2.5 h-2.5 text-indigo-400 animate-pulse" />
              Simulated Stream (Offline)
            </span>
          ) : status === 'connecting' ? (
            <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/30">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
              Connecting...
            </span>
          ) : (
            <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-slate-500/15 text-slate-400 border border-slate-500/30">
              WS Offline
            </span>
          )}

          {/* Live streamed transactions ticker */}
          <span className="hidden lg:flex items-center gap-1 text-[11px] font-mono text-slate-400">
            <Activity className="w-3 h-3 text-cyan-400" />
            <span>{totalStreamedTransactions.toLocaleString()} txns</span>
          </span>
        </div>
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
        <span className="text-xs font-mono text-[var(--color-text-muted)]">v2.4.1</span>
      </div>
    </header>
  );
}
