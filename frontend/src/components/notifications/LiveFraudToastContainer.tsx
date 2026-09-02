import { useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, ArrowRight, X, Zap } from 'lucide-react';
import { useLiveAlertStore } from '../../stores/useLiveAlertStore';
import type { LiveAlertToast } from '../../stores/useLiveAlertStore';
import { BANK_NAMES } from '../../api/types';

export default function LiveFraudToastContainer() {
  const activeAlertToasts = useLiveAlertStore((s) => s.activeAlertToasts);
  const dismissToast = useLiveAlertStore((s) => s.dismissToast);

  return (
    <div
      aria-live="polite"
      className="fixed bottom-5 right-5 z-50 flex flex-col gap-2.5 max-w-[360px] sm:max-w-md w-full pointer-events-none px-3 sm:px-0"
    >
      <AnimatePresence>
        {activeAlertToasts.map((toast) => (
          <LiveToastItem key={toast.id} toast={toast} onDismiss={() => dismissToast(toast.id)} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function LiveToastItem({ toast, onDismiss }: { toast: LiveAlertToast; onDismiss: () => void }) {
  const navigate = useNavigate();

  // Auto-dismiss toast after 7 seconds
  useEffect(() => {
    const timer = setTimeout(() => {
      onDismiss();
    }, 7000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  const bankName = BANK_NAMES[toast.bank_id] || toast.bank_id;
  const isCritical = toast.severity === 'critical' || toast.risk_score >= 850;

  return (
    <motion.div
      initial={{ opacity: 0, y: 30, scale: 0.92 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.85, transition: { duration: 0.2 } }}
      transition={{ type: 'spring', stiffness: 400, damping: 25 }}
      className={`pointer-events-auto rounded-2xl p-3.5 sm:p-4 shadow-2xl backdrop-blur-xl border transition-all ${
        isCritical
          ? 'bg-gradient-to-br from-red-950/90 via-slate-900/95 to-slate-950/95 border-red-500/40 shadow-[0_0_25px_rgba(239,68,68,0.25)]'
          : 'bg-gradient-to-br from-amber-950/90 via-slate-900/95 to-slate-950/95 border-amber-500/40 shadow-[0_0_25px_rgba(245,158,11,0.25)]'
      }`}
    >
      {/* Header Row */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 border ${
              isCritical
                ? 'bg-red-500/20 border-red-500/50 text-red-400'
                : 'bg-amber-500/20 border-amber-500/50 text-amber-400'
            }`}
          >
            <ShieldAlert className="w-4 h-4" />
          </div>
          <div className="min-w-0">
            <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-slate-400 block truncate">
              {bankName}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold uppercase border ${
              isCritical
                ? 'bg-red-500/20 text-red-300 border-red-500/40'
                : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
            }`}
          >
            {toast.severity} {toast.risk_score}
          </span>
          <button
            onClick={onDismiss}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-100 hover:bg-white/10 transition-colors"
            aria-label="Dismiss notification"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Typology & Description */}
      <div className="mb-3">
        <h5 className="text-xs sm:text-sm font-bold text-slate-100 tracking-tight leading-snug">
          {toast.description || toast.typology}
        </h5>
        <div className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 font-mono">
          <span>{toast.currency} {toast.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
          <span>•</span>
          <span className="truncate">{toast.transaction_id}</span>
        </div>
      </div>

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-2 border-t border-white/10 text-[11px]">
        <span className="flex items-center gap-1 text-[10px] text-slate-400 font-mono">
          <Zap className="w-3 h-3 text-cyan-400 animate-pulse" /> Live Scoring Stream
        </span>
        <button
          onClick={() => {
            onDismiss();
            navigate('/alerts');
          }}
          className="flex items-center gap-1 font-semibold text-cyan-400 hover:text-cyan-300 transition-colors"
        >
          <span>Investigate</span>
          <ArrowRight className="w-3 h-3" />
        </button>
      </div>
    </motion.div>
  );
}
