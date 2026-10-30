import { useState, useEffect, useContext } from 'react';
import { QueryClientContext } from '@tanstack/react-query';
import { useRealTimeFraudStream } from '../../hooks/useRealTimeFraudStream';
import { apiClient, formatApiError } from '../../api/client';
import { Activity, Radio, ShieldCheck, LogIn, LogOut } from 'lucide-react';

interface HeaderProps {
  onMenuClick: () => void;
}

interface SessionUserProfile {
  username: string;
  bank_id: string;
  roles: string[];
  clearance_level?: number;
}

export default function Header({ onMenuClick }: HeaderProps) {
  const docsUrl = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '') + '/docs';
  const { status, streamSource, streamProvenance, latencyMs, totalStreamedTransactions } = useRealTimeFraudStream();
  const queryClient = useContext(QueryClientContext);

  const [sessionUser, setSessionUser] = useState<SessionUserProfile | null>(() => {
    try {
      const raw = localStorage.getItem('cfi_session_user');
      if (raw) {
        return JSON.parse(raw) as SessionUserProfile;
      }
    } catch {
      // ignore storage errors
    }
    return null;
  });
  const [isLoginModalOpen, setIsLoginModalOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const syncAuthFromStorage = async () => {
      try {
        const token = localStorage.getItem('cfi_token');
        const rawUser = localStorage.getItem('cfi_session_user');
        if (!token) {
          setSessionUser(null);
          return;
        }
        if (rawUser) {
          setSessionUser(JSON.parse(rawUser) as SessionUserProfile);
          return;
        }
        const { data } = await apiClient.get<SessionUserProfile>('/api/v1/auth/me');
        if (data?.username) {
          localStorage.setItem('cfi_session_user', JSON.stringify(data));
          if (data.bank_id) {
            localStorage.setItem('cfi_tenant_id', data.bank_id);
          }
          setSessionUser(data);
        }
      } catch {
        // Ignore unauthenticated or offline status on initial mount
      }
    };

    void syncAuthFromStorage();
    const handleStorageChange = () => {
      void syncAuthFromStorage();
    };
    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('cfi-auth-changed', handleStorageChange);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('cfi-auth-changed', handleStorageChange);
    };
  }, []);

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoginError(null);
    setIsSubmitting(true);
    try {
      const { data } = await apiClient.post<{
        access_token: string;
        refresh_token?: string;
        user: SessionUserProfile;
      }>('/api/v1/auth/login', {
        username: username.trim(),
        password,
      });

      localStorage.setItem('cfi_token', data.access_token);
      if (data.refresh_token) {
        localStorage.setItem('cfi_refresh_token', data.refresh_token);
      }
      if (data.user?.bank_id) {
        localStorage.setItem('cfi_tenant_id', data.user.bank_id);
      }
      localStorage.setItem('cfi_session_user', JSON.stringify(data.user));
      setSessionUser(data.user);
      setPassword('');
      setIsLoginModalOpen(false);
      window.dispatchEvent(new Event('cfi-auth-changed'));
      await queryClient?.resetQueries();
      await queryClient?.invalidateQueries();
    } catch (err: unknown) {
      setLoginError(formatApiError(err, 'Institutional authentication failed.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await apiClient.post('/api/v1/auth/logout');
    } catch {
      // Proceed with local session cleanup even if backend logout fails
    }
    try {
      localStorage.removeItem('cfi_token');
      localStorage.removeItem('cfi_refresh_token');
      localStorage.removeItem('cfi_tenant_id');
      localStorage.removeItem('cfi_session_user');
    } catch {
      // ignore storage errors
    }
    setSessionUser(null);
    window.dispatchEvent(new Event('cfi-auth-changed'));
    await queryClient?.resetQueries();
    await queryClient?.invalidateQueries();
  };

  return (
    <>
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
              streamSource === 'LIVE_CONNECTOR' ? (
                <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30" title={`Live Ingestion Provider: ${streamProvenance}`}>
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                  Live Connector WS ({latencyMs}ms)
                </span>
              ) : streamSource === 'SIMULATED' ? (
                <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30" title="Simulated Sandbox Demo Feed (Synthetic transactions)">
                  <Radio className="w-2.5 h-2.5 text-indigo-400 animate-pulse" />
                  Simulated Feed ({latencyMs}ms)
                </span>
              ) : (
                <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-amber-500/15 text-amber-300 border border-amber-500/30" title="WebSocket connected, but no live telemetry provider is currently configured.">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                  Telemetry Standby ({latencyMs}ms)
                </span>
              )
            ) : status === 'mock_active' ? (
              <span className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-500/30" title="WebSocket disconnected. Deterministic simulated sandbox telemetry.">
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

        <div className="flex items-center gap-2.5 md:gap-3 shrink-0">
          {sessionUser ? (
            <div className="flex items-center gap-2">
              <span
                data-testid="active-institutional-session"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-mono font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"
              >
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                <span>{sessionUser.username}</span>
                <span className="text-emerald-400/70">·</span>
                <span data-testid="active-session-tenant">{sessionUser.bank_id.toUpperCase()}</span>
              </span>
              <button
                type="button"
                data-testid="institutional-sign-in-btn"
                onClick={() => {
                  setLoginError(null);
                  setIsLoginModalOpen(true);
                }}
                className="px-2 py-1 rounded-lg text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors"
              >
                Switch Principal
              </button>
              <button
                type="button"
                data-testid="institutional-sign-out-btn"
                onClick={handleSignOut}
                className="p-1.5 rounded-lg text-slate-400 hover:text-red-300 hover:bg-red-500/10 border border-transparent hover:border-red-500/30 transition-colors"
                title="Sign out of institutional session"
              >
                <LogOut className="w-3.5 h-3.5" />
              </button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="institutional-sign-in-btn"
              onClick={() => {
                setLoginError(null);
                setIsLoginModalOpen(true);
              }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/40 transition-colors"
            >
              <LogIn className="w-3.5 h-3.5" />
              <span>Institutional Sign In</span>
            </button>
          )}
          <div className="w-px h-4 bg-[var(--color-border)]" />
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

      {isLoginModalOpen && (
        <div
          data-testid="institutional-login-modal"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
        >
          <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-base font-bold text-slate-100">Institutional Session Authentication</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Authenticate with your sovereign institutional credentials to bind tenant context.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsLoginModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 text-sm"
              >
                ✕
              </button>
            </div>

            {loginError && (
              <div
                data-testid="institutional-login-error"
                className="mb-4 rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-xs text-red-300"
              >
                {loginError}
              </div>
            )}

            <form onSubmit={handleLoginSubmit} className="space-y-4">
              <div>
                <label
                  htmlFor="institutional-username-input"
                  className="block text-xs font-medium text-slate-300 mb-1"
                >
                  Principal Username
                </label>
                <input
                  id="institutional-username-input"
                  data-testid="input-institutional-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="e.g., analyst_bank_a"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 font-mono focus:border-indigo-500 focus:outline-none"
                  required
                />
              </div>

              <div>
                <label
                  htmlFor="institutional-password-input"
                  className="block text-xs font-medium text-slate-300 mb-1"
                >
                  Institutional Password
                </label>
                <input
                  id="institutional-password-input"
                  data-testid="input-institutional-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter institutional credential"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 font-mono focus:border-indigo-500 focus:outline-none"
                  required
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  data-testid="btn-cancel-institutional-login"
                  onClick={() => setIsLoginModalOpen(false)}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3.5 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  data-testid="btn-submit-institutional-login"
                  disabled={isSubmitting || !username.trim() || !password}
                  className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
                >
                  {isSubmitting ? 'Authenticating...' : 'Sign In'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
