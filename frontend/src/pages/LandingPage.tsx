import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// SVG Icon Components
const ShieldIcon = () => (
  <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);

const ZapIcon = () => (
  <svg className="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <rect x="9" y="9" width="6" height="6" />
    <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 15h3M1 9h3M1 15h3" />
  </svg>
);

const ServerIcon = () => (
  <svg className="w-5 h-5 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
    <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
    <line x1="6" y1="6" x2="6.01" y2="6" />
    <line x1="6" y1="18" x2="6.01" y2="18" />
  </svg>
);

const CodeIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

const CheckIcon = () => (
  <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const ArrowRightIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const LockIcon = () => (
  <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);

const ActivityIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
);

export default function LandingPage() {
  const navigate = useNavigate();

  // Hero particle controls
  const [isPlaying, setIsPlaying] = useState<boolean>(true);
  const [isDpShieldActive, setIsDpShieldActive] = useState<boolean>(true);
  const [fraudWaveCount, setFraudWaveCount] = useState<number>(0);
  const [selectedBank, setSelectedBank] = useState<string | null>(null);

  // Live Telemetry HUD state simulation
  const [flRound, setFlRound] = useState<number>(42);
  const [accuracy, setAccuracy] = useState<number>(98.4);
  const [activeTab, setActiveTab] = useState<'operations' | 'cases' | 'graph' | 'privacy'>('operations');

  // Code Studio Language State
  const [codeLang, setCodeLang] = useState<'python' | 'curl' | 'typescript' | 'k8s'>('python');
  const [codeEpsilon, setCodeEpsilon] = useState<number>(0.5);
  const [copiedCode, setCopiedCode] = useState<boolean>(false);

  // ABAC Simulator State
  const [abacRole, setAbacRole] = useState<'compliance_officer' | 'junior_analyst'>('compliance_officer');
  const [abacSensitivity, setAbacSensitivity] = useState<'tier_1' | 'tier_3'>('tier_1');
  const [abacAction, setAbacAction] = useState<'export_sar' | 'view_metrics'>('export_sar');

  // Increment FL round periodically
  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setFlRound((prev) => prev + 1);
      setAccuracy((prev) => Number((prev + (Math.random() * 0.08 - 0.04)).toFixed(2)));
    }, 4000);
    return () => clearInterval(interval);
  }, [isPlaying]);

  const handleInjectFraudWave = () => {
    setFraudWaveCount((prev) => prev + 1);
  };

  const handleCopyCode = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  };

  // ABAC Evaluation
  const isAbacAllowed = abacRole === 'compliance_officer' || abacAction === 'view_metrics';

  // Dynamic Code Snippets
  const getCodeSnippet = () => {
    if (codeLang === 'python') {
      return `import cfi_intelligence as cfi

# Initialize bank node client with Hardware Acceleration
client = cfi.BankNodeClient(
    bank_id="bank_alpha",
    coordinator_url="https://api.cfi-platform.org",
    use_cuda=True
)

# Join federated learning round with Differential Privacy shield
client.connect_and_train(
    epsilon=${codeEpsilon},
    delta=1e-5,
    local_epochs=3,
    batch_size=64
)`;
    } else if (codeLang === 'curl') {
      return `curl -X POST "https://api.cfi-platform.org/api/v1/coordinator/handshake" \\
  -H "Content-Type: application/json" \\
  -d '{
    "bank_id": "bank_alpha",
    "pytorch_version": "2.2.0+cu121",
    "python_version": "3.12.1",
    "hardware_type": "cuda",
    "ram_gb": 64.0,
    "epsilon_budget": ${codeEpsilon}
  }'`;
    } else if (codeLang === 'typescript') {
      return `import { CFIEventStream } from '@cfi/sdk';

const stream = new CFIEventStream({
  endpoint: 'wss://api.cfi-platform.org/ws/training',
  bankId: 'bank_alpha',
  epsilonLimit: ${codeEpsilon}
});

stream.on('round_complete', (metrics) => {
  console.log(\`Round \${metrics.round_id} Accuracy: \${metrics.accuracy}%\`);
});`;
    } else {
      return `apiVersion: apps/v1
kind: Deployment
metadata:
  name: cfi-bank-node-alpha
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: bank-node
        image: cfi/bank-node:v2.4.0
        env:
        - name: EPSILON_BUDGET
          value: "${codeEpsilon}"
        - name: HARDWARE_ACCEL
          value: "CUDA"`;
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans selection:bg-indigo-500 selection:text-white">
      {/* ── SECTION 1: GLASSMORPHIC TOP NAVBAR ─────────────────── */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-slate-950/80 border-b border-slate-800/80">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-emerald-500 p-0.5 shadow-lg shadow-indigo-500/20">
              <div className="h-full w-full bg-slate-950 rounded-[10px] flex items-center justify-center font-black text-white text-lg">
                🛡️
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-lg tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                  CF-Intelligence
                </span>
                <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono text-[10px] font-bold border border-indigo-500/30">
                  v2.4.0
                </span>
              </div>
              <p className="text-[11px] text-slate-400 hidden sm:block">
                Federated Cross-Bank Anti-Fraud Platform
              </p>
            </div>
          </div>

          {/* Desktop Navigation Anchors */}
          <nav className="hidden md:flex items-center gap-8 text-xs font-semibold text-slate-300">
            <a href="#product" className="hover:text-indigo-400 transition-colors">Product</a>
            <a href="#platform" className="hover:text-indigo-400 transition-colors">Platform</a>
            <a href="#architecture" className="hover:text-indigo-400 transition-colors">Architecture</a>
            <a href="#security" className="hover:text-indigo-400 transition-colors">Security</a>
            <a href="#api" className="hover:text-indigo-400 transition-colors">API</a>
            <a href="#docs" className="hover:text-indigo-400 transition-colors">Documentation</a>
          </nav>

          {/* Actions & Launch Demo */}
          <div className="flex items-center gap-4">
            <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-semibold">
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              Quorum Active (3/3 Synced)
            </div>

            <button
              onClick={() => navigate('/dashboard')}
              className="group relative inline-flex items-center justify-center p-0.5 overflow-hidden rounded-xl font-bold text-xs shadow-lg shadow-indigo-500/25 transition-all duration-300 hover:scale-105"
            >
              <span className="absolute inset-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 animate-gradient" />
              <span className="relative px-5 py-2.5 rounded-[10px] bg-slate-950 text-white flex items-center gap-2 group-hover:bg-slate-900 transition-all">
                <span>Launch Demo</span>
                <ArrowRightIcon />
              </span>
            </button>
          </div>
        </div>
      </header>

      {/* ── SECTION 2: HERO SECTION WITH ANIMATED 3-BANK NODE FLOW ──── */}
      <section className="relative pt-12 pb-24 px-6 max-w-7xl mx-auto overflow-hidden">
        {/* Glowing Background Radial Orbs */}
        <div className="absolute top-10 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-gradient-to-tr from-indigo-500/10 via-purple-500/10 to-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="text-center max-w-4xl mx-auto space-y-6">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-semibold"
          >
            <span>✨ Privacy-Preserving Collaborative Machine Learning</span>
            <span className="text-indigo-500">•</span>
            <span className="text-emerald-400 font-bold">GDPR & EU AI Act Compliant</span>
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="text-4xl sm:text-6xl font-black tracking-tight leading-tight text-slate-100"
          >
            Detect Cross-Bank Fraud Rings Without Sharing Raw Customer Data
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="text-base sm:text-lg text-slate-400 max-w-3xl mx-auto leading-relaxed"
          >
            Leverage Heterogeneous Federated Learning, Secure Enclaves, and Streaming Graph Neural Networks to stop multi-institutional money laundering and fraud syndicates in real time.
          </motion.p>
        </div>

        {/* Live Telemetry HUD Bar */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.3 }}
          className="mt-10 max-w-4xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-4 p-4 rounded-2xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl"
        >
          <div className="text-center">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Active FL Round</span>
            <div className="text-xl font-black text-indigo-400 font-mono mt-0.5">#{flRound}</div>
          </div>
          <div className="text-center">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Global GNN Accuracy</span>
            <div className="text-xl font-black text-emerald-400 font-mono mt-0.5">{accuracy}%</div>
          </div>
          <div className="text-center">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Privacy Budget (ε)</span>
            <div className="text-xl font-black text-purple-400 font-mono mt-0.5">0.50 (Strict)</div>
          </div>
          <div className="text-center">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Stream Bandwidth</span>
            <div className="text-xl font-black text-blue-400 font-mono mt-0.5">1.4 GB/s</div>
          </div>
        </motion.div>

        {/* ── INTERACTIVE 3-BANK NODE CANVAS FLOW ──────────────── */}
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.6, delay: 0.4 }}
          className="mt-10 rounded-3xl border border-indigo-500/20 bg-gradient-to-b from-slate-900/90 via-slate-950/95 to-slate-950 p-6 sm:p-10 shadow-2xl relative overflow-hidden"
        >
          {/* Controls Bar */}
          <div className="flex flex-wrap items-center justify-between gap-4 pb-6 border-b border-slate-800/80">
            <div className="flex items-center gap-3">
              <span className="h-3 w-3 rounded-full bg-emerald-400 animate-ping" />
              <h3 className="text-sm sm:text-base font-bold text-slate-200">
                Interactive Cross-Bank Consortium Streaming Topology
              </h3>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleInjectFraudWave}
                className="px-3.5 py-1.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs font-bold transition-all flex items-center gap-1.5 shadow-sm"
              >
                <span>Inject Fraud Wave 🚨</span>
                {fraudWaveCount > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-rose-500 text-white text-[10px]">
                    +{fraudWaveCount}
                  </span>
                )}
              </button>

              <button
                onClick={() => setIsDpShieldActive(!isDpShieldActive)}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all flex items-center gap-1.5 border ${
                  isDpShieldActive
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                    : 'bg-slate-800 text-slate-400 border-slate-700'
                }`}
              >
                <LockIcon />
                <span>DP Shield: {isDpShieldActive ? 'ON (ε=0.5)' : 'OFF'}</span>
              </button>

              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold border border-slate-700 transition-all"
              >
                {isPlaying ? 'Pause ⏸️' : 'Play ▶️'}
              </button>
            </div>
          </div>

          {/* SVG Canvas & Node Nodes Diagram */}
          <div className="relative w-full h-[420px] mt-6 flex items-center justify-center">
            {/* SVG Path Bezier Connectors with Animated Motion Particles */}
            <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 800 400" preserveAspectRatio="none">
              {/* Path 1: Bank Alpha -> Aggregator */}
              <path id="path-alpha" d="M 160,100 Q 400,120 400,200" fill="none" stroke="#6366f1" strokeWidth="2" strokeDasharray="4 4" opacity="0.4" />
              {/* Path 2: Bank Beta -> Aggregator */}
              <path id="path-beta" d="M 640,100 Q 400,120 400,200" fill="none" stroke="#a855f7" strokeWidth="2" strokeDasharray="4 4" opacity="0.4" />
              {/* Path 3: Bank Gamma -> Aggregator */}
              <path id="path-gamma" d="M 400,340 Q 400,270 400,200" fill="none" stroke="#10b981" strokeWidth="2" strokeDasharray="4 4" opacity="0.4" />

              {/* Animated Motion Particles Along Path */}
              {isPlaying && (
                <>
                  {/* Alpha Particles */}
                  <circle r="5" fill="#6366f1">
                    <animateMotion dur="2.5s" repeatCount="indefinite">
                      <mpath href="#path-alpha" />
                    </animateMotion>
                  </circle>

                  {/* Beta Particles */}
                  <circle r="5" fill="#a855f7">
                    <animateMotion dur="3.0s" repeatCount="indefinite">
                      <mpath href="#path-beta" />
                    </animateMotion>
                  </circle>

                  {/* Gamma Particles */}
                  <circle r="5" fill="#10b981">
                    <animateMotion dur="2.2s" repeatCount="indefinite">
                      <mpath href="#path-gamma" />
                    </animateMotion>
                  </circle>
                </>
              )}
            </svg>

            {/* Bank Alpha Node (Top Left) */}
            <motion.div
              whileHover={{ scale: 1.05 }}
              onClick={() => setSelectedBank('bank_alpha')}
              className={`absolute top-4 left-6 sm:left-16 p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-200 w-56 ${
                selectedBank === 'bank_alpha'
                  ? 'bg-indigo-500/20 border-indigo-500 shadow-lg shadow-indigo-500/20'
                  : 'bg-slate-900/80 border-slate-800 hover:border-indigo-500/50'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-bold text-indigo-400">BANK ALPHA</span>
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
              </div>
              <div className="text-sm font-bold text-slate-100">Santander UK Node</div>
              <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-1.5">
                <ZapIcon />
                <span>NVIDIA RTX 4090 (64GB)</span>
              </div>
            </motion.div>

            {/* Bank Beta Node (Top Right) */}
            <motion.div
              whileHover={{ scale: 1.05 }}
              onClick={() => setSelectedBank('bank_beta')}
              className={`absolute top-4 right-6 sm:right-16 p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-200 w-56 ${
                selectedBank === 'bank_beta'
                  ? 'bg-purple-500/20 border-purple-500 shadow-lg shadow-purple-500/20'
                  : 'bg-slate-900/80 border-slate-800 hover:border-purple-500/50'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-bold text-purple-400">BANK BETA</span>
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
              </div>
              <div className="text-sm font-bold text-slate-100">BNP Paribas Node</div>
              <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-1.5">
                <ZapIcon />
                <span>NVIDIA A100 (32GB)</span>
              </div>
            </motion.div>

            {/* Central Aggregator Core (Center) */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 p-6 rounded-3xl bg-slate-950 border-2 border-indigo-500 shadow-2xl shadow-indigo-500/30 text-center w-64 z-10">
              <div className="relative inline-block">
                <div className="h-12 w-12 rounded-2xl bg-indigo-500/20 border border-indigo-500/40 text-indigo-400 flex items-center justify-center mx-auto text-2xl mb-2">
                  🛰️
                </div>
                {isDpShieldActive && (
                  <div className="absolute -top-1 -right-1 h-5 w-5 rounded-full bg-emerald-500 text-slate-950 flex items-center justify-center text-[10px] font-black">
                    🔒
                  </div>
                )}
              </div>
              <h4 className="text-sm font-extrabold text-slate-100">CFI Aggregator Core</h4>
              <p className="text-[11px] text-slate-400 mt-0.5">Secure Enclave (Intel SGX)</p>
              <div className="mt-3 py-1 px-2.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 font-mono text-[10px] font-bold">
                FedAvg + Differential Privacy
              </div>
            </div>

            {/* Bank Gamma Node (Bottom Center) */}
            <motion.div
              whileHover={{ scale: 1.05 }}
              onClick={() => setSelectedBank('bank_gamma')}
              className={`absolute bottom-4 left-1/2 -translate-x-1/2 p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-200 w-56 ${
                selectedBank === 'bank_gamma'
                  ? 'bg-emerald-500/20 border-emerald-500 shadow-lg shadow-emerald-500/20'
                  : 'bg-slate-900/80 border-slate-800 hover:border-emerald-500/50'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-mono font-bold text-emerald-400">BANK GAMMA</span>
                <span className="h-2 w-2 rounded-full bg-emerald-400" />
              </div>
              <div className="text-sm font-bold text-slate-100">Deutsche Bank Node</div>
              <div className="text-[11px] text-slate-400 mt-1 flex items-center gap-1.5">
                <CpuIcon />
                <span>CPU Cluster (16GB RAM)</span>
              </div>
            </motion.div>
          </div>
        </motion.div>
      </section>

      {/* ── SECTION 3: PRODUCT & FEATURE SHOWCASE (6 GLASS CARDS) ───── */}
      <section id="product" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto space-y-4 mb-14">
          <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold">
            PRODUCT CAPABILITIES
          </span>
          <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
            Enterprise-Grade Fraud Intelligence Infrastructure
          </h2>
          <p className="text-sm text-slate-400">
            Built from the ground up to satisfy stringent banking privacy regulations while delivering multi-bank fraud ring detection.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {/* Card 1 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-indigo-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <ActivityIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">Heterogeneous FL Engine</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Dynamically negotiates batch sizes and local training epochs based on node VRAM and CPU specs to eliminate stragglers in global aggregation rounds.
            </p>
          </div>

          {/* Card 2 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-purple-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-purple-500/10 border border-purple-500/20 text-purple-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <ZapIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">Streaming GNN Subgraphs</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Detects multi-bank account collusion and synthetic identity rings in real time using Temporal Graph Attention Networks (TGAT).
            </p>
          </div>

          {/* Card 3 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-emerald-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <LockIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">Homomorphic Encryption & TEE</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Paillier homomorphic encryption combined with Intel SGX enclaves ensures model weight updates are computed in zero-trust isolation.
            </p>
          </div>

          {/* Card 4 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-blue-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <ShieldIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">EU AI Act Compliance Suite</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Automated bias detection, model sign-off certificates, and immutable audit logs ensuring compliance with EU AI Act and GDPR Article 25.
            </p>
          </div>

          {/* Card 5 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-amber-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <ServerIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">ISO 20022 Stream Parser</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Native parsing of financial XML transaction standards (`pacs.008`, `camt.053`) for instant feature engineering across banking hosts.
            </p>
          </div>

          {/* Card 6 */}
          <div className="glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/40 hover:border-rose-500/40 transition-all group">
            <div className="h-12 w-12 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 flex items-center justify-center mb-4 group-hover:scale-110 transition-transform">
              <CpuIcon />
            </div>
            <h3 className="text-lg font-bold text-slate-100 mb-2">Zero-Trust ABAC Policies</h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Fine-grained Attribute-Based Access Control enforcing role clearance, shift hours, and transaction threshold policies.
            </p>
          </div>
        </div>
      </section>

      {/* ── SECTION 4: PLATFORM LIVE PREVIEW STUDIO (TABBED VIEWPORT) ── */}
      <section id="platform" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="glass-card border border-slate-800/80 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
            <div>
              <span className="text-xs font-mono font-bold text-indigo-400 uppercase tracking-wider">
                LIVE INTERACTIVE PREVIEW
              </span>
              <h2 className="text-2xl font-extrabold text-slate-100 mt-1">
                Explore Platform Workspaces
              </h2>
            </div>

            {/* Viewport Tabs */}
            <div className="flex items-center gap-2 bg-slate-950 p-1.5 rounded-2xl border border-slate-800">
              {[
                { id: 'operations', label: 'Live FL Operations' },
                { id: 'cases', label: 'AML Investigation' },
                { id: 'graph', label: 'Graph Intelligence' },
                { id: 'privacy', label: 'Privacy Defense' },
              ].map((t) => (
                <button
                  key={t.id}
                  onClick={() => setActiveTab(t.id as any)}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                    activeTab === t.id
                      ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/30'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Interactive Mini-Dashboard Component Renderers */}
          <div className="mt-8">
            <AnimatePresence mode="wait">
              {activeTab === 'operations' && (
                <motion.div
                  key="operations"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="space-y-6"
                >
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800">
                      <span className="text-xs text-slate-400">Current Accuracy</span>
                      <div className="text-2xl font-black text-emerald-400 font-mono mt-1">98.42%</div>
                    </div>
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800">
                      <span className="text-xs text-slate-400">Target Loss</span>
                      <div className="text-2xl font-black text-indigo-400 font-mono mt-1">0.0412</div>
                    </div>
                    <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800">
                      <span className="text-xs text-slate-400">Active Consortium</span>
                      <div className="text-2xl font-black text-purple-400 font-mono mt-1">3 Bank Nodes</div>
                    </div>
                  </div>

                  <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="h-3 w-3 rounded-full bg-emerald-400 animate-ping" />
                      <span className="text-xs text-slate-200 font-bold">Round #42 Execution Stream</span>
                    </div>
                    <button
                      onClick={() => navigate('/dashboard')}
                      className="text-xs font-bold text-indigo-400 hover:text-indigo-300 flex items-center gap-1"
                    >
                      <span>Open Full Operational Console</span>
                      <ArrowRightIcon />
                    </button>
                  </div>
                </motion.div>
              )}

              {activeTab === 'cases' && (
                <motion.div
                  key="cases"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="space-y-4"
                >
                  <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800 flex items-center justify-between">
                    <div>
                      <span className="px-2.5 py-0.5 rounded-full bg-rose-500/20 text-rose-400 text-[10px] font-bold border border-rose-500/30">
                        PRIORITY P1_CRITICAL
                      </span>
                      <h4 className="text-sm font-bold text-slate-100 mt-1">
                        High-Risk Activity: Device Sharing & Crypto Outflow
                      </h4>
                      <p className="text-xs text-slate-400">Target Account: ACCT_9941 (Bank Alpha ➔ Bank Beta)</p>
                    </div>
                    <button
                      onClick={() => navigate('/cases')}
                      className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold shadow-sm"
                    >
                      Investigate Case →
                    </button>
                  </div>
                </motion.div>
              )}

              {activeTab === 'graph' && (
                <motion.div
                  key="graph"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="p-8 rounded-xl bg-slate-950/80 border border-slate-800 text-center space-y-4"
                >
                  <div className="h-16 w-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center mx-auto text-3xl">
                    🕸️
                  </div>
                  <h4 className="text-base font-bold text-slate-100">Cross-Bank Collusion Graph Intelligence</h4>
                  <p className="text-xs text-slate-400 max-w-md mx-auto">
                    Visualize multi-institutional money mule networks, shared device fingerprints, and rapid transfer rings.
                  </p>
                  <button
                    onClick={() => navigate('/graph')}
                    className="px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-bold shadow-md"
                  >
                    Open Interactive Graph Canvas →
                  </button>
                </motion.div>
              )}

              {activeTab === 'privacy' && (
                <motion.div
                  key="privacy"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="grid grid-cols-1 sm:grid-cols-2 gap-4"
                >
                  <div className="p-4 rounded-xl bg-slate-950/80 border border-emerald-500/30">
                    <span className="text-xs font-bold text-emerald-400 uppercase">MIA Audit Score</span>
                    <div className="text-xl font-black text-slate-100 font-mono mt-1">4.2% Leakage (Safe Tier)</div>
                  </div>
                  <div className="p-4 rounded-xl bg-slate-950/80 border border-indigo-500/30">
                    <span className="text-xs font-bold text-indigo-400 uppercase">DLG Gradient Protection</span>
                    <div className="text-xl font-black text-slate-100 font-mono mt-1">100% Noise Masked</div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </section>

      {/* ── SECTION 5: ARCHITECTURE & DATA JOURNEY PIPELINE ────────── */}
      <section id="architecture" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto space-y-4 mb-14">
          <span className="px-3.5 py-1.5 rounded-full bg-purple-500/10 border border-purple-500/20 text-purple-400 text-xs font-bold">
            SYSTEM ARCHITECTURE
          </span>
          <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
            End-to-End Privacy-Preserving Data Pipeline
          </h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          {[
            { step: '01', title: 'Intake Engine', desc: 'ISO 20022 XML intake & local GNN subgraph extraction' },
            { step: '02', title: 'DP Noise Layer', desc: 'Gaussian noise injection with epsilon differential privacy budget' },
            { step: '03', title: 'TEE Enclave', desc: 'Homomorphic Paillier weight aggregation in Intel SGX' },
            { step: '04', title: 'Model Registry', desc: 'Shadow evaluation & cryptographic model sign-off certificates' },
            { step: '05', title: 'SIEM Export', desc: 'Automated SAR exports & Splunk/Elastic SIEM compliance logging' },
          ].map((item, idx) => (
            <div key={idx} className="p-5 rounded-2xl bg-slate-900/60 border border-slate-800 relative space-y-2">
              <span className="text-xs font-mono font-bold text-indigo-400">{item.step}</span>
              <h4 className="text-sm font-bold text-slate-100">{item.title}</h4>
              <p className="text-xs text-slate-400 leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── SECTION 6: SECURITY & ZERO-TRUST COMPLIANCE MATRIX ────── */}
      <section id="security" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="glass-card border border-emerald-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
            <div>
              <span className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-wider">
                SECURITY & ABAC ENGINE
              </span>
              <h2 className="text-2xl font-extrabold text-slate-100 mt-1">
                Zero-Trust Attribute-Based Access Control Simulator
              </h2>
            </div>

            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-xs font-mono text-emerald-400 font-bold">Policy Engine Online</span>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 mt-8">
            {/* ABAC Form */}
            <div className="lg:col-span-6 space-y-4">
              <div>
                <label className="text-xs font-bold text-slate-300 uppercase tracking-wider">User Role</label>
                <select
                  value={abacRole}
                  onChange={(e) => setAbacRole(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200 focus:border-indigo-500"
                >
                  <option value="compliance_officer">Compliance Officer (Clearance 4)</option>
                  <option value="junior_analyst">Junior Analyst (Clearance 1)</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase tracking-wider">Resource Sensitivity</label>
                <select
                  value={abacSensitivity}
                  onChange={(e) => setAbacSensitivity(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200 focus:border-indigo-500"
                >
                  <option value="tier_1">Tier 1 - Confidential SAR Data</option>
                  <option value="tier_3">Tier 3 - Public Model Metrics</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase tracking-wider">Requested Action</label>
                <select
                  value={abacAction}
                  onChange={(e) => setAbacAction(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200 focus:border-indigo-500"
                >
                  <option value="export_sar">Export Suspicious Activity Report (SAR)</option>
                  <option value="view_metrics">View Aggregated Dashboard Metrics</option>
                </select>
              </div>
            </div>

            {/* ABAC Evaluation Output Result */}
            <div className="lg:col-span-6 flex flex-col justify-between p-6 rounded-2xl bg-slate-950 border border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">Policy Decision Output</span>
                <div className="mt-4">
                  {isAbacAllowed ? (
                    <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 font-extrabold text-sm">
                      <CheckIcon /> ACCESS GRANTED (Rule: Compliance_Override_V2)
                    </div>
                  ) : (
                    <div className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400 font-extrabold text-sm">
                      <span>⚠️ ACCESS DENIED (Insufficient Role Clearance)</span>
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-6 pt-4 border-t border-slate-800/80 font-mono text-[11px] text-slate-500">
                Merkle Hash Proof: <span className="text-indigo-400">0x8f92a10b42c1...7e91</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── SECTION 7: DEVELOPER MULTI-LANGUAGE CODE STUDIO ───────── */}
      <section id="api" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
            <div>
              <div className="flex items-center gap-2">
                <CodeIcon />
                <h2 className="text-2xl font-extrabold text-slate-100">Developer API & SDK Studio</h2>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Integrate bank nodes in minutes using our Python SDK, REST endpoints, or Kubernetes Helm charts.
              </p>
            </div>

            {/* Language Tabs */}
            <div className="flex items-center gap-2 bg-slate-950 p-1.5 rounded-2xl border border-slate-800">
              {[
                { id: 'python', label: 'Python SDK' },
                { id: 'curl', label: 'cURL REST' },
                { id: 'typescript', label: 'TypeScript WS' },
                { id: 'k8s', label: 'Kubernetes Helm' },
              ].map((lang) => (
                <button
                  key={lang.id}
                  onClick={() => setCodeLang(lang.id as any)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all ${
                    codeLang === lang.id
                      ? 'bg-indigo-600 text-white shadow-md'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {lang.label}
                </button>
              ))}
            </div>
          </div>

          {/* Interactive Epsilon Slider Controls */}
          <div className="mt-6 flex items-center gap-4">
            <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">Epsilon Budget:</span>
            <input
              type="range"
              min="0.1"
              max="2.0"
              step="0.1"
              value={codeEpsilon}
              onChange={(e) => setCodeEpsilon(Number(e.target.value))}
              className="w-48 h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
            />
            <span className="text-xs font-mono font-bold text-indigo-400">{codeEpsilon.toFixed(1)}</span>
          </div>

          {/* Code Viewer Container */}
          <div className="mt-4 relative rounded-2xl bg-slate-950 border border-slate-800 p-6 overflow-x-auto font-mono text-xs text-indigo-300">
            <button
              onClick={() => handleCopyCode(getCodeSnippet())}
              className="absolute top-4 right-4 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-bold border border-slate-700 transition-all"
            >
              {copiedCode ? 'Copied! ✓' : 'Copy Code'}
            </button>
            <pre className="whitespace-pre">{getCodeSnippet()}</pre>
          </div>
        </div>
      </section>

      {/* ── SECTION 8: DOCUMENTATION & QUICKSTART ─────────────────── */}
      <section id="docs" className="py-16 px-6 max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto space-y-4 mb-14">
          <span className="px-3.5 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold">
            QUICKSTART GUIDE
          </span>
          <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
            Deploy a Bank Consortium Node in 3 Steps
          </h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
            <span className="text-xs font-mono font-bold text-indigo-400">STEP 01</span>
            <h3 className="text-sm font-bold text-slate-100">Install Python SDK</h3>
            <div className="p-3 rounded-xl bg-slate-950 font-mono text-xs text-emerald-400 border border-slate-800">
              pip install cfi-intelligence
            </div>
          </div>

          <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
            <span className="text-xs font-mono font-bold text-indigo-400">STEP 02</span>
            <h3 className="text-sm font-bold text-slate-100">Initialize Bank Config</h3>
            <div className="p-3 rounded-xl bg-slate-950 font-mono text-xs text-emerald-400 border border-slate-800">
              cfi-node init --bank bank_alpha
            </div>
          </div>

          <div className="p-6 rounded-2xl bg-slate-900/60 border border-slate-800 space-y-3">
            <span className="text-xs font-mono font-bold text-indigo-400">STEP 03</span>
            <h3 className="text-sm font-bold text-slate-100">Connect to Coordinator</h3>
            <div className="p-3 rounded-xl bg-slate-950 font-mono text-xs text-emerald-400 border border-slate-800">
              cfi-node connect --cuda
            </div>
          </div>
        </div>
      </section>

      {/* ── SECTION 9: ENTERPRISE FOOTER & LAUNCH DEMO BANNER ──────── */}
      <footer className="border-t border-slate-800 bg-slate-950/90 py-16 px-6">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8">
          <div className="space-y-2 text-center md:text-left">
            <div className="flex items-center justify-center md:justify-start gap-2">
              <span className="text-xl">🛡️</span>
              <span className="font-extrabold text-lg text-slate-100">Collaborative Fraud Intelligence Platform</span>
            </div>
            <p className="text-xs text-slate-400">
              Privacy-Preserving Cross-Bank Federated Learning Infrastructure.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <button
              onClick={() => navigate('/dashboard')}
              className="px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 text-white font-bold text-xs shadow-lg shadow-indigo-500/20 hover:scale-105 transition-all"
            >
              Launch Live Platform Demo →
            </button>
          </div>
        </div>

        <div className="max-w-7xl mx-auto mt-12 pt-6 border-t border-slate-900 text-center text-xs text-slate-600">
          © {new Date().getFullYear()} CFI Platform. All Rights Reserved. Built for EU AI Act & GDPR Article 25 Compliance.
        </div>
      </footer>
    </div>
  );
}
