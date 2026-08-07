import React from 'react';
import { BankResult } from '../../api/types';

// Lightweight self-contained inline SVG icon components
const AwardIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="8" r="7" />
    <polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88" />
  </svg>
);

const DollarSignIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="1" x2="12" y2="23" />
    <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
  </svg>
);

const TrendingUpIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="23 6 13.5 15.5 8.5 10.5 1 18" />
    <polyline points="17 6 23 6 23 12" />
  </svg>
);

const ShieldAlertIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const CheckCircleIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
    <polyline points="22 4 12 14.01 9 11.01" />
  </svg>
);

const AlertOctagonIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </svg>
);

const UsersIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

interface IncentiveRegistryPanelProps {
  banks?: BankResult[];
}

const DEFAULT_INCENTIVE_BANKS: BankResult[] = [
  {
    id: 'bank_alpha',
    name: 'Bank Alpha',
    tier: 'Tier 1',
    fraud_ratio: 0.008,
    num_transactions: 125400,
    status: 'ACTIVE',
    contribution_score: 0.4281,
    quarantined: false,
    local_metrics: null,
    federated_metrics: null,
    improvement: null,
    data_profile: null,
  },
  {
    id: 'bank_beta',
    name: 'Bank Beta',
    tier: 'Tier 1',
    fraud_ratio: 0.025,
    num_transactions: 98200,
    status: 'ACTIVE',
    contribution_score: 0.3510,
    quarantined: false,
    local_metrics: null,
    federated_metrics: null,
    improvement: null,
    data_profile: null,
  },
  {
    id: 'bank_gamma',
    name: 'Bank Gamma',
    tier: 'Tier 2',
    fraud_ratio: 0.012,
    num_transactions: 45600,
    status: 'ACTIVE',
    contribution_score: 0.2209,
    quarantined: false,
    local_metrics: null,
    federated_metrics: null,
    improvement: null,
    data_profile: null,
  },
];

export const IncentiveRegistryPanel: React.FC<IncentiveRegistryPanelProps> = ({ banks }) => {
  const activeBanks = banks && banks.length > 0 ? banks : DEFAULT_INCENTIVE_BANKS;

  // Compute total positive contribution score for relative weights
  const totalPositiveScore = activeBanks.reduce((sum, bank) => {
    const score = bank.contribution_score || 0;
    return score > 0 ? sum + score : sum;
  }, 0);

  // Set total pool value for incentive calculation
  const totalPoolUSD = 100000;

  return (
    <div className="glass-card p-6 shadow-2xl text-slate-100 mb-8 overflow-hidden relative border border-[var(--color-border)]">
      {/* Decorative gradient overlay */}
      <div className="absolute top-0 right-0 w-80 h-80 bg-indigo-500/10 rounded-full blur-3xl -z-10 pointer-events-none" />
      <div className="absolute bottom-0 left-0 w-60 h-60 bg-emerald-500/5 rounded-full blur-3xl -z-10 pointer-events-none" />

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-[var(--color-border-subtle)] pb-5 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <AwardIcon className="h-6 w-6 text-emerald-400" />
            <h2 className="text-xl font-bold tracking-tight text-[var(--color-text-primary)]">Consortium Incentive Registry</h2>
          </div>
          <p className="text-xs sm:text-sm text-[var(--color-text-muted)]">
            Algorithmic contribution auditing via Leave-One-Out (LOO) Federated Shapley Value (SV) estimation.
          </p>
        </div>
        <div className="bg-slate-800/80 border border-slate-700/50 px-4 py-2.5 rounded-xl flex items-center gap-3 shrink-0">
          <UsersIcon className="h-5 w-5 text-indigo-400" />
          <div>
            <div className="text-[10px] text-slate-400 font-semibold uppercase tracking-wider">Consortium Members</div>
            <div className="text-sm font-bold text-white">{activeBanks.length} Nodes Registered</div>
          </div>
        </div>
      </div>

      {/* Warning if any bank is quarantined */}
      {activeBanks.some((b) => b.quarantined) && (
        <div className="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 mb-6 flex items-start gap-3">
          <ShieldAlertIcon className="h-6 w-6 text-rose-400 shrink-0 mt-0.5" />
          <div>
            <h3 className="text-sm font-bold text-rose-200">Security Alert: Client Isolation Triggered</h3>
            <p className="text-xs text-rose-300/80 mt-1 leading-relaxed">
              Automated contribution auditing has flagged adversarial or zero-variance training behavior. One or more clients have been isolated (quarantined) and excluded from the network to prevent gradient poisoning and free-riding.
            </p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Contribution List & Shapley Values */}
        <div className="lg:col-span-2 space-y-4">
          <h3 className="text-xs font-bold text-[var(--color-text-secondary)] flex items-center gap-1.5 uppercase tracking-wider mb-2">
            <TrendingUpIcon className="h-4 w-4 text-indigo-400" />
            Federated Shapley Contribution
          </h3>

          {activeBanks.map((bank) => {
            const rawScore = bank.contribution_score || 0;
            const isQuarantined = bank.quarantined;

            // Calculate relative share
            const sharePercent = totalPositiveScore > 0 && rawScore > 0
              ? (rawScore / totalPositiveScore) * 100
              : 0;

            return (
              <div
                key={bank.id}
                className={`bg-slate-800/40 border p-4 rounded-xl transition-all duration-300 hover:bg-slate-800/60 ${
                  isQuarantined
                    ? 'border-rose-900/50 bg-rose-950/10'
                    : 'border-slate-800/80'
                }`}
              >
                <div className="flex items-center justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={`h-2.5 w-2.5 rounded-full ${
                        isQuarantined
                          ? 'bg-rose-500 animate-pulse'
                          : 'bg-emerald-500'
                      }`}
                    />
                    <span className="font-semibold text-slate-100 text-sm">{bank.name}</span>
                    <span className="text-[10px] bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-slate-400 font-mono capitalize">
                      {bank.tier || 'Tier 1'}
                    </span>
                  </div>
                  <div className="text-right">
                    <span className="text-xs text-slate-400">LOO Shapley: </span>
                    <span
                      className={`font-mono text-sm font-bold ${
                        rawScore > 0
                          ? 'text-emerald-400'
                          : rawScore < -0.01
                          ? 'text-rose-400'
                          : 'text-slate-400'
                      }`}
                    >
                      {rawScore > 0 ? `+${rawScore.toFixed(4)}` : rawScore.toFixed(4)}
                    </span>
                  </div>
                </div>

                {/* Progress bar container */}
                <div className="relative w-full h-3 bg-slate-900 border border-slate-800 rounded-full overflow-hidden mb-2">
                  <div
                    className={`h-full rounded-full transition-all duration-1000 ${
                      isQuarantined
                        ? 'bg-gradient-to-r from-rose-600 to-rose-500'
                        : 'bg-gradient-to-r from-indigo-500 via-teal-400 to-emerald-400'
                    }`}
                    style={{ width: `${isQuarantined ? 100 : Math.max(4, sharePercent)}%` }}
                  />
                </div>

                <div className="flex justify-between items-center text-xs">
                  <span className="text-slate-400">
                    {isQuarantined ? (
                      <span className="text-rose-400 font-semibold flex items-center gap-1">
                        <AlertOctagonIcon className="h-3.5 w-3.5" /> Isolated (Quarantined)
                      </span>
                    ) : (
                      `Marginal Pool Share: ${sharePercent.toFixed(1)}%`
                    )}
                  </span>
                  <span className="text-slate-400 font-mono">
                    Transactions: {(bank.num_transactions || 50000).toLocaleString()}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Clearing House / Incentive Payouts */}
        <div className="bg-slate-950/60 border border-slate-800/80 rounded-xl p-5 flex flex-col justify-between">
          <div>
            <h3 className="text-xs font-bold text-[var(--color-text-secondary)] flex items-center gap-1.5 uppercase tracking-wider mb-3">
              <DollarSignIcon className="h-4 w-4 text-emerald-400" />
              Consortium Clearing Ledger
            </h3>

            <p className="text-xs text-slate-400 mb-4 leading-relaxed">
              Calculates financial compensation metrics based on statistical model values. Banks contributing higher risk signals receive proportional clearing payouts.
            </p>

            <div className="space-y-3">
              {activeBanks.map((bank) => {
                const rawScore = bank.contribution_score || 0;
                const isQuarantined = bank.quarantined;

                // Calculate payout share
                const payoutShare = totalPositiveScore > 0 && rawScore > 0
                  ? (rawScore / totalPositiveScore) * totalPoolUSD
                  : 0;

                return (
                  <div key={bank.id} className="flex justify-between items-center border-b border-slate-900 pb-2.5">
                    <div>
                      <div className="text-xs font-semibold text-slate-200">{bank.name}</div>
                      <div className="text-[10px] text-slate-500 font-mono">
                        {isQuarantined ? 'Quarantined' : `${((rawScore / (totalPositiveScore || 1)) * 100).toFixed(1)}% share`}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className={`text-sm font-bold font-mono ${isQuarantined ? 'text-rose-400/70 line-through' : 'text-emerald-400'}`}>
                        ${payoutShare.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                      <div className="text-[9px] text-slate-500">USD Equivalent</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="border-t border-slate-900 pt-4 mt-6 space-y-2">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="text-slate-400">Total Clearing Pool:</span>
              <span className="font-mono text-emerald-400 font-bold text-sm">${totalPoolUSD.toLocaleString()} USD</span>
            </div>
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="text-slate-400">Audit Ledger Log:</span>
              <span className="text-emerald-400 font-semibold text-xs flex items-center gap-1">
                <CheckCircleIcon className="h-3.5 w-3.5" /> SHA-256 Signed
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
