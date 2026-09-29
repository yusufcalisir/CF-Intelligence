import React from 'react';
import { OnChainPayout } from '../../api/types';

// Self-contained inline SVG icons
const ShieldCheckIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

const LayersIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polygon points="12 2 2 7 12 12 22 7 12 2" />
    <polyline points="2 17 12 22 22 17" />
    <polyline points="2 12 12 17 22 12" />
  </svg>
);

const FileCodeIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
    <polyline points="14 2 14 8 20 8" />
    <path d="m10 13-2 2 2 2" />
    <path d="m14 13 2 2-2 2" />
  </svg>
);

const CheckCircle2Icon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <path d="m9 12 2 2 4-4" />
  </svg>
);

const AlertTriangleIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </svg>
);

const ExternalLinkIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </svg>
);

const CoinsIcon: React.FC<{ className?: string }> = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="6" />
    <path d="M18 0.9A6 6 0 0 1 18 15.1" />
    <path d="M14.8 19.3A6 6 0 0 1 6.7 19.3" />
  </svg>
);

interface Web3SettlementPanelProps {
  enableWeb3Settlement?: boolean;
  settlementCurrency?: string;
  smartContractAddress?: string;
  settlementTxHash?: string | null;
  settlementBlockNumber?: number | null;
  settlementStatus?: string | null;
  onChainPayouts?: OnChainPayout[];
}

export const formatWalletAddress = (addr?: string | null): string => {
  if (!addr || typeof addr !== 'string') return '0x0000...0000';
  const trimmed = addr.trim();
  if (trimmed.length <= 12) return trimmed;
  return `${trimmed.substring(0, 8)}...${trimmed.substring(trimmed.length - 6)}`;
};

export const Web3SettlementPanel: React.FC<Web3SettlementPanelProps> = ({
  enableWeb3Settlement = true,
  settlementCurrency = 'wCBDC',
  smartContractAddress = '0x71C7656EC7ab88b098defB751B7401B5f6d8976F',
  settlementTxHash,
  settlementBlockNumber,
  settlementStatus,
  onChainPayouts = [],
}) => {
  if (!enableWeb3Settlement && !settlementTxHash) {
    return null;
  }

  return (
    <div className="bg-gradient-to-br from-slate-900/90 via-slate-800/90 to-indigo-950/80 backdrop-blur-md rounded-xl border border-indigo-500/30 p-6 shadow-2xl space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-700/60">
        <div className="flex items-center space-x-3">
          <div className="p-3 bg-indigo-500/20 rounded-lg border border-indigo-400/30">
            <CoinsIcon className="w-6 h-6 text-indigo-400" />
          </div>
          <div>
            <h3 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
              Automated Smart Contract Settlement
              <span className="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono">
                EVM Web3 / CBDC
              </span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Real-time clearing house incentive disbursement driven by Leave-One-Out (LOO) Federated Shapley Values
            </p>
          </div>
        </div>

        {/* Currency badge */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Settlement Asset:</span>
          <span className="px-3 py-1 bg-indigo-600/30 text-indigo-200 border border-indigo-400/40 text-sm font-semibold rounded-md font-mono flex items-center gap-1.5">
            <CoinsIcon className="w-3.5 h-3.5 text-indigo-300" />
            {settlementCurrency}
          </span>
        </div>
      </div>

      {/* Contract & Execution Metadata Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Contract Address */}
        <div className="bg-slate-800/70 rounded-lg p-3.5 border border-slate-700/60 space-y-1">
          <div className="flex items-center text-xs font-medium text-slate-400 space-x-1.5">
            <FileCodeIcon className="w-3.5 h-3.5 text-indigo-400" />
            <span>Smart Contract Address</span>
          </div>
          <p className="text-xs font-mono text-indigo-300 truncate" title={smartContractAddress}>
            {smartContractAddress}
          </p>
        </div>

        {/* Transaction Hash */}
        <div className="bg-slate-800/70 rounded-lg p-3.5 border border-slate-700/60 space-y-1">
          <div className="flex items-center text-xs font-medium text-slate-400 space-x-1.5">
            <ExternalLinkIcon className="w-3.5 h-3.5 text-emerald-400" />
            <span>On-Chain Tx Hash</span>
          </div>
          {settlementTxHash ? (
            <p className="text-xs font-mono text-emerald-300 truncate" title={settlementTxHash}>
              {settlementTxHash}
            </p>
          ) : (
            <p className="text-xs text-slate-500 italic">Pending simulation epoch settlement...</p>
          )}
        </div>

        {/* Block Height & Status */}
        <div className="bg-slate-800/70 rounded-lg p-3.5 border border-slate-700/60 space-y-1">
          <div className="flex items-center text-xs font-medium text-slate-400 space-x-1.5">
            <LayersIcon className="w-3.5 h-3.5 text-amber-400" />
            <span>Block Height & Status</span>
          </div>
          <div className="flex items-center space-x-2">
            <span className="text-xs font-mono text-slate-200">
              {settlementBlockNumber ? `Block #${settlementBlockNumber.toLocaleString()}` : 'Block #—'}
            </span>
            {settlementStatus === 'SUCCESS' && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-semibold flex items-center gap-1 border border-emerald-500/30">
                <CheckCircle2Icon className="w-3 h-3" /> CONFIRMED
              </span>
            )}
          </div>
        </div>
      </div>

      {/* On-Chain Payout Table */}
      {onChainPayouts.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-700/60 bg-slate-900/60">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-800/80 text-slate-300 font-semibold border-b border-slate-700/80">
              <tr>
                <th className="py-2.5 px-3">Consortium Bank</th>
                <th className="py-2.5 px-3">Wallet Address</th>
                <th className="py-2.5 px-3 text-right">Shapley Score</th>
                <th className="py-2.5 px-3 text-right">Share (%)</th>
                <th className="py-2.5 px-3 text-right">Payout ({settlementCurrency})</th>
                <th className="py-2.5 px-3 text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-300 font-mono">
              {onChainPayouts.map((payout, idx) => (
                <tr
                  key={idx}
                  className={`hover:bg-slate-800/50 transition-colors ${
                    payout.is_quarantined ? 'bg-amber-950/10' : ''
                  }`}
                >
                  <td className="py-2.5 px-3 font-sans font-medium text-white flex items-center space-x-2">
                    <span>{payout.bank_name || 'Consortium Bank'}</span>
                  </td>
                  <td className="py-2.5 px-3 text-slate-400 text-[11px]" title={payout.wallet_address || 'Unspecified Address'}>
                    {formatWalletAddress(payout.wallet_address)}
                  </td>
                  <td className="py-2.5 px-3 text-right font-semibold">
                    <span className={(payout.shapley_score ?? 0) < 0 ? 'text-rose-400' : 'text-emerald-400'}>
                      {(payout.shapley_score ?? 0).toFixed(4)} ({payout.shapley_basis_points ?? 0} bps)
                    </span>
                  </td>
                  <td className="py-2.5 px-3 text-right font-medium text-slate-300">
                    {(payout.share_percent ?? 0).toFixed(2)}%
                  </td>
                  <td className="py-2.5 px-3 text-right font-bold text-indigo-300">
                    ${(payout.payout_usd ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="py-2.5 px-3 text-center">
                    {payout.is_quarantined ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold bg-rose-500/20 text-rose-300 border border-rose-500/30 rounded">
                        <AlertTriangleIcon className="w-3 h-3 text-rose-400" /> QUARANTINED
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 rounded">
                        <ShieldCheckIcon className="w-3 h-3 text-emerald-400" /> SETTLED
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-center py-6 bg-slate-800/40 rounded-lg border border-slate-700/50">
          <p className="text-xs text-slate-400">
            No smart contract settlements executed for this simulation yet. Enable Web3 Settlement in controls to disburse automated token rewards.
          </p>
        </div>
      )}
    </div>
  );
};
