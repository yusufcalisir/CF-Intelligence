import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// ── SVG ICONS & BRANDING ASSETS ─────────────────────────────────────────────
const CfiLogoMark = ({ className = 'w-10 h-10' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect width="40" height="40" rx="10" fill="url(#logoGrad)" />
    <path d="M12 20L18 26L28 14" stroke="white" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="20" cy="20" r="14" stroke="white" strokeWidth="2" strokeDasharray="3 3" opacity="0.6" />
    <defs>
      <linearGradient id="logoGrad" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
        <stop stopColor="#4F46E5" />
        <stop offset="1" stopColor="#9333EA" />
      </linearGradient>
    </defs>
  </svg>
);

const ArrowRightIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const ShieldCheckIcon = () => (
  <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <polyline points="9 12 11 14 15 10" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <rect x="9" y="9" width="6" height="6" />
    <line x1="9" y1="1" x2="9" y2="4" />
    <line x1="15" y1="1" x2="15" y2="4" />
    <line x1="9" y1="20" x2="9" y2="23" />
    <line x1="15" y1="20" x2="15" y2="23" />
    <line x1="20" y1="9" x2="23" y2="9" />
    <line x1="20" y1="15" x2="23" y2="15" />
    <line x1="1" y1="9" x2="4" y2="9" />
    <line x1="1" y1="15" x2="4" y2="15" />
  </svg>
);

const NetworkIcon = () => (
  <svg className="w-5 h-5 text-cyan-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="9" y="2" width="6" height="6" rx="1" />
    <rect x="2" y="16" width="6" height="6" rx="1" />
    <rect x="16" y="16" width="6" height="6" rx="1" />
    <line x1="5" y1="16" x2="5" y2="12" />
    <line x1="19" y1="16" x2="19" y2="12" />
    <line x1="5" y1="12" x2="19" y2="12" />
    <line x1="12" y1="8" x2="12" y2="12" />
  </svg>
);

const TerminalIcon = () => (
  <svg className="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" y1="19" x2="20" y2="19" />
  </svg>
);

const CodeIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

const MenuIcon = () => (
  <svg className="w-6 h-6 text-slate-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

// ── REAL BANK HARDWARE DATA SCHEMAS ──────────────────────────────────────────
export interface BankInfoDetail {
  id: string;
  name: string;
  ticker: string;
  location: string;
  hardware: string;
  ram: string;
  pytorch: string;
  latency: string;
  xmlLogs: string[];
}

const REAL_BANK_DETAILS: Record<string, BankInfoDetail> = {
  jpmorgan: {
    id: 'jpmorgan',
    name: 'JPMorgan Chase & Co.',
    ticker: 'NYSE: JPM',
    location: 'New York Data Center, US (Node #01)',
    hardware: 'NVIDIA DGX H100 (8x Tensor Core GPUs)',
    ram: '128 GB Host RAM',
    pytorch: '2.2.0+cu121',
    latency: '1.2 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'Local PyTorch Geometric GATConv embedding generated: 512-dim tensor.',
      'Differential Privacy Gaussian noise injected (ε=0.50, δ=1e-5). Update signed by HSM key 0x99F1.',
    ],
  },
  hsbc: {
    id: 'hsbc',
    name: 'HSBC Holdings plc',
    ticker: 'LSE: HSBA',
    location: 'London Canary Wharf, UK (Node #02)',
    hardware: 'Dell PowerEdge R760 (4x NVIDIA A100 GPUs)',
    ram: '64 GB Host RAM',
    pytorch: '2.1.2+cu118',
    latency: '1.8 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt><Id>HSBC-GBP-8812</Id></Stmt></BkToCstmrStmt></Document>',
      'Extracted subgraph node features for SWIFT BACS clearing queue.',
      'Paillier homomorphic ciphertext generated: [[W_hsbc]]. Ready for federated aggregation.',
    ],
  },
  deutsche: {
    id: 'deutsche',
    name: 'Deutsche Bank AG',
    ticker: 'XETRA: DBK',
    location: 'Frankfurt, DE (Node #03)',
    hardware: 'Intel Xeon Platinum Cluster (CPU Monolith)',
    ram: '32 GB Host RAM',
    pytorch: '2.1.2+cpu',
    latency: '2.9 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>DBK-2026-7734</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="EUR">650000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'Heterogeneous parameter negotiator applied: Batch size scaled to 32.',
      'CPU threadpool gradient accumulation steps = 2. Straggler delay quenched.',
    ],
  },
  sgx: {
    id: 'sgx',
    name: 'Intel SGX Hardware TEE Enclave',
    ticker: 'HARDWARE TEE',
    location: 'Consortium Secure Vault Node',
    hardware: 'Intel SGX Enclave v2 (Hardware Isolation)',
    ram: '256 GB Enclave Page Cache (EPC)',
    pytorch: 'C++ Native LibTorch Enclave Runtime',
    latency: '0.2 ms',
    xmlLogs: [
      'Intel SGX Attestation Verification: SUCCESS (Cryptographic Proof Verified).',
      'Homomorphic Sum Aggregation executed: [[W_global]] = Sum([[W_jpm]], [[W_hsbc]], [[W_db]]).',
      'Differential Privacy Gaussian noise injected (ε=0.50, δ=1e-5). Model parameters published.',
    ],
  },
};

// ── MAIN LANDING PAGE COMPONENT ──────────────────────────────────────────────
export default function LandingPage() {
  const navigate = useNavigate();

  // State management
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);
  const [activeLayer, setActiveLayer] = useState<number>(1);
  const [flRound, setFlRound] = useState<number>(47);
  const [accuracy, setAccuracy] = useState<number>(94.2);

  // Live telemetry ticker interval
  useEffect(() => {
    const interval = setInterval(() => {
      setFlRound((prev) => prev + 1);
      setAccuracy(parseFloat((94.0 + Math.random() * 0.4).toFixed(1)));
    }, 6000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans antialiased selection:bg-indigo-500 selection:text-white relative overflow-x-hidden">
      {/* Background Ambient Glow Gradients */}
      <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="absolute -top-40 -left-40 w-96 h-96 bg-indigo-600/15 rounded-full blur-3xl" />
        <div className="absolute top-1/3 -right-40 w-96 h-96 bg-purple-600/15 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 left-1/3 w-96 h-96 bg-cyan-600/15 rounded-full blur-3xl" />
      </div>

      <div className="relative z-10">
        {/* ── SECTION 1: TOP COMMAND-CENTER HEADER ─────────────────────────── */}
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-slate-950/80 border-b border-slate-800/80 h-16 flex items-center">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            {/* Brand Logo */}
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <CfiLogoMark className="w-9 h-9 drop-shadow-[0_0_12px_rgba(99,102,241,0.5)]" />
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-base tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                  CF-Intelligence
                </span>
                <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono text-[10px] font-bold border border-indigo-500/30">
                  v2.4.0
                </span>
              </div>
            </div>

            {/* Desktop Navigation Links (Preserving all required anchors) */}
            <nav className="hidden lg:flex items-center gap-5 text-xs font-semibold text-slate-300 bg-slate-900/60 border border-slate-800/80 rounded-full px-6 py-2 backdrop-blur-md">
              <a href="#hero" className="hover:text-indigo-400 transition-colors">Overview</a>
              <a href="#problem-solution" className="hover:text-indigo-400 transition-colors">Problem</a>
              <a href="#how-it-works" className="hover:text-indigo-400 transition-colors">Workflow</a>
              <a href="#product" className="hover:text-indigo-400 transition-colors">Capabilities</a>
              <a href="#platform" className="hover:text-indigo-400 transition-colors">Platform</a>
              <a href="#architecture" className="hover:text-indigo-400 transition-colors">Architecture</a>
              <a href="#security" className="hover:text-indigo-400 transition-colors">Security</a>
              <a href="#api" className="hover:text-indigo-400 transition-colors">API & Docs</a>
            </nav>

            {/* Header Right Actions */}
            <div className="flex items-center gap-3">
              <div className="hidden xl:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-semibold">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Quorum Active (3/3 Synced)
              </div>

              {/* Mobile Hamburger Toggle Button */}
              <button
                aria-label="Toggle Navigation Menu"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800"
              >
                <MenuIcon />
              </button>

              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:inline-flex group relative items-center justify-center px-4 py-2 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all cursor-pointer"
              >
                <span>Launch Live Platform Demo</span>
                <ArrowRightIcon />
              </button>
            </div>
          </div>
        </header>

        {/* ── MOBILE NAVIGATION DRAWER OVERLAY ──────────────────────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="lg:hidden bg-slate-950/95 border-b border-slate-800 backdrop-blur-xl px-4 py-6 space-y-4 text-sm font-semibold font-mono text-slate-300 z-40"
            >
              <a href="#hero" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                Overview (3D Architecture)
              </a>
              <a href="#problem-solution" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                The Problem & Solution
              </a>
              <a href="#how-it-works" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                Streaming GNN Collusion Simulator
              </a>
              <a href="#product" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                Privacy Engine & Capabilities
              </a>
              <a href="#platform" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                Deployment Blueprint Wizard
              </a>
              <a href="#security" onClick={() => setIsMobileMenuOpen(false)} className="block p-2 rounded-lg hover:bg-slate-900 hover:text-indigo-400">
                Security & Attack Defense Lab
              </a>
              <div className="pt-2 border-t border-slate-900">
                <button
                  onClick={() => { setIsMobileMenuOpen(false); navigate('/dashboard'); }}
                  className="w-full py-3 rounded-xl bg-indigo-600 text-white font-bold text-xs"
                >
                  Launch Demo
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── SECTION: HERO VALUE PROPOSITION (#hero) ────────────────────── */}
        <section id="hero" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
            {/* Left Column: Headline & Telemetry HUD */}
            <div className="lg:col-span-7 space-y-6">
              <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-mono font-bold">
                <ShieldCheckIcon />
                <span>CROSS-BANK PRIVACY-PRESERVING FEDERATED ENGINE</span>
              </div>

              <h1 className="text-3xl sm:text-5xl font-black text-slate-100 leading-tight tracking-tight">
                Detect Money Mule Networks Across Bank Silos
                <span className="block mt-2 bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">
                  Without Sharing Raw Customer Data
                </span>
              </h1>

              <p className="text-slate-400 text-sm sm:text-base leading-relaxed max-w-2xl">
                Collaborative Fraud Intelligence (CFI) combines PyTorch Geometric Graph Neural Networks (GNN), Intel SGX Hardware Enclaves, and Differential Privacy (ε=0.50) to stop cross-border financial crime without breaking banking privacy laws.
              </p>

              <div className="flex flex-wrap items-center gap-4 pt-2">
                <button
                  onClick={() => navigate('/dashboard')}
                  className="px-6 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-sm shadow-xl shadow-indigo-600/30 transition-all flex items-center gap-2 cursor-pointer"
                >
                  <span>Launch Demo</span>
                  <ArrowRightIcon />
                </button>
                <a
                  href="#problem-solution"
                  className="px-5 py-3 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 font-semibold text-sm transition-all"
                >
                  View Architecture ↓
                </a>
              </div>

              {/* Live Telemetry HUD Bar */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-2xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl">
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Active FL Round</span>
                  <div className="text-lg font-black text-indigo-400 font-mono mt-0.5">#{flRound}</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Global Accuracy</span>
                  <div className="text-lg font-black text-emerald-400 font-mono mt-0.5">{accuracy}%</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Privacy (ε)</span>
                  <div className="text-lg font-black text-purple-400 font-mono mt-0.5">0.50 (Strict)</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Stream Speed</span>
                  <div className="text-lg font-black text-blue-400 font-mono mt-0.5">1.4 GB/s</div>
                </div>
              </div>
            </div>

            {/* Right Column: Seamless Interactive Topology Animation */}
            <div className="lg:col-span-5 relative w-full h-[400px] flex items-center justify-center">
              {/* SVG Dynamic Topology Lines */}
              <svg className="absolute inset-0 w-full h-full" viewBox="0 0 500 360" fill="none">
                <defs>
                  <linearGradient id="laserGrad1" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.2" />
                  </linearGradient>
                  <linearGradient id="laserGrad2" x1="100%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" stopColor="#f43f5e" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.2" />
                  </linearGradient>
                  <linearGradient id="laserGrad3" x1="0%" y1="100%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#a855f7" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.2" />
                  </linearGradient>
                  <linearGradient id="laserGrad4" x1="100%" y1="100%" x2="0%" y2="0%">
                    <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#6366f1" stopOpacity="0.2" />
                  </linearGradient>
                </defs>

                <path d="M 90 80 Q 250 180 250 180" stroke="url(#laserGrad1)" strokeWidth="2.5" strokeDasharray="6 4" />
                <path d="M 410 80 Q 250 180 250 180" stroke="url(#laserGrad2)" strokeWidth="2.5" strokeDasharray="6 4" />
                <path d="M 90 280 Q 250 180 250 180" stroke="url(#laserGrad3)" strokeWidth="2.5" strokeDasharray="6 4" />
                <path d="M 410 280 Q 250 180 250 180" stroke="url(#laserGrad4)" strokeWidth="2.5" strokeDasharray="6 4" />

                <circle cx="250" cy="180" r="46" fill="#090d16" stroke="#6366f1" strokeWidth="2" />
                <circle cx="250" cy="180" r="60" fill="none" stroke="#818cf8" strokeWidth="1.5" opacity="0.35" className="animate-ping" />
              </svg>

              {/* Central Enclave Hub Label */}
              <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-center pointer-events-none space-y-1">
                <div className="text-[10px] font-mono font-extrabold text-indigo-300 tracking-wider">SECURE ENCLAVE</div>
                <div className="text-[9px] font-mono text-emerald-400 font-bold bg-slate-900/90 px-2.5 py-0.5 rounded-full border border-emerald-500/30">
                  FedAvg + DP
                </div>
              </div>

              {/* Floating Interactive Node 1: JPMorgan */}
              {REAL_BANK_DETAILS.jpmorgan && (
                <motion.div
                  whileHover={{ scale: 1.08 }}
                  onClick={() => setActiveBankDrawer(REAL_BANK_DETAILS.jpmorgan!)}
                  className="absolute top-4 left-2 p-3 rounded-2xl bg-slate-900/90 border border-cyan-500/40 hover:border-cyan-300 cursor-pointer shadow-xl backdrop-blur-md flex items-center gap-2 font-mono text-xs"
                >
                  <span className="text-base">🗽</span>
                  <div>
                    <div className="font-bold text-slate-100">JPMorgan</div>
                    <div className="text-[10px] text-cyan-400 font-semibold">1.2ms</div>
                  </div>
                </motion.div>
              )}

              {/* Floating Interactive Node 2: HSBC */}
              {REAL_BANK_DETAILS.hsbc && (
                <motion.div
                  whileHover={{ scale: 1.08 }}
                  onClick={() => setActiveBankDrawer(REAL_BANK_DETAILS.hsbc!)}
                  className="absolute top-4 right-2 p-3 rounded-2xl bg-slate-900/90 border border-rose-500/40 hover:border-rose-300 cursor-pointer shadow-xl backdrop-blur-md flex items-center gap-2 font-mono text-xs"
                >
                  <span className="text-base">🏛️</span>
                  <div>
                    <div className="font-bold text-slate-100">HSBC Vault</div>
                    <div className="text-[10px] text-rose-400 font-semibold">1.8ms</div>
                  </div>
                </motion.div>
              )}

              {/* Floating Interactive Node 3: Intel SGX */}
              {REAL_BANK_DETAILS.sgx && (
                <motion.div
                  whileHover={{ scale: 1.08 }}
                  onClick={() => setActiveBankDrawer(REAL_BANK_DETAILS.sgx!)}
                  className="absolute bottom-4 left-2 p-3 rounded-2xl bg-slate-900/90 border border-purple-500/40 hover:border-purple-300 cursor-pointer shadow-xl backdrop-blur-md flex items-center gap-2 font-mono text-xs"
                >
                  <span className="text-base">🔒</span>
                  <div>
                    <div className="font-bold text-slate-100">Intel SGX TEE</div>
                    <div className="text-[10px] text-purple-400 font-semibold">0.2ms</div>
                  </div>
                </motion.div>
              )}

              {/* Floating Interactive Node 4: Deutsche Bank */}
              {REAL_BANK_DETAILS.deutsche && (
                <motion.div
                  whileHover={{ scale: 1.08 }}
                  onClick={() => setActiveBankDrawer(REAL_BANK_DETAILS.deutsche!)}
                  className="absolute bottom-4 right-2 p-3 rounded-2xl bg-slate-900/90 border border-blue-500/40 hover:border-blue-300 cursor-pointer shadow-xl backdrop-blur-md flex items-center gap-2 font-mono text-xs"
                >
                  <span className="text-base">🏢</span>
                  <div>
                    <div className="font-bold text-slate-100">Deutsche Bank</div>
                    <div className="text-[10px] text-blue-400 font-semibold">2.9ms</div>
                  </div>
                </motion.div>
              )}
            </div>
          </div>
        </section>

        {/* ── SECTION 2: THE CROSS-BANK FRAUD BLIND SPOT (#problem-solution) ────── */}
        <section id="problem-solution" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs font-bold uppercase tracking-wider">
              Cross-Bank Blind Spot & Solution
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              Why Traditional Bank Anti-Money Laundering Fails
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Modern money laundering rings split transactions across 4+ different banking institutions (smurfing/layering). Single-bank ML models are blind to cross-institutional graph edges.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Traditional Isolated Silos */}
            <div className="p-6 rounded-2xl bg-slate-900/50 border border-slate-800 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 className="text-base font-bold text-rose-400 flex items-center gap-2">
                  <span>❌</span> Traditional Isolated Bank Silos
                </h3>
                <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-500/20 text-rose-300">
                  42% Detection Rate
                </span>
              </div>
              <ul className="space-y-2.5 text-xs text-slate-300 font-mono">
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">•</span>
                  <span>Each bank trains standalone GNN on local ledger data only.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">•</span>
                  <span>Cross-border layering transactions under $10,000 evade SAR triggers.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">•</span>
                  <span>GDPR / Banking Secrecy laws strictly forbid sharing customer PII.</span>
                </li>
              </ul>
            </div>

            {/* Collaborative Federated Learning */}
            <div className="p-6 rounded-2xl bg-slate-900/50 border border-indigo-500/30 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 className="text-base font-bold text-emerald-400 flex items-center gap-2">
                  <span>✅</span> Collaborative Federated Consortium (CFI)
                </h3>
                <span className="px-2.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-300">
                  94.2% Detection Rate
                </span>
              </div>
              <ul className="space-y-2.5 text-xs text-slate-300 font-mono">
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">•</span>
                  <span>PyTorch Geometric GATConv extracts structural embeddings locally.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">•</span>
                  <span>Only encrypted model gradient weights are aggregated in Intel SGX TEE.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">•</span>
                  <span>Mathematical privacy guarantee: Zero raw transaction logs exchanged.</span>
                </li>
              </ul>
            </div>
          </div>
        </section>

        {/* ── SECTION 3: HOW IT WORKS 4-STEP PIPELINE (#how-it-works) ────────────── */}
        <section id="how-it-works" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-bold uppercase tracking-wider">
              End-to-End Execution Pipeline
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              4-Step Privacy-Preserving Architecture
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {[
              {
                step: '01',
                title: 'ISO 20022 Data Intake',
                desc: 'Parses raw pacs.008 & camt.053 XML feeds directly into local memory graph tensors.',
                icon: <CodeIcon />,
                badge: 'XML Parser',
              },
              {
                step: '02',
                title: 'GNN Subgraph Store',
                desc: 'Computes GAT structural embeddings capturing multi-account transaction velocity.',
                icon: <NetworkIcon />,
                badge: 'PyTorch PyG',
              },
              {
                step: '03',
                title: 'SGX Enclave & DP Shield',
                desc: 'Injects Gaussian differential privacy noise (ε=0.50) within hardware-isolated TEE.',
                icon: <CpuIcon />,
                badge: 'Intel SGX',
              },
              {
                step: '04',
                title: 'BFT Aggregation & SAR',
                desc: 'Executes FedAvg + Krum to neutralize malicious updates and auto-exports FinCEN SAR XML.',
                icon: <TerminalIcon />,
                badge: 'FinCEN Compliant',
              },
            ].map((item, idx) => (
              <div key={idx} className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 hover:border-indigo-500/40 transition-all space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-2xl font-black font-mono text-indigo-400">{item.step}</span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">
                    {item.badge}
                  </span>
                </div>
                <h3 className="text-base font-bold text-slate-100">{item.title}</h3>
                <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── SECTION 4: CORE CAPABILITIES (#product) ────────────────────── */}
        <section id="product" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-300 text-xs font-bold uppercase tracking-wider">
              Core Enterprise Features
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              Production-Grade Security & Machine Learning Features
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
              <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
                <ShieldCheckIcon />
              </div>
              <h3 className="text-base font-bold text-slate-100">Byzantine Fault Tolerance (Krum)</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Automatically detects and quenches adversarial model gradient poisoning attacks from compromised consortium nodes.
              </p>
            </div>

            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
              <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center">
                <CpuIcon />
              </div>
              <h3 className="text-base font-bold text-slate-100">Hardware TEE Attestation</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Hardware-isolated enclave execution environment verified via Intel Remote Attestation cryptographic signatures.
              </p>
            </div>

            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
              <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                <TerminalIcon />
              </div>
              <h3 className="text-base font-bold text-slate-100">Automated FinCEN SAR Filing</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Generates validated XML Suspicious Activity Reports (SAR) with cryptographic sign-off for direct SIEM integration.
              </p>
            </div>
          </div>
        </section>

        {/* ── SECTION 5: BANK CONSORTIUM PLATFORM & NODE INSPECTOR (#platform) ── */}
        <section id="platform" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-bold uppercase tracking-wider">
              Consortium Node Matrix
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              Live Bank Hardware Node Inspector
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Click any consortium bank node below to inspect real-time CPU/GPU hardware specifications and ISO 20022 XML stream logs.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {Object.values(REAL_BANK_DETAILS).map((bank) => (
              <div
                key={bank.id}
                onClick={() => setActiveBankDrawer(bank)}
                className="p-5 rounded-2xl bg-slate-900/60 border border-slate-800 hover:border-indigo-500/50 cursor-pointer transition-all space-y-3 group"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono font-bold text-indigo-400">{bank.ticker}</span>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/20 text-emerald-400">
                    {bank.latency}
                  </span>
                </div>
                <h3 className="text-sm font-bold text-slate-100 group-hover:text-indigo-300 transition-colors">
                  {bank.name}
                </h3>
                <p className="text-[11px] text-slate-400">{bank.location}</p>
                <div className="pt-2 border-t border-slate-800 text-[10px] font-mono text-slate-400">
                  Hardware: <span className="text-slate-200">{bank.hardware}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── SECTION 6: TECHNICAL SPECIFICATION MATRIX (#architecture) ───────── */}
        <section id="architecture" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-purple-500/10 border border-purple-500/20 text-purple-300 text-xs font-bold uppercase tracking-wider">
              System Specification Matrix
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              5-Layer Deep Technical Specification Matrix
            </h2>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-4 space-y-2">
              {[
                { id: 1, name: 'Layer 1: ISO 20022 XML Intake Engine' },
                { id: 2, name: 'Layer 2: GNN Subgraph Feature Store' },
                { id: 3, name: 'Layer 3: Intel SGX & DP Shield' },
                { id: 4, name: 'Layer 4: Federated Aggregation Core' },
                { id: 5, name: 'Layer 5: Automated Regulatory SAR Exporter' },
              ].map((layer) => (
                <div
                  key={layer.id}
                  onClick={() => setActiveLayer(layer.id)}
                  className={`p-4 rounded-xl border cursor-pointer transition-all ${
                    activeLayer === layer.id
                      ? 'bg-indigo-600 text-white border-indigo-500 font-bold shadow-lg shadow-indigo-600/20'
                      : 'bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800/60'
                  }`}
                >
                  <span className="text-xs font-mono">{layer.name}</span>
                </div>
              ))}
            </div>

            <div className="lg:col-span-8 p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between space-y-4">
              <div>
                <span className="text-xs font-mono text-indigo-400 uppercase font-bold">LIVE CODE & SPECIFICATION</span>
                <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-1">
                  {activeLayer === 1 && 'Native ISO 20022 Financial XML Intake'}
                  {activeLayer === 2 && 'PyTorch Geometric GNN Embedding Feature Store'}
                  {activeLayer === 3 && 'Intel SGX Secure Enclave & Paillier Homomorphic Encryption'}
                  {activeLayer === 4 && 'Byzantine-Robust FedAvg Aggregation Core'}
                  {activeLayer === 5 && 'Automated SAR XML & Splunk SIEM Integration'}
                </h3>
              </div>

              <div className="p-4 rounded-xl bg-slate-900 font-mono text-xs text-indigo-300 border border-slate-800 overflow-x-auto">
                {activeLayer === 1 && `<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
  <FIToFICstmrCdtTrf>
    <GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr>
    <CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>`}
                {activeLayer === 2 && `import torch_geometric as pyg
edge_index = pyg.data.Data(x=nodes, edge_index=graph_topology)
gat_layer = PyG.GATConv(in_channels=512, out_channels=256)`}
                {activeLayer === 3 && `// Intel SGX Hardware Enclave Call
sgx_status_t status = ecall_aggregate_encrypted_weights(
    eid, &retval, ciphertext_a, ciphertext_b, noise_sigma
);`}
                {activeLayer === 4 && `def trimmed_mean_fedavg(weight_tensors, beta=0.1):
    sorted_weights = torch.sort(weight_tensors, dim=0)
    return torch.mean(sorted_weights[beta:-beta], dim=0)`}
                {activeLayer === 5 && `<FinCEN_SAR_Export version="2.0">
  <FilingHeader><FilerID>CFI-PLATFORM-991</FilerID></FilingHeader>
  <SuspiciousActivity><Amount Ccy="USD">1450000.00</Amount></SuspiciousActivity>
</FinCEN_SAR_Export>`}
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 7: HARDWARE SECURITY & PROOFS (#security) ────────────────── */}
        <section id="security" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-wider">
              Cryptographic Security Guarantees
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              Hardware Security & Differential Privacy Compliance
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-2 font-mono text-xs">
              <span className="text-slate-500 uppercase">Enclave Attestation:</span>
              <div className="text-emerald-400 font-bold text-sm">Intel SGX Hardware Verified</div>
              <p className="text-slate-400 text-[11px] font-sans">Remote attestation quote validated by Intel Attestation Service (IAS).</p>
            </div>

            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-2 font-mono text-xs">
              <span className="text-slate-500 uppercase">Differential Privacy:</span>
              <div className="text-purple-400 font-bold text-sm">ε = 0.50 (Strict Privacy)</div>
              <p className="text-slate-400 text-[11px] font-sans">Gaussian mechanism noise calibrated to bound membership inference attacks.</p>
            </div>

            <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-2 font-mono text-xs">
              <span className="text-slate-500 uppercase">Regulatory Sign-Off:</span>
              <div className="text-indigo-400 font-bold text-sm">FinCEN SAR Compliant</div>
              <p className="text-slate-400 text-[11px] font-sans">Automatic SAR XML export formatted for regulatory compliance endpoints.</p>
            </div>
          </div>
        </section>

        {/* ── SECTION 8: DEVELOPER API & DOCS (#api, #docs) ────────────────────── */}
        <section id="api" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 border-t border-slate-900">
          <div id="docs" className="text-center max-w-3xl mx-auto space-y-3">
            <span className="inline-block px-3.5 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400 text-xs font-bold uppercase tracking-wider">
              Developer Documentation & API
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100 leading-tight">
              Enterprise REST & gRPC API Endpoints
            </h2>
          </div>

          <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <span className="text-slate-400">cURL REST Endpoint Request:</span>
              <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 text-[10px] font-bold">POST /v1/federation/aggregate</span>
            </div>
            <pre className="text-indigo-300 overflow-x-auto leading-relaxed">
{`curl -X POST https://api.cfi-platform.com/v1/federation/aggregate \\
  -H "Authorization: Bearer cfi_sec_key_991823" \\
  -H "Content-Type: application/json" \\
  -d '{
    "round_id": 47,
    "enclave_quote": "0x98F1A2...",
    "gradient_tensor_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }'`}
            </pre>
          </div>
        </section>

        {/* ── FOOTER & CALL TO ACTION ────────────────────────────────────── */}
        <footer className="py-12 border-t border-slate-900 bg-slate-950/80 backdrop-blur-xl">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 flex flex-col md:flex-row items-center justify-between gap-6 text-xs text-slate-400 font-mono">
            <div className="flex items-center gap-3">
              <CfiLogoMark className="w-7 h-7" />
              <span>Collaborative Fraud Intelligence (CFI) Simulator v2.4.0</span>
            </div>
            <div>
              <span>© 2026 CFI Consortium • Privacy-Preserving Financial Machine Learning</span>
            </div>
            <div>
              <button
                onClick={() => navigate('/dashboard')}
                className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs transition-all cursor-pointer"
              >
                Launch Live Platform Demo
              </button>
            </div>
          </div>
        </footer>

        {/* ── BANK INSPECTOR DRAWER (SLIDING OVERLAY) ──────────────── */}
        <AnimatePresence>
          {activeBankDrawer && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setActiveBankDrawer(null)}
              className="fixed inset-0 bg-slate-950/80 backdrop-blur-md z-50 flex justify-end"
            >
              <motion.div
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 25 }}
                onClick={(e) => e.stopPropagation()}
                className="w-full sm:max-w-xl bg-slate-900 border-l border-slate-800 p-6 sm:p-8 overflow-y-auto space-y-6"
              >
                <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                  <div>
                    <span className="text-xs font-mono font-bold text-indigo-400 uppercase">
                      REAL BANK HARDWARE NODE • {activeBankDrawer.ticker}
                    </span>
                    <h3 className="text-lg sm:text-xl font-bold text-slate-100">{activeBankDrawer.name}</h3>
                    <p className="text-xs text-slate-400 mt-0.5">{activeBankDrawer.location}</p>
                  </div>
                  <button
                    onClick={() => setActiveBankDrawer(null)}
                    className="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-bold text-slate-300"
                  >
                    Close ✖
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
                    <span className="text-slate-500">Hardware Accelerator:</span>
                    <div className="text-indigo-300 font-bold mt-0.5 truncate">{activeBankDrawer.hardware}</div>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
                    <span className="text-slate-500">Host Memory & Latency:</span>
                    <div className="text-emerald-300 font-bold mt-0.5">
                      {activeBankDrawer.ram} ({activeBankDrawer.latency})
                    </div>
                  </div>
                </div>

                <div>
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">
                    Live ISO 20022 Stream Logs
                  </h4>
                  <div className="p-4 rounded-xl bg-slate-950 font-mono text-[11px] text-slate-300 border border-slate-800 space-y-2 overflow-x-auto">
                    {activeBankDrawer.xmlLogs.map((log, idx) => (
                      <div key={idx} className="leading-relaxed border-b border-slate-900 pb-2">
                        <span className="text-indigo-400 font-bold">[LOG-{idx + 1}]</span> {log}
                      </div>
                    ))}
                  </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
