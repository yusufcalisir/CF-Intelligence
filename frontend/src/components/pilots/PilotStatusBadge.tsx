import type { PilotStatus } from '../../api/types';

interface PilotStatusBadgeProps {
  status: PilotStatus | string;
  size?: 'sm' | 'md' | 'lg';
}

const STATUS_CONFIG: Record<string, { label: string; bg: string; text: string; border: string; dot: string }> = {
  DRAFT: {
    label: 'Draft',
    bg: 'bg-slate-800/60',
    text: 'text-slate-300',
    border: 'border-slate-700',
    dot: 'bg-slate-400',
  },
  PREFLIGHT: {
    label: 'Preflight Verification',
    bg: 'bg-purple-900/40',
    text: 'text-purple-300',
    border: 'border-purple-600/40',
    dot: 'bg-purple-400 animate-pulse',
  },
  AWAITING_APPROVAL: {
    label: 'Awaiting Approvals',
    bg: 'bg-amber-900/40',
    text: 'text-amber-300',
    border: 'border-amber-600/40',
    dot: 'bg-amber-400',
  },
  AUTHORIZED: {
    label: 'Fully Authorized',
    bg: 'bg-cyan-900/40',
    text: 'text-cyan-300',
    border: 'border-cyan-600/40',
    dot: 'bg-cyan-400',
  },
  ACTIVE: {
    label: 'Operational Active',
    bg: 'bg-emerald-900/40',
    text: 'text-emerald-300',
    border: 'border-emerald-600/40',
    dot: 'bg-emerald-400 animate-ping',
  },
  PAUSED: {
    label: 'Emergency Paused',
    bg: 'bg-amber-950/60',
    text: 'text-amber-200',
    border: 'border-amber-500/60',
    dot: 'bg-amber-500',
  },
  COMPLETED: {
    label: 'Completed',
    bg: 'bg-teal-900/40',
    text: 'text-teal-300',
    border: 'border-teal-600/40',
    dot: 'bg-teal-400',
  },
  ABORTED: {
    label: 'Aborted (Terminal)',
    bg: 'bg-rose-950/60',
    text: 'text-rose-300',
    border: 'border-rose-600/50',
    dot: 'bg-rose-500',
  },
  FAILED: {
    label: 'Failed (Terminal)',
    bg: 'bg-red-950/60',
    text: 'text-red-300',
    border: 'border-red-600/50',
    dot: 'bg-red-500',
  },
};

export default function PilotStatusBadge({ status, size = 'md' }: PilotStatusBadgeProps) {
  const normalized = (status || 'DRAFT').toUpperCase();
  const conf = STATUS_CONFIG[normalized] || {
    label: normalized,
    bg: 'bg-slate-800/60',
    text: 'text-slate-300',
    border: 'border-slate-700',
    dot: 'bg-slate-400',
  };

  const sizeClasses = {
    sm: 'text-[10px] px-2 py-0.5 gap-1',
    md: 'text-xs px-2.5 py-1 gap-1.5',
    lg: 'text-sm px-3.5 py-1.5 gap-2 font-medium',
  }[size];

  return (
    <span
      className={`inline-flex items-center rounded-full border ${conf.bg} ${conf.text} ${conf.border} ${sizeClasses} tracking-wide font-mono font-semibold shadow-sm`}
    >
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${conf.dot}`} />
      <span>{conf.label}</span>
    </span>
  );
}
