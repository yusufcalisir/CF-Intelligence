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
    label: 'mTLS 1.3 & Vault PKI HSM Handshake',
    subtext: 'Establishing Zero-Trust links with JPM, HSBC & DBK nodes...',
    badge: 'STAGE 01',
    metric: 'FIPS 140-2 · 1.2ms',
  },
  {
    id: 2,
    label: 'Post-Quantum Lattice Key Exchange',
    subtext: 'Negotiating NIST FIPS 203 (Kyber-768) & FIPS 204 (Dilithium-3) keys...',
    badge: 'STAGE 02',
    metric: 'PQC Lattice · Bound',
  },
  {
    id: 3,
    label: 'Intel SGX Enclave & zk-SNARK Attestation',
    subtext: 'Verifying Groth16 BN254 model proofs & TEE Paillier HE rings...',
    badge: 'STAGE 03',
    metric: 'zk-SNARK · O(1) <5ms',
  },
  {
    id: 4,
    label: 'ISO 20022 Graph & Agentic AML Stream',
    subtext: 'Parsing pacs.008 streams, GAT embeddings & FinCEN RAG engine...',
    badge: 'STAGE 04',
    metric: '12,840 Nodes · GAT',
  },
  {
    id: 5,
    label: 'Adaptive Rényi DP Budget Auto-Scaler',
    subtext: 'Calibrating dynamic noise multiplier (σ_t) against loss velocity...',
    badge: 'STAGE 05',
    metric: 'RDP Dual · Active',
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
        return prev + 2.0;
      });
    }, 35);

    // Stage progression & telemetry logs
    const stage1Timeout = setTimeout(() => {
      setCurrentStageIdx(1);
      setLogMessages((prev) => [
        ...prev,
        '[mTLS / Vault PKI] Bank Nodes Connected & HSM Attested: JPM (#01), HSBC (#02), DBK (#03)',
      ]);
    }, 550);

    const stage2Timeout = setTimeout(() => {
      setCurrentStageIdx(2);
      setLogMessages((prev) => [
        ...prev,
        '[PQC Lattice] CRYSTALS-Kyber-768 KEM & Dilithium-3 Signatures Verified',
      ]);
    }, 1100);

    const stage3Timeout = setTimeout(() => {
      setCurrentStageIdx(3);
      setLogMessages((prev) => [
        ...prev,
        '[zk-SNARK & SGX] Groth16 BN254 Weight Attestation Verified (Proof SLA <5ms)',
      ]);
    }, 1650);

    const stage4Timeout = setTimeout(() => {
      setCurrentStageIdx(4);
      setLogMessages((prev) => [
        ...prev,
        '[FedGNN & AML Copilot] 12,840 transaction graph tensors & FinCEN RAG agent synchronized',
      ]);
    }, 2200);

    const completionTimeout = setTimeout(() => {
      setLogMessages((prev) => [
        ...prev,
        '[Coordinator] Consortium Command Center Active. Mounting Dashboard...',
      ]);
      setTimeout(() => {
        onComplete();
      }, 350);
    }, 2850);

    return () => {
      clearInterval(progressInterval);
      clearTimeout(stage1Timeout);
      clearTimeout(stage2Timeout);
      clearTimeout(stage3Timeout);
      clearTimeout(stage4Timeout);
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
        className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-[#02030a]/90 backdrop-blur-2xl"
      >
        {/* Ambient Security Grid Background */}
        <div className="absolute inset-0 bg-[radial-gradient(#6366f1_1px,transparent_1px)] [background-size:24px_24px] opacity-15 pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[550px] h-[550px] bg-indigo-600/15 rounded-full blur-[140px] pointer-events-none" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[350px] h-[350px] bg-cyan-600/15 rounded-full blur-[100px] pointer-events-none" />

        <motion.div
          initial={{ scale: 0.94, opacity: 0, y: 15 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          exit={{ scale: 0.94, opacity: 0, y: 15 }}
          transition={{ type: 'spring', damping: 25, stiffness: 260 }}
          className="relative w-full max-w-xl bg-[#060719]/95 border border-indigo-500/30 rounded-3xl p-6 sm:p-8 shadow-[0_0_90px_rgba(99,102,241,0.3)] overflow-hidden"
        >
          {/* Top Decorative Cyber Gradient Bar */}
          <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-indigo-500 via-cyan-400 to-purple-500" />

          {/* Header */}
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-3">
              <div className="relative flex items-center justify-center w-11 h-11 rounded-2xl bg-indigo-500/10 border border-indigo-500/30 shadow-[0_0_15px_rgba(99,102,241,0.2)]">
                <img src="/logo.svg" alt="CF Logo" className="w-6 h-6 object-contain" />
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-cyan-500" />
                </span>
              </div>
              <div>
                <h3 className="text-base font-bold text-slate-100 tracking-tight flex items-center gap-2">
                  Consortium Node Initialization
                </h3>
                <p className="text-xs font-mono text-cyan-400">
                  CF-Intelligence · Cross-Bank Federated Operations
                </p>
              </div>
            </div>
            <button
              onClick={onComplete}
              className="px-3 py-1.5 rounded-xl text-[11px] font-mono text-slate-300 hover:text-white bg-white/5 hover:bg-white/10 border border-white/10 transition-all cursor-pointer shadow-sm"
            >
              Skip Intro ➔
            </button>
          </div>

          {/* Cross-Bank Network Topology Visualizer */}
          <div className="mb-5 p-4 rounded-2xl bg-[#03040f] border border-white/8 relative overflow-hidden">
            <div className="flex items-center justify-between text-[10px] font-mono text-slate-400 mb-3 border-b border-white/5 pb-2">
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                CROSS-BANK FEDERATED TOPOLOGY
              </span>
              <span className="text-indigo-400">mTLS 1.3 ACTIVE</span>
            </div>

            {/* Interactive SVG Network Map */}
            <div className="relative h-28 flex items-center justify-between px-4 sm:px-8">
              {/* SVG Connecting Lines */}
              <svg className="absolute inset-0 w-full h-full pointer-events-none stroke-indigo-500/30 stroke-[2] [stroke-dasharray:4_4]">
                <line x1="20%" y1="50%" x2="50%" y2="50%" className="animate-pulse" />
                <line x1="80%" y1="50%" x2="50%" y2="50%" className="animate-pulse" />
                <line x1="50%" y1="20%" x2="50%" y2="80%" className="animate-pulse" />
              </svg>

              {/* Bank Node Alpha (JPMorgan) */}
              <div className="relative z-10 flex flex-col items-center">
                <div className="w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-400/50 flex items-center justify-center text-xs font-mono font-bold text-indigo-300 shadow-[0_0_15px_rgba(99,102,241,0.3)]">
                  JPM
                </div>
                <span className="text-[9.5px] font-mono text-slate-300 mt-1">Bank Alpha</span>
                <span className="text-[8.5px] font-mono text-emerald-400">Node #01</span>
              </div>

              {/* Central FL Coordinator Node */}
              <div className="relative z-10 flex flex-col items-center">
                <div className="relative w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-600 via-purple-600 to-cyan-500 flex items-center justify-center p-0.5 shadow-[0_0_25px_rgba(6,182,212,0.5)]">
                  <div className="w-full h-full rounded-[14px] bg-[#060719] flex flex-col items-center justify-center">
                    <span className="text-[11px] font-extrabold font-mono text-cyan-300">FL</span>
                    <span className="text-[8px] font-mono text-slate-400">SERVER</span>
                  </div>
                </div>
                <span className="text-[10px] font-mono font-bold text-cyan-300 mt-1">FL Coordinator</span>
                <span className="text-[8.5px] font-mono text-indigo-400">SGX Enclave</span>
              </div>

              {/* Bank Node Beta (HSBC) */}
              <div className="relative z-10 flex flex-col items-center">
                <div className="w-10 h-10 rounded-xl bg-teal-500/20 border border-teal-400/50 flex items-center justify-center text-xs font-mono font-bold text-teal-300 shadow-[0_0_15px_rgba(20,184,166,0.3)]">
                  HSBC
                </div>
                <span className="text-[9.5px] font-mono text-slate-300 mt-1">Bank Beta</span>
                <span className="text-[8.5px] font-mono text-emerald-400">Node #02</span>
              </div>
            </div>
          </div>

          {/* Progress Bar Container */}
          <div className="space-y-2 mb-5">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-300 font-medium">Consortium Telemetry Synchronization</span>
              <span className="text-cyan-400 font-bold">{Math.min(100, Math.round(progress))}%</span>
            </div>
            <div className="relative w-full h-2.5 bg-slate-900/90 rounded-full overflow-hidden border border-white/10 p-0.5">
              <motion.div
                className="h-full bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400 rounded-full shadow-[0_0_15px_rgba(6,182,212,0.7)]"
                style={{ width: `${Math.min(100, progress)}%` }}
                transition={{ ease: 'linear' }}
              />
            </div>
          </div>

          {/* Initialization Stage Cards */}
          <div className="space-y-2 mb-5">
            {STAGES.map((stage, idx) => {
              const isCompleted = idx < currentStageIdx;
              const isActive = idx === currentStageIdx;

              return (
                <div
                  key={stage.id}
                  className={`flex items-center justify-between p-2.5 rounded-xl border transition-all duration-300 ${
                    isActive
                      ? 'bg-indigo-500/12 border-indigo-500/50 shadow-[0_0_20px_rgba(99,102,241,0.2)]'
                      : isCompleted
                      ? 'bg-white/[0.03] border-emerald-500/25 text-slate-300'
                      : 'bg-white/[0.01] border-white/5 opacity-40'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="shrink-0">
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
                      <p className="text-[10.5px] text-slate-400 truncate mt-0.5">
                        {stage.subtext}
                      </p>
                    </div>
                  </div>

                  <span
                    className={`text-[9.5px] font-mono shrink-0 px-2 py-0.5 rounded-md border ml-2 ${
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
          <div className="rounded-xl bg-[#03030c] border border-white/10 p-3 space-y-1 font-mono text-[10.5px] text-slate-400 overflow-hidden">
            <div className="flex items-center justify-between text-[9px] text-slate-500 border-b border-white/5 pb-1.5 mb-1">
              <span>TELEMETRY STREAM LOG</span>
              <span className="text-cyan-400 animate-pulse">LIVE VERIFICATION</span>
            </div>
            <div className="h-12 overflow-y-auto space-y-1">
              {logMessages.length === 0 ? (
                <div className="text-slate-600 italic">Initializing telemetry stream...</div>
              ) : (
                logMessages.map((msg, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-slate-300">
                    <span className="text-cyan-400 shrink-0">›</span>
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
