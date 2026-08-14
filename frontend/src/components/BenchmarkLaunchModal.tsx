import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  BarChart3,
  Layers,
  Database,
  ShieldCheck,
  TrendingUp,
  CheckCircle2,
  Cpu,
  ArrowRight,
} from 'lucide-react';
import { useModalA11y } from '../hooks/useModalA11y';

interface BenchmarkLaunchModalProps {
  isOpen: boolean;
  onClose?: () => void;
  onComplete: () => void;
}

interface BenchmarkStageInfo {
  id: number;
  label: string;
  subtext: string;
  tag: string;
  icon: typeof BarChart3;
  color: string;
  glow: string;
}

const BENCHMARK_STAGES: BenchmarkStageInfo[] = [
  {
    id: 1,
    label: 'Kaggle & PaySim Baseline Ingestion',
    subtext: 'Loading 284,807 real-world credit card transactions & 492 fraud cases',
    tag: '284.8k Records · Loaded',
    icon: Database,
    color: '#6366f1',
    glow: 'rgba(99, 102, 241, 0.4)',
  },
  {
    id: 2,
    label: 'Non-IID Dirichlet α=0.5 Cross-Bank Partition',
    subtext: 'Synthesizing heterogeneous data skew across JPM, HSBC & DBK nodes',
    tag: 'Dirichlet α=0.5 · Bounded',
    icon: Layers,
    color: '#8b5cf6',
    glow: 'rgba(139, 92, 246, 0.4)',
  },
  {
    id: 3,
    label: 'Collaborative GNN vs Siloed Model Matrix',
    subtext: 'Benchmarking PR-AUC (0.8420 vs 0.6940) & ROC-AUC (0.9120 vs 0.8350)',
    tag: '+21.3% PR-AUC Gain',
    icon: TrendingUp,
    color: '#06b6d4',
    glow: 'rgba(6, 182, 212, 0.4)',
  },
  {
    id: 4,
    label: 'Daily Cost & False Positive Reduction Audit',
    subtext: 'Calculating net daily fraud prevention ($15,630 vs $29,880 loss baseline)',
    tag: '+$14.2k/day ROI',
    icon: Cpu,
    color: '#10b981',
    glow: 'rgba(16, 185, 129, 0.4)',
  },
  {
    id: 5,
    label: 'Launching Design Partner Benchmark Sandbox',
    subtext: 'Attestation verified. Opening live interactive empirical metrics canvas',
    tag: 'Sandbox Ready · v2.4',
    icon: ShieldCheck,
    color: '#38bdf8',
    glow: 'rgba(56, 189, 248, 0.4)',
  },
];

export default function BenchmarkLaunchModal({ isOpen, onClose, onComplete }: BenchmarkLaunchModalProps) {
  const [currentStageIdx, setCurrentStageIdx] = useState(0);
  const [progress, setProgress] = useState(10);

  const { containerRef } = useModalA11y<HTMLDivElement>({
    isOpen,
    onClose: onClose || onComplete,
    closeOnEscape: true,
    trapFocus: true,
    restoreFocus: true,
  });

  useEffect(() => {
    if (!isOpen) {
      setCurrentStageIdx(0);
      setProgress(10);
      return;
    }

    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(progressInterval);
          return 100;
        }
        return prev + 2.4;
      });
    }, 40);

    const s1 = setTimeout(() => setCurrentStageIdx(1), 400);
    const s2 = setTimeout(() => setCurrentStageIdx(2), 850);
    const s3 = setTimeout(() => setCurrentStageIdx(3), 1350);
    const s4 = setTimeout(() => setCurrentStageIdx(4), 1800);

    const completion = setTimeout(() => {
      setTimeout(() => {
        onComplete();
      }, 250);
    }, 2350);

    return () => {
      clearInterval(progressInterval);
      clearTimeout(s1);
      clearTimeout(s2);
      clearTimeout(s3);
      clearTimeout(s4);
      clearTimeout(completion);
    };
  }, [isOpen, onComplete]);

  if (!isOpen) return null;

  const currentStage: BenchmarkStageInfo = BENCHMARK_STAGES[currentStageIdx] ?? (BENCHMARK_STAGES[0] as BenchmarkStageInfo);
  const CurrentIcon = currentStage.icon;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-[200] flex items-center justify-center p-3 sm:p-6 overflow-x-hidden overflow-y-auto">
        {/* Deep Backdrop with Ambient Glow */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 bg-[#03030c]/90 backdrop-blur-2xl"
        />

        {/* Modal Window Container */}
        <motion.div
          ref={containerRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="benchmark-modal-title"
          aria-describedby="benchmark-modal-desc"
          tabIndex={-1}
          initial={{ opacity: 0, scale: 0.94, y: 15 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.94, y: 15 }}
          transition={{ type: 'spring', damping: 26, stiffness: 260 }}
          className="relative w-full max-w-lg rounded-3xl bg-gradient-to-b from-[#0a0c28] via-[#07081a] to-[#04050e] border border-indigo-500/30 p-5 sm:p-7 shadow-[0_0_80px_rgba(99,102,241,0.25)] space-y-5 z-10 overflow-hidden focus:outline-none"
        >
          {/* Top Edge Glow */}
          <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400" />

          {/* Header */}
          <div className="flex items-center justify-between border-b border-white/10 pb-4 gap-2">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-2xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-300 shadow-inner shrink-0">
                <BarChart3 className="w-5 h-5 animate-pulse" />
              </div>
              <div className="min-w-0">
                <h3 id="benchmark-modal-title" className="text-base sm:text-lg font-bold text-white tracking-tight truncate">
                  Initializing Benchmark Sandbox
                </h3>
                <p id="benchmark-modal-desc" className="text-xs text-indigo-300/80 font-mono truncate">
                  Empirical Proof & Real Data Baseline Engine
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="text-[10px] font-mono font-bold px-2.5 py-1 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                {Math.min(100, Math.round(progress))}%
              </span>
              <button
                onClick={onComplete || onClose}
                aria-label="Skip initialization and open benchmark hub"
                className="px-2.5 py-1 rounded-lg text-[10px] sm:text-[11px] font-mono text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all cursor-pointer flex items-center gap-1 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                title="Direct jump to benchmark sandbox"
              >
                <span>Skip</span>
                <ArrowRight className="w-3 h-3" />
              </button>
            </div>
          </div>

          {/* Active Stage Animated Card */}
          <div className="p-4 rounded-2xl bg-[#02030a]/80 border border-white/10 space-y-3 relative overflow-hidden">
            <div
              className="absolute -right-8 -top-8 w-24 h-24 rounded-full blur-2xl pointer-events-none opacity-40"
              style={{ backgroundColor: currentStage.color }}
            />

            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2.5 min-w-0">
                <div
                  className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 border"
                  style={{
                    backgroundColor: `${currentStage.color}20`,
                    borderColor: `${currentStage.color}50`,
                    color: currentStage.color,
                  }}
                >
                  <CurrentIcon className="w-4 h-4" />
                </div>
                <div className="min-w-0">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 block truncate">
                    Stage 0{currentStage.id} of 05
                  </span>
                  <h4 className="text-xs sm:text-sm font-bold text-slate-100 truncate">
                    {currentStage.label}
                  </h4>
                </div>
              </div>

              <span
                className="text-[9.5px] font-mono font-bold px-2.5 py-0.5 rounded-full border shrink-0"
                style={{
                  backgroundColor: `${currentStage.color}15`,
                  borderColor: `${currentStage.color}40`,
                  color: currentStage.color,
                }}
              >
                {currentStage.tag}
              </span>
            </div>

            <p className="text-xs text-slate-300 leading-relaxed font-sans pl-1">
              {currentStage.subtext}
            </p>
          </div>

          {/* Step Progression Indicators */}
          <div className="space-y-1.5">
            {BENCHMARK_STAGES.map((s, idx) => {
              const isCompleted = idx < currentStageIdx;
              const isCurrent = idx === currentStageIdx;

              return (
                <div
                  key={s.id}
                  className={`flex items-center justify-between px-3 py-1.5 rounded-xl text-xs font-mono transition-all ${
                    isCurrent
                      ? 'bg-indigo-600/15 border border-indigo-500/30 text-indigo-200'
                      : isCompleted
                      ? 'text-slate-400 opacity-80'
                      : 'text-slate-600 opacity-40'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    {isCompleted ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                    ) : isCurrent ? (
                      <span className="w-2 h-2 rounded-full bg-indigo-400 animate-ping shrink-0" />
                    ) : (
                      <span className="w-2 h-2 rounded-full bg-slate-700 shrink-0" />
                    )}
                    <span className="truncate">{s.label}</span>
                  </div>
                  <span className="text-[10px] opacity-75 shrink-0 ml-2">
                    {isCompleted ? '✓ Done' : isCurrent ? 'Active...' : 'Queued'}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Progress Bar */}
          <div className="space-y-1.5 pt-1">
            <div className="w-full bg-slate-900 rounded-full h-1.5 overflow-hidden p-0.5 border border-white/5">
              <div
                className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400 transition-all duration-150"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] font-mono text-slate-500">
              <span>Authenticating Secure Sandbox...</span>
              <span>Redirecting to Benchmark Hub</span>
            </div>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
