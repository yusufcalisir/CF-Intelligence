import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

interface PlatformLaunchModalProps {
  isOpen: boolean;
  onClose?: () => void;
  onComplete: () => void;
}

interface InitializationStage {
  id: number;
  label: string;
  subtext: string;
  badge: string;
  metric: string;
}

const STAGES: InitializationStage[] = [
  {
    id: 1,
    label: 'gRPC mTLS Consortium Handshake',
    subtext: 'Connecting JPM (#01), HSBC (#02) & DBK (#03) Bank Nodes...',
    badge: 'STAGE 01',
    metric: 'mTLS 1.3 · 1.2ms',
  },
  {
    id: 2,
    label: 'Intel SGX Hardware Enclave Vault',
    subtext: 'Verifying Remote Attestation Quote & Paillier HE Keys...',
    badge: 'STAGE 02',
    metric: 'SGX v2 · Attested',
  },
  {
    id: 3,
    label: 'ISO 20022 Graph Telemetry Ingestion',
    subtext: 'Parsing pacs.008 / camt.053 message streams & GNN Tensors...',
    badge: 'STAGE 03',
    metric: '12,840 Nodes · GATConv',
  },
  {
    id: 4,
    label: 'Mounting Live Operations Command Center',
    subtext: 'Calibrating (ε=0.50, δ=1e-5) Differential Privacy bounds...',
    badge: 'STAGE 04',
    metric: 'RDP Accountant · Ready',
  },
];

export default function PlatformLaunchModal({ isOpen, onComplete }: PlatformLaunchModalProps) {
  const [currentStageIdx, setCurrentStageIdx] = useState(0);
  const [progress, setProgress] = useState(5);
  const [logMessages, setLogMessages] = useState<string[]>([]);

  useEffect(() => {
    if (!isOpen) {
      setCurrentStageIdx(0);
      setProgress(5);
      setLogMessages([]);
      return;
    }

    // Progress bar animation
    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(progressInterval);
          return 100;
        }
        return prev + 2;
      });
    }, 45);

    // Stage progression & logs
    const stage1Timeout = setTimeout(() => {
      setCurrentStageIdx(1);
      setLogMessages((prev) => [
        ...prev,
        '[gRPC] Handshake verified: JPM, HSBC, DBK connected via mTLS 1.3',
      ]);
    }, 700);

    const stage2Timeout = setTimeout(() => {
      setCurrentStageIdx(2);
      setLogMessages((prev) => [
        ...prev,
        '[Intel SGX] IAS Attestation Quote status: SUCCESS (Paillier HE Active)',
      ]);
    }, 1400);

    const stage3Timeout = setTimeout(() => {
      setCurrentStageIdx(3);
      setLogMessages((prev) => [
        ...prev,
        '[GNN Engine] 12,840 transaction graph node embeddings synchronized',
      ]);
    }, 2100);

    const completionTimeout = setTimeout(() => {
      setLogMessages((prev) => [
        ...prev,
        '[Coordinator] Consortium Command Center mounted. Redirecting...',
      ]);
      setTimeout(() => {
        onComplete();
      }, 400);
    }, 2700);

    return () => {
      clearInterval(progressInterval);
      clearTimeout(stage1Timeout);
      clearTimeout(stage2Timeout);
      clearTimeout(stage3Timeout);
      clearTimeout(completionTimeout);
    };
  }, [isOpen, onComplete]);

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-[#03030d]/85 backdrop-blur-2xl"
      >
        {/* Glowing Background Orbs */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-indigo-600/15 rounded-full blur-[140px] pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] bg-purple-600/15 rounded-full blur-[100px] pointer-events-none" />

        <motion.div
          initial={{ scale: 0.94, opacity: 0, y: 15 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.94, opacity: 0, y: 15 }}
          transition={{ type: 'spring', damping: 25, stiffness: 260 }}
          className="relative w-full max-w-lg bg-[#070718]/95 border border-indigo-500/25 rounded-3xl p-6 sm:p-8 shadow-[0_0_80px_rgba(99,102,241,0.25)] overflow-hidden"
        >
          {/* Top Decorative Line */}
          <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400" />

          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="relative flex items-center justify-center w-11 h-11 rounded-2xl bg-indigo-500/10 border border-indigo-500/20">
                <img src="/logo.svg" alt="CF Logo" className="w-6 h-6 object-contain" />
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-cyan-500" />
                </span>
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-100 tracking-tight">
                  Initializing Consortium Demo
                </h3>
                <p className="text-xs font-mono text-indigo-400">
                  CF-Intelligence · Federated Operations
                </p>
              </div>
            </div>
            <button
              onClick={onComplete}
              className="px-3 py-1.5 rounded-xl text-[11px] font-mono text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-colors cursor-pointer"
            >
              Skip ➔
            </button>
          </div>

          {/* Progress Bar Container */}
          <div className="space-y-2 mb-6">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-400">System Handshake & Telemetry Sync</span>
              <span className="text-cyan-400 font-bold">{Math.min(100, progress)}%</span>
            </div>
            <div className="relative w-full h-2 bg-slate-900/90 rounded-full overflow-hidden border border-white/5">
              <motion.div
                className="h-full bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400 rounded-full shadow-[0_0_15px_rgba(6,182,212,0.6)]"
                style={{ width: `${Math.min(100, progress)}%` }}
                transition={{ ease: 'linear' }}
              />
            </div>
          </div>

          {/* Stage Cards */}
          <div className="space-y-2.5 mb-6">
            {STAGES.map((stage, idx) => {
              const isCompleted = idx < currentStageIdx;
              const isActive = idx === currentStageIdx;

              return (
                <div
                  key={stage.id}
                  className={`flex items-start justify-between p-3 rounded-2xl border transition-all duration-300 ${
                    isActive
                      ? 'bg-indigo-500/10 border-indigo-500/40 shadow-[0_0_20px_rgba(99,102,241,0.15)]'
                      : isCompleted
                      ? 'bg-white/[0.02] border-emerald-500/20 text-slate-300'
                      : 'bg-white/[0.01] border-white/5 opacity-40'
                  }`}
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="mt-0.5 shrink-0">
                      {isCompleted ? (
                        <div className="w-5 h-5 rounded-full bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 text-[10px] font-bold">
                          ✓
                        </div>
                      ) : isActive ? (
                        <div className="relative flex items-center justify-center w-5 h-5">
                          <span className="animate-spin w-4 h-4 rounded-full border-2 border-indigo-400 border-t-transparent" />
                        </div>
                      ) : (
                        <div className="w-5 h-5 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 text-[10px] font-mono">
                          0{stage.id}
                        </div>
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-slate-100 truncate">
                          {stage.label}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-400 truncate mt-0.5">
                        {stage.subtext}
                      </p>
                    </div>
                  </div>

                  <span
                    className={`text-[9.5px] font-mono shrink-0 px-2 py-0.5 rounded-md border ${
                      isCompleted
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : isActive
                        ? 'bg-indigo-500/20 border-indigo-500/40 text-indigo-300 animate-pulse'
                        : 'bg-white/5 border-white/10 text-slate-500'
                    }`}
                  >
                    {stage.metric}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Console Log Stream Box */}
          <div className="rounded-2xl bg-[#03030c] border border-white/8 p-3 space-y-1 font-mono text-[10.5px] text-slate-400 overflow-hidden">
            <div className="flex items-center justify-between text-[9px] text-slate-500 border-b border-white/5 pb-1.5 mb-1">
              <span>TELEMETRY STREAM LOG</span>
              <span className="text-cyan-400 animate-pulse">LIVE</span>
            </div>
            <div className="h-14 overflow-y-auto space-y-1">
              {logMessages.length === 0 ? (
                <div className="text-slate-600 italic">Initializing telemetry stream...</div>
              ) : (
                logMessages.map((msg, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-slate-300">
                    <span className="text-indigo-400 shrink-0">›</span>
                    <span className="leading-tight">{msg}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
