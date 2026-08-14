import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ShieldCheck,
  Cpu,
  Layers,
  Lock,
  ArrowRight,
  Sparkles,
  Binary,
  Radio,
  CheckCircle2,
} from 'lucide-react';

interface PlatformLaunchModalProps {
  isOpen: boolean;
  onClose?: () => void;
  onComplete: () => void;
}

interface StageInfo {
  id: number;
  label: string;
  subtext: string;
  tag: string;
  icon: typeof Lock;
  color: string;
  glow: string;
}

const STAGES: StageInfo[] = [
  {
    id: 1,
    label: 'mTLS 1.3 & Vault PKI Handshake',
    subtext: 'Authenticating JPM, HSBC & DBK on-premises node certificates',
    tag: 'FIPS 140-3 · 1.2ms',
    icon: Lock,
    color: '#6366f1',
    glow: 'rgba(99, 102, 241, 0.4)',
  },
  {
    id: 2,
    label: 'Post-Quantum Kyber-768 Exchange',
    subtext: 'Negotiating NIST FIPS 203 KEM & Dilithium-3 quantum-safe keys',
    tag: 'PQC Lattice · Bound',
    icon: Binary,
    color: '#8b5cf6',
    glow: 'rgba(139, 92, 246, 0.4)',
  },
  {
    id: 3,
    label: 'PyTorch GAT & Rényi DP Noise Calibration',
    subtext: 'Computing 8-head structural embeddings with Opacus (ε=0.50, δ=1e-5)',
    tag: '12.8k Graphs · Active',
    icon: Layers,
    color: '#06b6d4',
    glow: 'rgba(6, 182, 212, 0.4)',
  },
  {
    id: 4,
    label: 'Intel SGX Enclave & Paillier HE Sum',
    subtext: 'Hardware TEE blind homomorphic summation & zk-SNARK attestation',
    tag: 'Groth16 BN254 · <5ms',
    icon: Cpu,
    color: '#10b981',
    glow: 'rgba(16, 185, 129, 0.4)',
  },
  {
    id: 5,
    label: 'Byzantine Krum Defense & Global Weights',
    subtext: 'Adversarial poisoning filtered. Dispatching consortium model',
    tag: '94.2% Acc · Consensus',
    icon: ShieldCheck,
    color: '#38bdf8',
    glow: 'rgba(56, 189, 248, 0.4)',
  },
];

const BANK_NODES = [
  { id: 'JPM', name: 'JPMorgan', role: 'Bank Alpha', x: 20, y: 75, color: '#6366f1' },
  { id: 'HSB', name: 'HSBC', role: 'Bank Beta', x: 80, y: 75, color: '#a855f7' },
  { id: 'DBK', name: 'Deutsche', role: 'Bank Gamma', x: 50, y: 18, color: '#06b6d4' },
];

export default function PlatformLaunchModal({ isOpen, onComplete }: PlatformLaunchModalProps) {
  const [currentStageIdx, setCurrentStageIdx] = useState(0);
  const [progress, setProgress] = useState(8);

  useEffect(() => {
    if (!isOpen) {
      setCurrentStageIdx(0);
      setProgress(8);
      return;
    }

    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(progressInterval);
          return 100;
        }
        return prev + 2.0;
      });
    }, 45);

    const s1 = setTimeout(() => setCurrentStageIdx(1), 500);
    const s2 = setTimeout(() => setCurrentStageIdx(2), 1000);
    const s3 = setTimeout(() => setCurrentStageIdx(3), 1550);
    const s4 = setTimeout(() => setCurrentStageIdx(4), 2100);

    const completion = setTimeout(() => {
      setTimeout(() => {
        onComplete();
      }, 300);
    }, 2750);

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

  const currentStage = STAGES[Math.min(currentStageIdx, STAGES.length - 1)]!;
  const StageIcon = currentStage.icon;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-4 bg-[#020208]/92 backdrop-blur-2xl overflow-hidden select-none"
      >
        {/* Ambient Holographic Radial Background */}
        <div className="absolute inset-0 bg-[radial-gradient(#6366f1_1px,transparent_1px)] [background-size:24px_24px] opacity-20 pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[340px] sm:w-[500px] h-[340px] sm:h-[500px] bg-indigo-600/15 rounded-full blur-[120px] pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[220px] sm:w-[320px] h-[220px] sm:h-[320px] bg-cyan-500/15 rounded-full blur-[90px] pointer-events-none" />

        {/* Modal Window Container - Zero Scroll, 100% Viewport-Friendly */}
        <motion.div
          role="dialog"
          aria-modal="true"
          initial={{ scale: 0.94, opacity: 0, y: 10 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.94, opacity: 0, y: 10 }}
          transition={{ type: 'spring', damping: 26, stiffness: 300 }}
          className="relative w-full max-w-[480px] bg-[#050614]/95 border border-indigo-500/30 rounded-2xl sm:rounded-3xl shadow-[0_0_80px_rgba(79,70,229,0.3)] overflow-hidden flex flex-col"
        >
          {/* Top Neon Laser Glow Bar */}
          <div className="h-1 w-full bg-gradient-to-r from-indigo-500 via-cyan-400 to-purple-500 shadow-[0_0_15px_rgba(6,182,212,0.8)]" />

          {/* Header: Minimal, High-Tech, Crisp */}
          <div className="px-4 py-3 sm:px-5 sm:py-3.5 border-b border-white/8 bg-[#03030d]/80 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="relative flex items-center justify-center w-8 h-8 rounded-xl bg-indigo-500/15 border border-indigo-400/40 shrink-0 shadow-[0_0_12px_rgba(99,102,241,0.3)]">
                <ShieldCheck className="w-4 h-4 text-cyan-400" />
                <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500" />
                </span>
              </div>
              <div className="min-w-0">
                <h3 className="text-xs sm:text-sm font-bold text-slate-100 tracking-tight flex items-center gap-1.5 truncate">
                  Consortium Handshake
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.2 rounded text-[9px] font-mono text-emerald-400 bg-emerald-500/10 border border-emerald-500/25">
                    <Radio className="w-2 h-2 animate-pulse" /> LIVE
                  </span>
                </h3>
                <p className="text-[10px] font-mono text-slate-400 truncate">
                  CF-Intelligence · Privacy-Preserving Plane
                </p>
              </div>
            </div>

            <button
              onClick={onComplete}
              className="px-2.5 py-1 rounded-lg text-[10px] sm:text-[11px] font-mono text-slate-400 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all cursor-pointer flex items-center gap-1 shrink-0"
              title="Direct jump to dashboard"
            >
              <span>Skip</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>

          {/* Body Content */}
          <div className="p-4 sm:p-5 space-y-3.5 sm:space-y-4">
            {/* ── 1. CINEMATIC SVG TOPOLOGY RADAR (Zero-scroll, responsive) ── */}
            <div className="relative h-32 sm:h-36 rounded-2xl bg-[#03030c] border border-white/8 p-2 flex items-center justify-center overflow-hidden">
              {/* Radar Grid Circles */}
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div className="w-28 h-28 rounded-full border border-indigo-500/15 animate-ping opacity-25" />
                <div className="w-24 h-24 rounded-full border border-indigo-500/20" />
                <div className="w-16 h-16 rounded-full border border-cyan-500/25" />
              </div>

              {/* Dynamic Connection Beams */}
              <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 100 100" preserveAspectRatio="none">
                {BANK_NODES.map((b) => (
                  <line
                    key={b.id}
                    x1={b.x}
                    y1={b.y}
                    x2={50}
                    y2={50}
                    stroke="rgba(99, 102, 241, 0.4)"
                    strokeWidth="0.8"
                    strokeDasharray="2 2"
                    className="animate-pulse"
                  />
                ))}
              </svg>

              {/* Central Intel SGX Enclave Hub */}
              <div className="relative z-10 flex flex-col items-center">
                <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-600 via-purple-600 to-cyan-500 p-0.5 shadow-[0_0_25px_rgba(6,182,212,0.6)]">
                  <div className="w-full h-full rounded-[10px] bg-[#050616] flex flex-col items-center justify-center">
                    <Cpu className="w-4 h-4 text-cyan-300" />
                    <span className="text-[7.5px] font-mono font-bold text-cyan-300">SGX TEE</span>
                  </div>
                </div>
                <span className="text-[8.5px] font-mono text-cyan-400 font-semibold mt-1">FL Coordinator</span>
              </div>

              {/* 3 Bank Nodes Around Enclave */}
              {BANK_NODES.map((node) => (
                <div
                  key={node.id}
                  className="absolute flex flex-col items-center"
                  style={{ left: `${node.x}%`, top: `${node.y}%`, transform: 'translate(-50%, -50%)' }}
                >
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center text-[10px] font-mono font-bold text-white shadow-md border"
                    style={{
                      backgroundColor: `${node.color}20`,
                      borderColor: `${node.color}70`,
                      boxShadow: `0 0 12px ${node.color}40`,
                    }}
                  >
                    {node.id}
                  </div>
                  <span className="text-[7.5px] font-mono text-slate-400 mt-0.5">{node.role}</span>
                </div>
              ))}
            </div>

            {/* ── 2. DYNAMIC FOCUSED ACTIVE STAGE CARD (Framer Motion) ── */}
            <div className="relative min-h-[72px] sm:min-h-[76px] rounded-xl sm:rounded-2xl bg-indigo-500/10 border border-indigo-500/30 p-3 sm:p-3.5 flex items-center justify-between gap-3 shadow-[0_0_20px_rgba(99,102,241,0.15)] overflow-hidden">
              <div className="flex items-center gap-3 min-w-0">
                <div
                  className="w-9 h-9 sm:w-10 sm:h-10 rounded-xl flex items-center justify-center shrink-0 border"
                  style={{
                    backgroundColor: `${currentStage.color}25`,
                    borderColor: `${currentStage.color}60`,
                    boxShadow: `0 0 15px ${currentStage.glow}`,
                  }}
                >
                  <StageIcon className="w-4 h-4 sm:w-5 sm:h-5 text-white" />
                </div>

                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[9px] font-mono font-bold px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 shrink-0">
                      STAGE 0{currentStage.id}/05
                    </span>
                  </div>
                  <h4 className="text-xs sm:text-[13px] font-bold text-slate-100 tracking-tight truncate mt-0.5">
                    {currentStage.label}
                  </h4>
                  <p className="text-[10px] text-slate-400 truncate mt-0.2">
                    {currentStage.subtext}
                  </p>
                </div>
              </div>

              <span className="text-[9.5px] font-mono font-semibold px-2 py-1 rounded-md bg-white/5 border border-white/10 text-cyan-300 shrink-0">
                {currentStage.tag}
              </span>
            </div>

            {/* ── 3. FIVE-STAGE STEP TRACKER (Visual Breadcrumb) ── */}
            <div className="grid grid-cols-5 gap-1.5">
              {STAGES.map((s, idx) => {
                const isDone = idx < currentStageIdx;
                const isCurrent = idx === currentStageIdx;

                return (
                  <div
                    key={s.id}
                    className={`h-1.5 rounded-full transition-all duration-300 ${
                      isDone
                        ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]'
                        : isCurrent
                        ? 'bg-indigo-400 animate-pulse shadow-[0_0_8px_rgba(129,140,248,0.8)]'
                        : 'bg-white/10'
                    }`}
                  />
                );
              })}
            </div>

            {/* ── 4. REAL-TIME TELEMETRY MICRO-PILLS ── */}
            <div className="grid grid-cols-3 gap-2 text-center font-mono text-[9px] sm:text-[10px]">
              <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 flex items-center justify-center gap-1 text-slate-300">
                <CheckCircle2 className="w-2.5 h-2.5 text-emerald-400 shrink-0" />
                <span className="truncate">3 Nodes Linked</span>
              </div>
              <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 flex items-center justify-center gap-1 text-slate-300">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0" />
                <span className="truncate">ε=0.50 DP Bound</span>
              </div>
              <div className="p-1.5 rounded-lg bg-white/[0.02] border border-white/5 flex items-center justify-center gap-1 text-slate-300">
                <span className="w-1.5 h-1.5 rounded-full bg-purple-400 shrink-0" />
                <span className="truncate">SGX Attested</span>
              </div>
            </div>
          </div>

          {/* Footer: Progress Bar */}
          <div className="px-4 py-3 sm:px-5 sm:py-3.5 bg-[#03030d] border-t border-white/8 space-y-1.5">
            <div className="flex items-center justify-between text-[10.5px] sm:text-xs font-mono">
              <span className="text-slate-300 flex items-center gap-1.5 truncate">
                <Sparkles className="w-3 h-3 text-indigo-400 shrink-0" />
                <span className="truncate">Synchronizing Consortium Data Plane...</span>
              </span>
              <span className="text-cyan-400 font-bold ml-2 shrink-0">{Math.min(100, Math.round(progress))}%</span>
            </div>
            <div className="relative w-full h-1.5 sm:h-2 bg-slate-900 rounded-full overflow-hidden border border-white/10 p-0.5">
              <motion.div
                className="h-full bg-gradient-to-r from-indigo-500 via-cyan-400 to-purple-500 rounded-full shadow-[0_0_12px_rgba(6,182,212,0.8)]"
                style={{ width: `${Math.min(100, progress)}%` }}
                transition={{ ease: 'linear' }}
              />
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
