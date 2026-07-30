import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence, useInView } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// ── TYPES ───────────────────────────────────────────────────────────────────
export interface BankInfoDetail {
  id: string; name: string; ticker: string; location: string;
  hardware: string; ram: string; pytorch: string; latency: string; xmlLogs: string[];
}
interface Module { id: string; name: string; category: string; purpose: string; algorithm: string; inputs: string; outputs: string; tech: string; }
interface ArchNode { id: string; label: string; description: string; tech: string[]; responsibilities: string[]; protocols: string[]; }

// ── DATA ────────────────────────────────────────────────────────────────────
const BANK_NODES: Record<string, BankInfoDetail> = {
  jpmorgan: { id: 'jpmorgan', name: 'JPMorgan Chase & Co.', ticker: 'NYSE: JPM', location: 'New York Data Center, US (Node #01)', hardware: 'NVIDIA DGX H100 (8× Tensor Core GPUs)', ram: '128 GB Host RAM', pytorch: '2.2.0+cu121', latency: '1.2 ms', xmlLogs: ['<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>', 'GATConv (in=512, heads=8, out=256) embedding computed in 14.2ms.', 'DP Gaussian noise σ=0.031 injected. ε=0.50, δ=1e-5. HSM-signed: 0x99F1.'] },
  hsbc: { id: 'hsbc', name: 'HSBC Holdings plc', ticker: 'LSE: HSBA', location: 'London Canary Wharf, UK (Node #02)', hardware: 'Dell PowerEdge R760 (4× NVIDIA A100 GPUs)', ram: '64 GB Host RAM', pytorch: '2.1.2+cu118', latency: '1.8 ms', xmlLogs: ['<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt><Id>HSBC-GBP-8812</Id></Stmt></BkToCstmrStmt></Document>', 'Subgraph feature extraction complete. 12,840 nodes, 47,291 edges ingested.', 'Paillier ciphertext [[W_hsbc]] emitted. Ready for secure aggregation.'] },
  deutsche: { id: 'deutsche', name: 'Deutsche Bank AG', ticker: 'XETRA: DBK', location: 'Frankfurt, DE (Node #03)', hardware: 'Intel Xeon Platinum (CPU Monolith)', ram: '32 GB Host RAM', pytorch: '2.1.2+cpu', latency: '2.9 ms', xmlLogs: ['<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>DBK-2026-7734</MsgId></GrpHdr></FIToFICstmrCdtTrf></Document>', 'Heterogeneous negotiator: batch_size=32, grad_accum_steps=2.', 'CPU straggler quenched. Round latency 342ms.'] },
  sgx: { id: 'sgx', name: 'Intel SGX Hardware TEE Enclave', ticker: 'HARDWARE TEE', location: 'Consortium Secure Vault Node', hardware: 'Intel SGX Enclave v2 (Hardware Isolation)', ram: '256 GB Enclave Page Cache (EPC)', pytorch: 'C++ Native LibTorch Enclave Runtime', latency: '0.2 ms', xmlLogs: ['Remote Attestation Quote verified by Intel IAS. Status: SUCCESS.', 'Homomorphic Sum: [[W_global]] = Σ([[W_jpm]], [[W_hsbc]], [[W_db]])', 'DP noise injected (ε=0.50, δ=1e-5). [[W_global]] published to consortium.'] },
};

const PLATFORM_MODULES: Module[] = [
  { id: 'fl-engine', name: 'Federated Learning Engine', category: 'Core Engine', purpose: 'Orchestrates distributed training rounds with FedAvg and straggler tolerance.', algorithm: 'FedAvg, FedProx, async SGD', inputs: 'Local gradients from bank nodes', outputs: 'Aggregated global model weights', tech: 'PyTorch 2.2, gRPC, Protocol Buffers' },
  { id: 'dp-engine', name: 'Differential Privacy Engine', category: 'Privacy Layer', purpose: 'Applies Gaussian noise to local gradients providing (ε, δ)-DP guarantees.', algorithm: 'Gaussian Mechanism, RDP Accountant', inputs: 'Raw local gradients, sensitivity bounds', outputs: 'Noise-perturbed gradient tensors', tech: 'Opacus 1.4, Rényi DP' },
  { id: 'secure-agg', name: 'Secure Aggregation', category: 'Cryptography', purpose: 'Paillier homomorphic encryption ensures no raw gradient is seen by the coordinator.', algorithm: 'Paillier HE, Shamir Secret Sharing', inputs: 'Encrypted gradient ciphertexts', outputs: 'Homomorphically aggregated ciphertext', tech: 'Intel SGX Enclave v2, python-phe' },
  { id: 'bft-agg', name: 'Byzantine-Robust Aggregation', category: 'BFT Defense', purpose: 'Neutralises gradient poisoning attacks from compromised bank nodes.', algorithm: 'Krum, Trimmed Mean, Flame', inputs: 'Gradient updates from all nodes', outputs: 'Byzantine-filtered aggregated gradient', tech: 'Custom PyTorch, scikit-learn' },
  { id: 'gnn-engine', name: 'Graph Neural Network Engine', category: 'ML Runtime', purpose: 'Builds transaction graphs from ISO 20022 feeds and computes GNN embeddings.', algorithm: 'GAT, GraphSAGE', inputs: 'ISO 20022 XML pacs.008 / camt.053', outputs: '512-dim node embeddings, risk scores', tech: 'PyTorch Geometric 2.6, DGL' },
  { id: 'risk-engine', name: 'Risk Scoring Engine', category: 'Intelligence', purpose: 'XGBoost + GNN ensemble with SHAP interpretability per transaction.', algorithm: 'XGBoost + GNN ensemble, SHAP, LIME', inputs: 'GNN embeddings, transaction features', outputs: 'Risk score [0-1], SHAP attributions', tech: 'XGBoost 2.0, SHAP, Platt Calibration' },
  { id: 'telemetry', name: 'Telemetry & Monitoring', category: 'Observability', purpose: 'Streams real-time FL round metrics and node health to the dashboard.', algorithm: 'EWMA smoothing, anomaly detection', inputs: 'Node heartbeats, round metrics', outputs: 'Prometheus metrics, InfluxDB time-series', tech: 'Prometheus, Grafana, OpenTelemetry' },
  { id: 'bank-connector', name: 'Bank Connector Framework', category: 'Integration', purpose: 'Normalises ISO 20022 XML streams from heterogeneous bank core banking systems.', algorithm: 'Schema validation, normalisation', inputs: 'Raw pacs.008, camt.053 XML streams', outputs: 'Normalised transaction graph tensors', tech: 'Apache Kafka, lxml, xmlschema' },
];

const ARCH_NODES: ArchNode[] = [
  { id: 'frontend', label: 'React Dashboard', description: 'Real-time monitoring dashboard and fraud investigation interface.', tech: ['React 18', 'Vite', 'Framer Motion', 'Recharts'], responsibilities: ['FL round monitoring', 'Graph visualisation', 'Risk investigation', 'Node inspection'], protocols: ['WebSocket', 'REST'] },
  { id: 'api-gw', label: 'API Gateway', description: 'Authenticated entrypoint for all dashboard, bank connector, and external tool traffic.', tech: ['FastAPI', 'JWT', 'TLS 1.3'], responsibilities: ['Auth enforcement', 'Rate limiting', 'Routing', 'Request logging'], protocols: ['HTTPS', 'WebSocket'] },
  { id: 'coordinator', label: 'FL Coordinator', description: 'Central orchestrator managing training rounds, node selection, and aggregation scheduling.', tech: ['Python 3.11', 'gRPC', 'Celery', 'Redis'], responsibilities: ['Round scheduling', 'Node selection', 'Timeout handling', 'Model versioning'], protocols: ['gRPC', 'Protocol Buffers'] },
  { id: 'fl-engine', label: 'FL Engine', description: 'Implements federated optimisation algorithms and gradient aggregation.', tech: ['PyTorch 2.2', 'NumPy', 'SciPy'], responsibilities: ['FedAvg', 'FedProx', 'Straggler tolerance', 'Model validation'], protocols: ['Shared Memory', 'gRPC'] },
  { id: 'privacy-engine', label: 'Privacy Engine', description: 'Applies Gaussian noise and manages cumulative (ε, δ) privacy budget.', tech: ['Opacus 1.4', 'python-phe', 'RDP'], responsibilities: ['Gradient clipping', 'Noise injection', 'Budget tracking', 'ε audit'], protocols: ['Internal API'] },
  { id: 'sgx-enclave', label: 'SGX Enclave', description: 'Hardware-isolated trusted execution environment for homomorphic aggregation.', tech: ['Intel SGX SDK', 'LibTorch (C++)', 'OpenEnclave'], responsibilities: ['HE aggregation', 'Remote attestation', 'Enclave verification', 'Key management'], protocols: ['Enclave-to-Enclave', 'ECALL/OCALL'] },
  { id: 'graph-engine', label: 'Graph Engine', description: 'PyTorch Geometric runtime for transaction graph construction and GNN inference.', tech: ['PyTorch Geometric 2.6', 'DGL', 'NetworkX'], responsibilities: ['Graph construction', 'GATConv inference', 'Subgraph sampling', 'Embedding store'], protocols: ['Internal gRPC'] },
  { id: 'bank-nodes', label: 'Bank Node Agents', description: 'Lightweight Python agents deployed at each bank performing local training.', tech: ['PyTorch', 'gRPC client', 'HSM SDK'], responsibilities: ['Local training', 'DP noise injection', 'Gradient encryption', 'Heartbeat'], protocols: ['gRPC (mTLS)', 'ISO 20022 XML'] },
];

// ── ANIMATED NETWORK SVG ─────────────────────────────────────────────────────
function ConsortiumNetworkSVG({ compact = false }: { compact?: boolean }) {
  const banks = [
    { id: 'JPM', x: 40,  y: 50,  color: '#6366f1' },
    { id: 'HSB', x: 140, y: 20,  color: '#8b5cf6' },
    { id: 'DBK', x: 140, y: 90,  color: '#06b6d4' },
    { id: 'SGX', x: 90,  y: 55,  color: '#10b981' },
  ];
  const edges = [[0,3],[1,3],[2,3]];
  const [tick, setTick] = useState(0);
  useEffect(() => { const t = setInterval(() => setTick(p => p+1), 80); return () => clearInterval(t); }, []);
  const vb = compact ? '0 0 180 110' : '0 0 180 110';

  return (
    <svg viewBox={vb} className="w-full h-full" style={{ overflow:'visible' }}>
      <defs>
        {banks.map(b => (
          <radialGradient key={b.id} id={`grd2-${b.id}`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={b.color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={b.color} stopOpacity="0" />
          </radialGradient>
        ))}
      </defs>
      {edges.map(([a,b],i) => {
        const from = banks[a]!; const to = banks[b]!;
        const off = ((tick*2)+i*40)%120;
        return (
          <g key={`e${i}`}>
            <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="#1a1a30" strokeWidth="1.5"/>
            <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke={from.color} strokeWidth="1" strokeDasharray="10 50" strokeDashoffset={-off} strokeOpacity="0.7"/>
          </g>
        );
      })}
      {banks.map((b,i) => (
        <g key={b.id}>
          <circle cx={b.x} cy={b.y} r="18" fill={`url(#grd2-${b.id})`}/>
          <circle cx={b.x} cy={b.y} r="8" fill="#09091a" stroke={b.color} strokeWidth="1.5"/>
          <circle cx={b.x} cy={b.y} r={8+((tick+i*30)%60)/10} fill="none" stroke={b.color} strokeWidth="0.6" strokeOpacity={1-((tick+i*30)%60)/60}/>
          <circle cx={b.x} cy={b.y} r="2.5" fill={b.color}/>
          <text x={b.x} y={b.y+20} textAnchor="middle" fontSize="6.5" fill={b.color} fontFamily="monospace" fontWeight="700">{b.id}</text>
        </g>
      ))}
      {edges.map(([a,b],i) => {
        const from = banks[a]!; const to = banks[b]!;
        const t = ((tick+i*25)%80)/80;
        const px = from.x+(to.x-from.x)*t, py = from.y+(to.y-from.y)*t;
        const color = banks[i]?.color ?? '#6366f1';
        return <circle key={`pkt${i}`} cx={px} cy={py} r="2" fill={color} opacity={t>0.08&&t<0.92?0.9:0}/>;
      })}
    </svg>
  );
}

// ── DASHBOARD PREVIEW ────────────────────────────────────────────────────────
function DashboardPreview({ flRound, accuracy }: { flRound: number; accuracy: number }) {
  const [alertTick, setAlertTick] = useState(0);
  useEffect(() => { const t = setInterval(() => setAlertTick(p => p+1), 3200); return () => clearInterval(t); }, []);

  const alerts = [
    { id: 'JPM-9912', bank: 'JPM', amount: '1,450,000', score: 0.94, high: true,  time: '00:01s' },
    { id: 'HSBC-8812', bank: 'HSB', amount: '87,400',   score: 0.31, high: false, time: '00:08s' },
    { id: 'DBK-7734', bank: 'DBK', amount: '650,000',   score: 0.87, high: true,  time: '00:14s' },
    { id: 'JPM-9913', bank: 'JPM', amount: '12,200',    score: 0.11, high: false, time: '00:22s' },
  ];

  const sideIcons = ['⬡', '◎', '△', '▦'];

  return (
    <div className="relative rounded-xl overflow-hidden border border-white/10 shadow-[0_0_80px_rgba(99,102,241,0.18)] bg-[#09091a]">
      {/* Browser chrome */}
      <div className="flex items-center gap-2.5 px-3 py-2.5 bg-[#0d0d1f] border-b border-white/7">
        <div className="flex gap-1.5 shrink-0">
          <div className="w-2.5 h-2.5 rounded-full bg-rose-500/50" />
          <div className="w-2.5 h-2.5 rounded-full bg-amber-500/50" />
          <div className="w-2.5 h-2.5 rounded-full bg-emerald-500/60" />
        </div>
        <div className="flex-1 mx-2 px-3 py-0.5 rounded-md bg-white/5 text-[10px] font-mono text-slate-600 flex items-center gap-2">
          <span className="text-slate-700">🔒</span>
          cfi-platform.com/dashboard
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[9px] font-mono text-emerald-400">LIVE</span>
        </div>
      </div>

      {/* App body */}
      <div className="flex">
        {/* Sidebar */}
        <div className="w-9 shrink-0 border-r border-white/5 bg-[#0b0b1c] py-3 flex flex-col items-center gap-3">
          <div className="w-5 h-5 rounded-md bg-indigo-600 flex items-center justify-center text-[8px] text-white font-bold">C</div>
          {sideIcons.map((ic, i) => (
            <div key={i} className={`text-[11px] ${i === 0 ? 'text-indigo-400' : 'text-slate-700 hover:text-slate-500'} cursor-pointer`}>{ic}</div>
          ))}
        </div>

        {/* Main content */}
        <div className="flex-1 p-3 space-y-2.5 min-w-0">
          {/* Stats row */}
          <div className="grid grid-cols-2 gap-2">
            {[
              { label: 'Active FL Round',  value: `#${flRound}`,  color: 'text-indigo-400',  bg: 'bg-indigo-600/8' },
              { label: 'Global Accuracy',  value: `${accuracy}%`, color: 'text-emerald-400', bg: 'bg-emerald-600/8' },
              { label: 'Privacy Budget',   value: 'ε = 0.50',     color: 'text-purple-400',  bg: 'bg-purple-600/8' },
              { label: 'Stream Speed',     value: '1.4 GB/s',     color: 'text-cyan-400',    bg: 'bg-cyan-600/8' },
            ].map(stat => (
              <div key={stat.label} className={`p-2 rounded-lg ${stat.bg} border border-white/5`}>
                <div className="text-[8px] font-mono text-slate-600 uppercase tracking-wide">{stat.label}</div>
                <motion.div
                  key={stat.value}
                  initial={{ opacity: 0.7 }}
                  animate={{ opacity: 1 }}
                  className={`text-sm font-bold font-mono ${stat.color}`}
                >
                  {stat.value}
                </motion.div>
              </div>
            ))}
          </div>

          {/* Risk alert feed */}
          <div className="rounded-lg border border-white/6 overflow-hidden">
            <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-white/5 bg-white/3">
              <span className="text-[9px] font-mono text-slate-600 uppercase tracking-wider">Risk Alert Feed</span>
              <span className="flex items-center gap-1 text-[9px] font-mono text-emerald-500">
                <span className="h-1 w-1 rounded-full bg-emerald-500 animate-ping" />streaming
              </span>
            </div>
            <div className="divide-y divide-white/4">
              {alerts.map((a, i) => (
                <motion.div
                  key={`${a.id}-${alertTick}`}
                  initial={i === 0 ? { opacity: 0, y: -4 } : { opacity: 1 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className="flex items-center gap-2 px-2.5 py-1.5 hover:bg-white/3 transition-colors"
                >
                  <span className={`w-1 h-4 rounded-full shrink-0 ${a.high ? 'bg-rose-500' : 'bg-emerald-500/60'}`} />
                  <span className="text-[9px] font-mono text-slate-500 w-14 shrink-0">{a.bank} TX</span>
                  <span className="text-[9px] font-mono text-slate-400 flex-1 truncate">${a.amount}</span>
                  <span className={`text-[9px] font-mono font-bold shrink-0 ${a.high ? 'text-rose-400' : 'text-emerald-400'}`}>
                    {a.score.toFixed(2)}
                  </span>
                  <span className={`px-1 py-px rounded text-[8px] font-mono shrink-0 ${a.high ? 'bg-rose-600/20 text-rose-400' : 'bg-emerald-600/15 text-emerald-500'}`}>
                    {a.high ? 'HIGH' : 'LOW'}
                  </span>
                </motion.div>
              ))}
            </div>
          </div>

          {/* Network topology */}
          <div className="rounded-lg border border-white/6 bg-white/2 p-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[9px] font-mono text-slate-600 uppercase tracking-wider">Consortium Network</span>
              <span className="text-[9px] font-mono text-emerald-500">3/3 synced</span>
            </div>
            <div className="h-[100px]">
              <ConsortiumNetworkSVG compact />
            </div>
          </div>
        </div>
      </div>

      {/* Glow overlay border */}
      <div className="absolute inset-0 rounded-xl pointer-events-none ring-1 ring-indigo-500/15" />
    </div>
  );
}

// ── FADE WRAPPER ─────────────────────────────────────────────────────────────
function FadeSection({ children, className = '', delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: '-60px' });
  return (
    <motion.div ref={ref} initial={{ opacity: 0, y: 20 }} animate={inView ? { opacity: 1, y: 0 } : {}} transition={{ duration: 0.5, delay, ease: 'easeOut' }} className={className}>
      {children}
    </motion.div>
  );
}

// ── ICONS ────────────────────────────────────────────────────────────────────
const BrandLogo = ({ className = 'w-9 h-9' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 44 44" fill="none">
    <rect width="44" height="44" rx="10" fill="#1e1b4b"/>
    <rect x="1" y="1" width="42" height="42" rx="9" stroke="#4f46e5" strokeWidth="1.5" strokeOpacity="0.6"/>
    <path d="M14 22L20 28L30 16" stroke="#818cf8" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
    <circle cx="22" cy="22" r="14" stroke="#4f46e5" strokeWidth="1" strokeDasharray="3 3" strokeOpacity="0.5"/>
  </svg>
);
const MenuIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>
  </svg>
);
const ArrowRight = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
  </svg>
);


// ── MAIN ─────────────────────────────────────────────────────────────────────
export default function LandingPage() {
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);
  const [activeModule, setActiveModule] = useState<Module | null>(PLATFORM_MODULES[0] ?? null);
  const [activeArchNode, setActiveArchNode] = useState<ArchNode | null>(ARCH_NODES[0] ?? null);
  const [activeApiTab, setActiveApiTab] = useState<'curl' | 'python' | 'ts'>('curl');
  const [activePrivacyTab, setActivePrivacyTab] = useState<'flow' | 'threat' | 'compliance'>('flow');
  const [flRound, setFlRound] = useState(47);
  const [accuracy, setAccuracy] = useState(94.2);

  useEffect(() => {
    const t = setInterval(() => {
      setFlRound(p => p + 1);
      setAccuracy(parseFloat((94.0 + Math.random() * 0.4).toFixed(1)));
    }, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="min-h-screen bg-[#070711] text-slate-300 font-sans antialiased selection:bg-indigo-600 selection:text-white overflow-x-hidden">

      {/* Ambient background */}
      <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="absolute -top-60 -left-40 w-[600px] h-[600px] rounded-full bg-indigo-900/20 blur-[120px]"/>
        <div className="absolute top-1/3 -right-60 w-[500px] h-[500px] rounded-full bg-purple-900/15 blur-[120px]"/>
        <div className="absolute bottom-0 left-1/3 w-[400px] h-[400px] rounded-full bg-cyan-900/10 blur-[120px]"/>
        <div className="absolute inset-0" style={{ backgroundImage:'linear-gradient(rgba(99,102,241,0.035) 1px,transparent 1px),linear-gradient(90deg,rgba(99,102,241,0.035) 1px,transparent 1px)', backgroundSize:'48px 48px' }}/>
      </div>

      <div className="relative z-10">

        {/* ── HEADER ──────────────────────────────────────────────── */}
        <header className="sticky top-0 z-50 h-14 flex items-center border-b border-white/5 bg-[#070711]/85 backdrop-blur-xl">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <BrandLogo/>
              <div className="flex items-baseline gap-2">
                <span className="font-semibold text-sm text-slate-100 tracking-tight">CF-Intelligence</span>
                <span className="text-xs text-slate-500 font-mono">v2.4.0</span>
              </div>
            </div>

            <nav aria-label="primary" className="hidden lg:flex items-center gap-6 text-[13px] font-medium text-slate-500">
              {['Overview','Problem','Workflow','Capabilities','Platform','Architecture','Security','API & Docs'].map(label => (
                <a key={label}
                  href={`#${label==='API & Docs'?'api':label==='Architecture'?'architecture':label.toLowerCase().replace(/ & /g,'-').replace(/ /g,'-')}`}
                  className="hover:text-slate-200 transition-colors">{label}</a>
              ))}
            </nav>

            <div className="flex items-center gap-3">
              <div className="hidden xl:flex items-center gap-1.5 text-[11px] font-mono text-emerald-500">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"/>
                3/3 nodes synced
              </div>
              <button
                aria-label="Toggle Navigation Menu"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-slate-100 cursor-pointer"
              ><MenuIcon/></button>
              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[13px] font-semibold text-white bg-indigo-600 hover:bg-indigo-500 transition-all cursor-pointer shadow-[0_0_20px_rgba(99,102,241,0.35)] hover:shadow-[0_0_30px_rgba(99,102,241,0.5)]"
              >Launch Demo <ArrowRight/></button>
            </div>
          </div>
        </header>

        {/* ── MOBILE DRAWER ───────────────────────────────────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.nav initial={{opacity:0,y:-8}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-8}}
              className="lg:hidden border-b border-white/5 bg-[#070711]/95 backdrop-blur-xl px-4 py-4 space-y-1 z-40">
              {[
                {label:'Overview (3D Architecture)',    href:'#hero'},
                {label:'The Problem & Solution',        href:'#problem-solution'},
                {label:'Streaming GNN Collusion Simulator', href:'#how-it-works'},
                {label:'Privacy Engine & Capabilities', href:'#product'},
                {label:'Deployment Blueprint Wizard',   href:'#platform'},
                {label:'System Architecture',           href:'#architecture'},
                {label:'Security & Attack Defense Lab', href:'#security'},
                {label:'API & Docs',                    href:'#api'},
              ].map(link => (
                <a key={link.label} href={link.href} onClick={() => setIsMobileMenuOpen(false)}
                  className="block px-3 py-2 text-[13px] text-slate-400 hover:text-slate-100 hover:bg-white/5 rounded-lg transition-colors">
                  {link.label}
                </a>
              ))}
              <div className="pt-2 border-t border-white/5">
                <button onClick={() => { setIsMobileMenuOpen(false); navigate('/dashboard'); }}
                  className="w-full py-2 text-[13px] font-medium text-white bg-indigo-600 rounded-lg">
                  Launch Live Platform Demo
                </button>
              </div>
            </motion.nav>
          )}
        </AnimatePresence>

        {/* ══════════════════════════════════════════════════════════
            SECTION 1 — HERO / OVERVIEW
        ══════════════════════════════════════════════════════════ */}
        <section id="hero" className="relative py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 lg:gap-16 items-center">

            {/* Left column */}
            <motion.div initial={{opacity:0,y:30}} animate={{opacity:1,y:0}} transition={{duration:0.6}} className="space-y-8">
              <div>
                <motion.div initial={{opacity:0,x:-10}} animate={{opacity:1,x:0}} transition={{delay:0.1}}
                  className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-600/10 border border-indigo-500/20 text-indigo-400 text-xs font-mono mb-5">
                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 animate-pulse"/>
                  Enterprise Fraud Intelligence — Production
                </motion.div>
                <h1 className="text-4xl sm:text-5xl font-bold leading-tight tracking-tight mb-5">
                  <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">
                    Privacy-Preserving
                  </span>
                  <br/>
                  <span className="text-slate-100">Cross-Bank Fraud</span>
                  <br/>
                  <span className="text-slate-100">Detection</span>
                </h1>
                <p className="text-slate-400 text-[15px] leading-relaxed max-w-lg">
                  A federated machine learning platform that enables financial institutions to collaboratively detect fraud across institutions — without ever sharing raw transaction data.
                </p>
              </div>

              {/* Key metrics */}
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: 'Detection Rate',   value: '94.2%',   sub: 'vs. 42% isolated', color: 'from-emerald-500 to-teal-500' },
                  { label: 'Privacy Guarantee', value: 'ε = 0.50', sub: '(ε, δ)-DP bounded', color: 'from-indigo-500 to-purple-500' },
                  { label: 'False Positive',    value: '↓ 5×',    sub: '31% → 6.1% FPR',  color: 'from-cyan-500 to-blue-500' },
                ].map((m, i) => (
                  <motion.div key={m.label} initial={{opacity:0,y:12}} animate={{opacity:1,y:0}} transition={{delay:0.2+i*0.07}}
                    className="p-3 rounded-xl bg-white/3 border border-white/7 hover:border-white/15 transition-colors">
                    <div className={`text-xl font-bold font-mono bg-gradient-to-r ${m.color} bg-clip-text text-transparent`}>{m.value}</div>
                    <div className="text-[10px] font-mono text-slate-500 mt-0.5">{m.label}</div>
                    <div className="text-[9px] font-mono text-slate-700 mt-0.5">{m.sub}</div>
                  </motion.div>
                ))}
              </div>

              {/* Tech badges */}
              <div className="flex flex-wrap gap-2">
                {['Federated Learning','GATConv · PyG 2.6','Paillier HE + Intel SGX','Opacus ε-DP','ISO 20022 / pacs.008','FinCEN SAR'].map(tag => (
                  <span key={tag} className="px-2.5 py-1 rounded-full text-[10px] font-mono text-slate-500 border border-white/8 bg-white/3 hover:border-white/20 transition-colors">{tag}</span>
                ))}
              </div>

              {/* CTAs */}
              <div className="flex flex-wrap items-center gap-3">
                <button onClick={() => navigate('/dashboard')}
                  className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-[13px] font-semibold text-white transition-all cursor-pointer shadow-[0_0_30px_rgba(99,102,241,0.4)] hover:shadow-[0_0_40px_rgba(99,102,241,0.55)]">
                  Launch Live Platform Demo <ArrowRight/>
                </button>
                <a href="#architecture"
                  className="flex items-center gap-2 px-6 py-2.5 rounded-xl border border-white/10 hover:border-white/25 text-[13px] font-medium text-slate-300 hover:text-slate-100 transition-all">
                  System Design
                </a>
              </div>
            </motion.div>

            {/* Right column — SaaS dashboard preview */}
            <motion.div initial={{opacity:0,y:30}} animate={{opacity:1,y:0}} transition={{duration:0.6,delay:0.15}}>
              <DashboardPreview flRound={flRound} accuracy={accuracy}/>
            </motion.div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 2 — PROBLEM (#problem-solution)  [compact]
        ══════════════════════════════════════════════════════════ */}
        <section id="problem-solution" className="py-12 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection>
            <div className="flex flex-col sm:flex-row items-center justify-between gap-6">
              <div className="max-w-sm">
                <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest mb-2">The Problem</div>
                <h2 className="text-xl font-bold text-slate-100 leading-snug">Money laundering spans institutions. Existing AML is institution-local.</h2>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-center px-5 py-4 rounded-xl bg-rose-600/5 border border-rose-500/15">
                  <div className="text-3xl font-bold font-mono text-rose-400">42%</div>
                  <div className="text-[10px] font-mono text-slate-500 mt-1">Isolated Detection</div>
                </div>
                <div className="text-slate-600 font-mono text-lg">→</div>
                <div className="text-center px-5 py-4 rounded-xl bg-emerald-600/5 border border-emerald-500/20 shadow-[0_0_30px_rgba(16,185,129,0.06)]">
                  <div className="text-3xl font-bold font-mono text-emerald-400">94.2%</div>
                  <div className="text-[10px] font-mono text-slate-500 mt-1">Federated Detection</div>
                </div>
              </div>
              <div className="max-w-xs text-[12px] text-slate-500 font-mono leading-relaxed">
                Zero raw transaction data leaves any institution. (ε=0.50, δ=1e-5)-DP guaranteed per gradient update.
              </div>
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 3 — WORKFLOW (#how-it-works)  [compact pipeline]
        ══════════════════════════════════════════════════════════ */}
        <section id="how-it-works" className="py-12 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection>
            <div className="flex items-center justify-between mb-5">
              <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest">Federated Pipeline — 8 Stages</div>
              <a href="#platform" className="text-[11px] font-mono text-slate-500 hover:text-slate-300 transition-colors">See full docs →</a>
            </div>
            <div className="relative flex items-start gap-0 overflow-x-auto pb-2">
              {['ISO 20022\nIngestion','Local GNN\nTraining','Differential\nPrivacy','Secure\nAggregation','Byzantine\nFilter','Global\nUpdate','Risk\nScoring','SAR\nExport'].map((label, i) => (
                <div key={i} className="flex items-center shrink-0">
                  <div className="flex flex-col items-center">
                    <div className={`w-8 h-8 rounded-full border flex items-center justify-center text-[10px] font-mono font-bold shrink-0 ${
                      i < 4 ? 'border-indigo-500/40 bg-indigo-600/10 text-indigo-400' : 'border-white/10 bg-white/4 text-slate-500'
                    }`}>{i+1}</div>
                    <div className="text-[9px] font-mono text-slate-600 text-center mt-1.5 leading-tight whitespace-pre-line w-14">{label}</div>
                  </div>
                  {i < 7 && <div className="w-6 h-px bg-white/8 shrink-0 mt-[-10px]"/>}
                </div>
              ))}
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 4 — CAPABILITIES (#product)  [feature grid]
        ══════════════════════════════════════════════════════════ */}
        <section id="product" className="py-12 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection>
            <div className="flex items-center justify-between mb-5">
              <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest">Platform Modules</div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {PLATFORM_MODULES.map(mod => (
                <motion.div key={mod.id} whileHover={{y:-2}} onClick={() => setActiveModule(mod)}
                  className={`p-3 rounded-xl border cursor-pointer transition-all ${
                    activeModule?.id===mod.id
                      ? 'bg-indigo-600/10 border-indigo-500/30 shadow-[0_0_18px_rgba(99,102,241,0.12)]'
                      : 'bg-white/3 border-white/7 hover:border-white/15'
                  }`}>
                  <div className="text-[10px] font-mono text-slate-700 mb-0.5">{mod.category}</div>
                  <div className="text-[12px] font-medium text-slate-300">{mod.name}</div>
                  <div className="text-[10px] text-slate-600 mt-1 leading-snug">{mod.algorithm.split(',')[0]}</div>
                </motion.div>
              ))}
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 5 — PLATFORM / NODE INSPECTOR (#platform)
        ══════════════════════════════════════════════════════════ */}
        <section id="platform" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection className="max-w-2xl mb-10">
            <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest mb-3">Consortium Node Registry</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Active Bank Node Inspector</h2>
            <p className="text-slate-500 text-[14px] leading-relaxed">
              Click any node to inspect hardware configuration, PyTorch runtime, and live ISO 20022 stream activity.
            </p>
          </FadeSection>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {Object.values(BANK_NODES).map((bank, i) => (
              <motion.div key={bank.id}
                initial={{opacity:0,y:16}} animate={{opacity:1,y:0}} transition={{delay:i*0.08}}
                whileHover={{y:-4,transition:{duration:0.2}}}
                onClick={() => setActiveBankDrawer(bank)}
                className="p-5 rounded-xl bg-white/3 border border-white/7 hover:border-indigo-500/40 hover:shadow-[0_0_30px_rgba(99,102,241,0.1)] cursor-pointer transition-all group space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"/>
                    <span className="text-[10px] font-mono text-emerald-500">ACTIVE</span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-600">{bank.latency}</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-slate-300 group-hover:text-slate-100 transition-colors">{bank.name}</div>
                  <div className="text-[10px] font-mono text-slate-700 mt-0.5">{bank.ticker}</div>
                </div>
                <div className="text-[10px] font-mono text-slate-700 border-t border-white/5 pt-2.5 leading-relaxed">{bank.hardware}</div>
              </motion.div>
            ))}
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 6 — ARCHITECTURE (#architecture)
        ══════════════════════════════════════════════════════════ */}
        <section id="architecture" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection className="max-w-2xl mb-10">
            <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest mb-3">System Design</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Service Layer Map</h2>
            <p className="text-slate-500 text-[14px] leading-relaxed">Click any service node to inspect responsibilities, protocols, and technology stack.</p>
          </FadeSection>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 gap-2">
              {ARCH_NODES.map(node => (
                <motion.button key={node.id} onClick={() => setActiveArchNode(node)} whileHover={{scale:1.02}}
                  className={`p-4 rounded-lg border text-left transition-all ${
                    activeArchNode?.id===node.id
                      ? 'bg-indigo-600/10 border-indigo-500/35 shadow-[0_0_18px_rgba(99,102,241,0.15)]'
                      : 'bg-white/3 border-white/7 hover:border-white/15'
                  }`}>
                  <div className={`text-[12px] font-medium mb-0.5 ${activeArchNode?.id===node.id?'text-indigo-300':'text-slate-400'}`}>{node.label}</div>
                  <div className="text-[10px] font-mono text-slate-700 leading-snug">{node.tech.slice(0,2).join(' · ')}</div>
                </motion.button>
              ))}
            </div>

            {activeArchNode && (
              <AnimatePresence mode="wait">
                <motion.div key={activeArchNode.id} initial={{opacity:0,x:12}} animate={{opacity:1,x:0}} exit={{opacity:0,x:-12}} transition={{duration:0.2}}
                  className="lg:col-span-7 p-6 rounded-xl bg-white/3 border border-white/8 space-y-5">
                  <div className="border-b border-white/5 pb-4">
                    <div className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider mb-0.5">Service Component</div>
                    <h3 className="text-base font-semibold text-slate-100">{activeArchNode.label}</h3>
                    <p className="text-[13px] text-slate-400 mt-2 leading-relaxed">{activeArchNode.description}</p>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-[11px] font-mono">
                    {[{label:'Responsibilities',items:activeArchNode.responsibilities,dot:'text-indigo-500'},{label:'Protocols',items:activeArchNode.protocols,dot:'text-purple-500'},{label:'Technology',items:activeArchNode.tech,dot:'text-cyan-500'}].map(col=>(
                      <div key={col.label}>
                        <div className="text-[9px] text-slate-600 uppercase tracking-wider mb-2">{col.label}</div>
                        <ul className="space-y-1.5">{col.items.map(it=>(
                          <li key={it} className="text-slate-300 flex items-center gap-1.5"><span className={col.dot}>·</span>{it}</li>
                        ))}</ul>
                      </div>
                    ))}
                  </div>
                </motion.div>
              </AnimatePresence>
            )}
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 7 — SECURITY (#security)
        ══════════════════════════════════════════════════════════ */}
        <section id="security" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <FadeSection className="max-w-2xl mb-8">
            <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest mb-3">Security Model</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Privacy & Trust Boundary Model</h2>
          </FadeSection>

          <div className="flex gap-1 p-1 bg-white/3 border border-white/7 rounded-xl w-fit mb-8">
            {[{id:'flow',label:'Data Flow'},{id:'threat',label:'Threat Model'},{id:'compliance',label:'Compliance'}].map(tab=>(
              <button key={tab.id} onClick={()=>setActivePrivacyTab(tab.id as 'flow'|'threat'|'compliance')}
                className={`px-4 py-1.5 text-[12px] font-medium rounded-lg transition-all ${activePrivacyTab===tab.id?'bg-indigo-600 text-white shadow-[0_0_14px_rgba(99,102,241,0.4)]':'text-slate-500 hover:text-slate-300'}`}>
                {tab.label}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            <motion.div key={activePrivacyTab} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-8}} transition={{duration:0.2}}>
              {activePrivacyTab==='flow' && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                  {[
                    {title:'Inside Bank Perimeter',items:['Raw transaction records','Customer PII','Account balances','Local GNN graph'],note:'← Never transmitted externally',noteColor:'text-rose-500'},
                    {title:'Transmitted (DP-Noised)',items:['DP-noised gradient tensors','Paillier ciphertexts','Round participation flags','HSM-signed commitments'],note:'← (ε=0.50, δ=1e-5)-DP bounded',noteColor:'text-emerald-500'},
                    {title:'SGX Enclave (HW Isolated)',items:['HE aggregation only','IAS attestation verified','Encrypted memory pages','No external network access'],note:'← Hardware trust boundary',noteColor:'text-purple-500'},
                  ].map(col=>(
                    <div key={col.title} className="p-5 rounded-xl bg-white/3 border border-white/7 space-y-3">
                      <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">{col.title}</div>
                      <div className="space-y-2 font-mono text-[11px] text-slate-400">{col.items.map(item=>(
                        <div key={item} className="p-2 rounded bg-white/3 border border-white/5">{item}</div>
                      ))}</div>
                      <div className={`text-[10px] font-mono ${col.noteColor}`}>{col.note}</div>
                    </div>
                  ))}
                </div>
              )}
              {activePrivacyTab==='threat' && (
                <div className="space-y-3">
                  {[
                    {threat:'Gradient Inversion Attack',    mitigation:'Gaussian DP noise (σ calibrated to ε=0.50) makes gradient inversion computationally infeasible. Clipping bound C=1.0.'},
                    {threat:'Byzantine Gradient Poisoning', mitigation:'Krum + Trimmed Mean Byzantine-robust aggregation neutralises up to f < n/2 compromised nodes per round.'},
                    {threat:'Coordinator Compromise',       mitigation:'Intel SGX TEE handles aggregation. Coordinator sees only ciphertexts; plaintext gradients never accessible outside enclave.'},
                    {threat:'Membership Inference',         mitigation:'RDP accountant tracks cumulative budget. Training halted when ε threshold exceeded. Per-sample clipping prevents memorisation.'},
                    {threat:'Model Extraction',             mitigation:'Global model distributed only to authenticated bank nodes over mTLS. No external inference endpoint exposed.'},
                  ].map(row=>(
                    <motion.div key={row.threat} whileHover={{x:3}}
                      className="flex items-start gap-4 p-4 rounded-lg bg-white/3 border border-white/7 hover:border-white/12 transition-all">
                      <div className="w-44 shrink-0">
                        <div className="text-[11px] font-medium text-rose-400">{row.threat}</div>
                        <div className="text-[10px] font-mono text-emerald-500 mt-0.5">Mitigated</div>
                      </div>
                      <div className="text-[11px] text-slate-400 font-mono leading-relaxed">{row.mitigation}</div>
                    </motion.div>
                  ))}
                </div>
              )}
              {activePrivacyTab==='compliance' && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  {[
                    {standard:'GDPR Article 25',        status:'Privacy by Design',detail:'DP guarantees built into training pipeline. No PII ever leaves institution.'},
                    {standard:'FinCEN SAR Regulation',  status:'Compliant',        detail:'Automated SAR XML generation with cryptographic sign-off and audit trail.'},
                    {standard:'EU AML Directive 6AMLD', status:'Compliant',        detail:'Cross-border pattern detection via federated architecture without data transfer.'},
                    {standard:'NIST SP 800-188',        status:'Aligned',          detail:'De-identification through differential privacy following NIST de-ID standard.'},
                    {standard:'ISO 20022',              status:'Native',           detail:'pacs.008 and camt.053 message formats parsed natively by Bank Connector.'},
                    {standard:'SOC 2 Type II',          status:'In Progress',      detail:'Audit logging, access controls, and telemetry pipeline support SOC 2 criteria.'},
                  ].map(row=>(
                    <div key={row.standard} className="p-4 rounded-lg bg-white/3 border border-white/7 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-mono text-slate-300">{row.standard}</span>
                        <span className="text-[10px] font-mono text-emerald-400">{row.status}</span>
                      </div>
                      <p className="text-[11px] text-slate-500 leading-relaxed">{row.detail}</p>
                    </div>
                  ))}
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 8 — API & DOCS (#api)
        ══════════════════════════════════════════════════════════ */}
        <section id="api" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/5">
          <div id="docs">
            <FadeSection className="max-w-2xl mb-8">
              <div className="text-[11px] font-mono text-indigo-500 uppercase tracking-widest mb-3">Developer API</div>
              <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">REST API & Connector SDK</h2>
              <p className="text-slate-500 text-[14px] leading-relaxed">REST API for coordinator control, WebSocket stream for real-time telemetry, Bank Connector SDK for onboarding.</p>
            </FadeSection>

            <div className="flex gap-1 p-1 bg-white/3 border border-white/7 rounded-xl w-fit mb-5">
              {[{id:'curl',label:'cURL'},{id:'python',label:'Python'},{id:'ts',label:'TypeScript'}].map(tab=>(
                <button key={tab.id} onClick={()=>setActiveApiTab(tab.id as 'curl'|'python'|'ts')}
                  className={`px-4 py-1.5 text-[12px] font-mono rounded-lg transition-all ${activeApiTab===tab.id?'bg-slate-700 text-slate-100':'text-slate-500 hover:text-slate-300'}`}>
                  {tab.label}
                </button>
              ))}
            </div>

            <AnimatePresence mode="wait">
              <motion.div key={activeApiTab} initial={{opacity:0,y:6}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:0.2}}
                className="rounded-xl bg-[#08081a] border border-white/7 p-5 overflow-x-auto mb-6">
                <pre className="text-[12px] font-mono text-indigo-200/80 leading-relaxed whitespace-pre">
                  {activeApiTab==='curl'&&`# Trigger a new federated learning round
curl -X POST https://api.cfi-platform.com/v1/rounds \\
  -H "Authorization: Bearer cfi_api_key_991823" \\
  -H "Content-Type: application/json" \\
  -d '{
    "consortium_id": "cfi-prod-001",
    "node_ids": ["jpmorgan-01", "hsbc-02", "deutsche-03"],
    "privacy_config": {"epsilon": 0.50, "delta": 1e-5},
    "aggregation": "krum"
  }'`}
                  {activeApiTab==='python'&&`from cfi_sdk import CFIClient

client = CFIClient(api_key="cfi_api_key_991823")

round_ = client.rounds.start(
    consortium_id="cfi-prod-001",
    node_ids=["jpmorgan-01", "hsbc-02", "deutsche-03"],
    privacy={"epsilon": 0.50, "delta": 1e-5},
)

for event in client.rounds.stream(round_.id):
    print(f"Round {event.round_id}: {event.stage} — {event.accuracy:.3f}")`}
                  {activeApiTab==='ts'&&`import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({ apiKey: 'cfi_api_key_991823' });

const round = await client.rounds.start({
  consortiumId: 'cfi-prod-001',
  nodeIds: ['jpmorgan-01', 'hsbc-02', 'deutsche-03'],
  privacyConfig: { epsilon: 0.50, delta: 1e-5 },
});

const ws = client.telemetry.subscribe(round.id);
ws.on('round.stage', (e) => console.log(e.stage, e.accuracy));`}
                </pre>
              </motion.div>
            </AnimatePresence>

            <div className="rounded-xl border border-white/7 overflow-hidden">
              <div className="px-4 py-3 bg-white/3 border-b border-white/5">
                <span className="text-[11px] font-mono text-slate-500">API Endpoints — v1</span>
              </div>
              <table className="w-full text-[11px] font-mono">
                <tbody>
                  {[
                    {method:'POST',path:'/v1/rounds',      desc:'Trigger a new federated learning round'},
                    {method:'GET', path:'/v1/rounds/:id',  desc:'Get round status and metrics'},
                    {method:'GET', path:'/v1/nodes',        desc:'List consortium bank nodes'},
                    {method:'POST',path:'/v1/connectors',  desc:'Register new bank connector'},
                    {method:'GET', path:'/v1/reports/sar', desc:'Retrieve FinCEN SAR exports'},
                    {method:'WS',  path:'/v1/telemetry',   desc:'Real-time round telemetry stream'},
                  ].map(row=>(
                    <tr key={row.path} className="border-b border-white/5 hover:bg-white/3 transition-colors">
                      <td className="px-4 py-3 w-16">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${row.method==='GET'?'bg-sky-600/10 text-sky-400':row.method==='POST'?'bg-emerald-600/10 text-emerald-400':'bg-purple-600/10 text-purple-400'}`}>{row.method}</span>
                      </td>
                      <td className="px-4 py-3 text-indigo-300">{row.path}</td>
                      <td className="px-4 py-3 text-slate-600">{row.desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* ── FOOTER ──────────────────────────────────────────────── */}
        <footer className="border-t border-white/5 py-10 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <BrandLogo className="w-7 h-7"/>
              <div>
                <div className="text-[12px] font-semibold text-slate-300">CF-Intelligence v2.4.0</div>
                <div className="text-[11px] font-mono text-slate-700">Privacy-Preserving Federated Fraud Intelligence</div>
              </div>
            </div>
            <div className="text-[11px] font-mono text-slate-700">PyTorch · Intel SGX · ISO 20022 · FinCEN SAR</div>
            <button onClick={() => navigate('/dashboard')}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-[12px] font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-all cursor-pointer shadow-[0_0_20px_rgba(99,102,241,0.35)]">
              Open Platform <ArrowRight/>
            </button>
          </div>
        </footer>

        {/* ── BANK NODE INSPECTOR DRAWER ──────────────────────────── */}
        <AnimatePresence>
          {activeBankDrawer && (
            <motion.div initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
              onClick={() => setActiveBankDrawer(null)}
              className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex justify-end">
              <motion.div
                initial={{x:'100%'}} animate={{x:0}} exit={{x:'100%'}}
                transition={{type:'spring',damping:26,stiffness:180}}
                onClick={e=>e.stopPropagation()}
                className="w-full max-w-lg bg-[#0a0a16] border-l border-white/7 p-6 overflow-y-auto space-y-5">
                <div className="flex items-start justify-between border-b border-white/5 pb-5">
                  <div>
                    <div className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider">Bank Node Inspector</div>
                    <h3 className="text-base font-semibold text-slate-100 mt-1">{activeBankDrawer.name}</h3>
                    <div className="text-[11px] font-mono text-slate-600 mt-0.5">{activeBankDrawer.location}</div>
                  </div>
                  <button onClick={() => setActiveBankDrawer(null)}
                    className="px-3 py-1.5 rounded-lg text-[11px] font-mono bg-white/5 border border-white/10 text-slate-500 hover:text-slate-100 transition-colors">
                    close
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
                  {[{k:'Ticker',v:activeBankDrawer.ticker},{k:'Latency',v:activeBankDrawer.latency},{k:'Hardware',v:activeBankDrawer.hardware},{k:'Host RAM',v:activeBankDrawer.ram},{k:'PyTorch',v:activeBankDrawer.pytorch},{k:'Status',v:'ACTIVE — Round Participant'}].map(row=>(
                    <div key={row.k} className="p-3 rounded-lg bg-white/3 border border-white/7">
                      <div className="text-slate-600 text-[9px] uppercase tracking-wider mb-0.5">{row.k}</div>
                      <div className="text-slate-200 truncate">{row.v}</div>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="text-[10px] font-mono text-slate-600 uppercase tracking-wider mb-2">ISO 20022 Stream Activity</div>
                  <div className="rounded-lg bg-[#08081a] border border-white/6 p-4 space-y-3 font-mono text-[10px] text-slate-400 overflow-x-auto">
                    {activeBankDrawer.xmlLogs.map((log,i)=>(
                      <motion.div key={i} initial={{opacity:0,x:-5}} animate={{opacity:1,x:0}} transition={{delay:i*0.1}}
                        className="flex items-start gap-2 border-b border-white/5 pb-2 last:border-0 last:pb-0">
                        <span className="text-indigo-600 shrink-0">[{String(i+1).padStart(2,'0')}]</span>
                        <span className="break-all leading-relaxed">{log}</span>
                      </motion.div>
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
