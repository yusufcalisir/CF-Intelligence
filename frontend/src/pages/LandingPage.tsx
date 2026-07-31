import { useState, useEffect, useRef, memo } from 'react';
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
  { id: 'fl-engine', name: 'Federated Learning Engine', category: 'Core Orchestration', purpose: 'Coordinates distributed GNN model optimization across banking institutions using FedAvg and FedProx with straggler mitigation.', algorithm: 'FedAvg, FedProx, Asynchronous SGD', inputs: 'Local gradient tensors from node agents', outputs: 'Aggregated global GNN model weights', tech: 'PyTorch 2.2, gRPC, Protocol Buffers' },
  { id: 'dp-engine', name: 'Differential Privacy Engine', category: 'Mathematical Privacy', purpose: 'Applies L2 gradient clipping and calibrated Gaussian noise injection to ensure mathematical (ε, δ)-differential privacy bounds.', algorithm: 'Gaussian Mechanism, RDP Accountant', inputs: 'Raw local model gradients, clipping bound C=1.0', outputs: 'Differentially private gradient tensors', tech: 'Opacus 1.4, Rényi DP' },
  { id: 'secure-agg', name: 'Secure Homomorphic Aggregation', category: 'Hardware Cryptography', purpose: 'Executes additive homomorphic aggregation of encrypted model updates inside Intel SGX hardware TEE enclaves.', algorithm: 'Paillier HE, Shamir Secret Sharing', inputs: 'Encrypted gradient ciphertexts', outputs: 'Homomorphically summed global ciphertext', tech: 'Intel SGX Enclave v2, python-phe, C++' },
  { id: 'bft-agg', name: 'Byzantine-Robust Defense Filter', category: 'Adversarial Defense', purpose: 'Detects and neutralises gradient poisoning, model replacement, and backdoor attacks from compromised bank nodes.', algorithm: 'Krum, Trimmed Mean, Flame, Cosine Dist', inputs: 'Gradient updates from all consortium nodes', outputs: 'Filtered, verified aggregated gradient', tech: 'Custom PyTorch, scikit-learn' },
  { id: 'gnn-engine', name: 'Graph Neural Network Engine', category: 'ML Topology Engine', purpose: 'Constructs dynamic multi-hop transaction graphs from ISO 20022 message streams and computes 512-dim structural embeddings.', algorithm: 'GAT (Graph Attention Network), GraphSAGE', inputs: 'ISO 20022 XML pacs.008 & camt.053 feeds', outputs: '512-dimensional node embeddings', tech: 'PyTorch Geometric 2.6, DGL' },
  { id: 'risk-engine', name: 'Calibrated Risk Scoring Engine', category: 'Fraud Intelligence', purpose: 'Combines GNN graph embeddings with tabular transaction velocity features to generate calibrated risk scores with SHAP attributions.', algorithm: 'XGBoost + GNN ensemble, SHAP, LIME', inputs: 'GNN structural embeddings, transaction features', outputs: 'Calibrated Risk Score [0-1], SHAP vectors', tech: 'XGBoost 2.0, SHAP, Platt Calibration' },
  { id: 'telemetry', name: 'Real-Time Telemetry & Monitoring', category: 'Platform Observability', purpose: 'Streams live FL training metrics, gradient norms, privacy budget consumption, and node health to the coordinator dashboard.', algorithm: 'EWMA smoothing, streaming anomaly detection', inputs: 'Node heartbeats, round gradient metrics', outputs: 'Prometheus time-series, InfluxDB metrics', tech: 'Prometheus, Grafana, OpenTelemetry' },
  { id: 'bank-connector', name: 'Bank Connector Integration Framework', category: 'Core Banking Ingestion', purpose: 'Ingests, validates XSD schemas, and normalises ISO 20022 XML financial messages from core banking ledgers.', algorithm: 'Schema validation, normalisation pipeline', inputs: 'Raw pacs.008 credit transfers & camt.053 statements', outputs: 'Normalised transaction graph tensors', tech: 'Apache Kafka, lxml, xmlschema' },
];

const ARCH_NODES: ArchNode[] = [
  { id: 'frontend', label: 'React Dashboard', description: 'Real-time monitoring dashboard and fraud investigation interface.', tech: ['React 18', 'Vite', 'Framer Motion', 'Recharts'], responsibilities: ['FL round monitoring', 'Graph visualisation', 'Risk investigation', 'Node inspection'], protocols: ['WebSocket', 'REST'] },
  { id: 'api-gw', label: 'API Gateway', description: 'Authenticated entrypoint for all dashboard, bank connector, and external tool traffic.', tech: ['FastAPI', 'JWT', 'TLS 1.3'], responsibilities: ['Auth enforcement', 'Rate limiting', 'Routing', 'Request logging'], protocols: ['HTTPS', 'WebSocket'] },
  { id: 'coordinator', label: 'FL Coordinator', description: 'Central orchestrator managing training rounds, node selection, and aggregation scheduling.', tech: ['Python 3.11', 'gRPC', 'Celery', 'Redis'], responsibilities: ['Round scheduling', 'Node selection', 'Timeout handling', 'Model versioning'], protocols: ['gRPC', 'Protocol Buffers'] },
  { id: 'fl-engine', label: 'FL Engine', description: 'Implements federated optimisation algorithms (FedAvg, FedProx) and gradient aggregation.', tech: ['PyTorch 2.2', 'NumPy', 'SciPy'], responsibilities: ['FedAvg', 'FedProx', 'Straggler tolerance', 'Model validation'], protocols: ['Shared Memory', 'gRPC'] },
  { id: 'privacy-engine', label: 'Privacy Engine', description: 'Applies calibrated Gaussian noise and manages cumulative (ε, δ) privacy budget via RDP accountant.', tech: ['Opacus 1.4', 'python-phe', 'RDP'], responsibilities: ['Gradient clipping', 'Noise injection', 'Budget tracking', 'ε audit'], protocols: ['Internal API'] },
  { id: 'sgx-enclave', label: 'SGX Enclave', description: 'Hardware-isolated trusted execution environment for homomorphic aggregation of encrypted gradients.', tech: ['Intel SGX SDK', 'LibTorch (C++)', 'OpenEnclave'], responsibilities: ['HE aggregation', 'Remote attestation', 'Enclave verification', 'Key management'], protocols: ['Enclave-to-Enclave', 'ECALL/OCALL'] },
  { id: 'graph-engine', label: 'Graph Engine', description: 'PyTorch Geometric runtime for transaction graph construction and GNN inference from ISO 20022 feeds.', tech: ['PyTorch Geometric 2.6', 'DGL', 'NetworkX'], responsibilities: ['Graph construction', 'GATConv inference', 'Subgraph sampling', 'Embedding store'], protocols: ['Internal gRPC'] },
  { id: 'bank-nodes', label: 'Bank Node Agents', description: 'Lightweight Python agents deployed at each bank performing local training on private ledger data.', tech: ['PyTorch', 'gRPC client', 'HSM SDK'], responsibilities: ['Local training', 'DP noise injection', 'Gradient encryption', 'Heartbeat'], protocols: ['gRPC (mTLS)', 'ISO 20022 XML'] },
];

const PRESENTATION_WORKFLOW = [
  {
    id: 1,
    short: 'Ingestion',
    label: 'ISO 20022 Message Stream Ingestion',
    summary: 'Core banking ledgers stream pacs.008 (credit transfers) and camt.053 (account statements) into the bank node data plane. Messages are validated against XSD schemas and converted into normalized transaction graph tensors.',
    highlights: ['Automatic XSD schema validation against ISO 20022 standards', 'Zero PII leaves local bank storage', 'Graph construction in under 15ms per transaction batch'],
    input: 'Raw XML pacs.008 / camt.053',
    output: 'Normalized local graph tensor',
    badge: 'Stage 01'
  },
  {
    id: 2,
    short: 'Local GNN',
    label: 'On-Premise Graph Attention Neural Training',
    summary: 'Local PyTorch Geometric GAT models train exclusively inside the bank perimeter. Multi-head attention layers extract 512-dimensional structural embeddings capturing multi-hop laundering topologies.',
    highlights: ['8-head Graph Attention Networks (GATConv)', 'Deep structural pattern discovery across local ledger', 'Zero raw transaction data sharing'],
    input: 'Graph node & edge tensors',
    output: 'Local GNN weight updates',
    badge: 'Stage 02'
  },
  {
    id: 3,
    short: 'Diff. Privacy',
    label: 'Differential Privacy Noise Calibration',
    summary: 'Before model updates exit bank premises, Opacus applies L2 gradient clipping (C=1.0) and injects calibrated Gaussian noise proportional to sensitivity. The Rényi DP accountant enforces mathematical (ε=0.50, δ=1e-5) privacy bounds.',
    highlights: ['Mathematically provable (ε=0.50, δ=1e-5)-DP guarantee', 'Rényi DP cumulative privacy accountant', 'Gradient clipping prevents sample memorization'],
    input: 'Raw model gradients',
    output: 'Differentially private gradient tensors',
    badge: 'Stage 03'
  },
  {
    id: 4,
    short: 'Sec. Aggregation',
    label: 'Intel SGX Hardware TEE Enclave Summation',
    summary: 'Noised gradient tensors are encrypted using Paillier additive homomorphic encryption and sent to the Intel SGX hardware enclave. The enclave performs encrypted summation without decrypting individual bank updates.',
    highlights: ['Intel SGX hardware-isolated Enclave v2', 'Paillier additive homomorphic encryption', 'Intel IAS Remote Attestation cryptographic verification'],
    input: 'Encrypted gradient ciphertexts',
    output: 'Homomorphically summed global ciphertext',
    badge: 'Stage 04'
  },
  {
    id: 5,
    short: 'BFT Defense',
    label: 'Byzantine-Robust Adversarial Poisoning Filter',
    summary: 'The FL Coordinator runs Byzantine-robust aggregation (Krum algorithm + Trimmed Mean) to evaluate incoming gradient vectors. Poisoned or malicious updates from compromised nodes are isolated and quenched.',
    highlights: ['Krum & Trimmed Mean Byzantine resilience', 'Quenches up to f < n/2 malicious node attacks', 'Gradient cosine distance anomaly detection'],
    input: 'Node gradient vectors',
    output: 'Verified, filtered global update',
    badge: 'Stage 05'
  },
  {
    id: 6,
    short: 'Global Update',
    label: 'Global Model Distribution & Registry Commit',
    summary: 'The aggregated global model weights are committed to the Model Registry with SHA-256 cryptographic audit signatures. Updated model parameters are broadcast back to all bank nodes via mutual TLS (mTLS).',
    highlights: ['FedAvg global weight synthesis', 'SHA-256 immutable audit trail', 'Secure gRPC mTLS broadcast to consortium'],
    input: 'Filtered global gradient',
    output: 'Updated global model weights',
    badge: 'Stage 06'
  },
  {
    id: 7,
    short: 'Risk Engine',
    label: 'Calibrated Risk Scoring & SHAP Explanations',
    summary: 'Global GNN embeddings are passed into an XGBoost ensemble classifier to generate calibrated risk scores [0-1]. The SHAP TreeExplainer computes human-interpretable feature attributions for fraud analysts.',
    highlights: ['Ensemble GNN + XGBoost classifier', 'SHAP TreeExplainer for full decision transparency', 'False Positive Rate reduced by 5× (31% → 6.1%)'],
    input: '512-dim GNN embeddings',
    output: 'Risk Score [0-1] + SHAP breakdown',
    badge: 'Stage 07'
  },
  {
    id: 8,
    short: 'SAR Filing',
    label: 'Automated FinCEN SAR XML Package Export',
    summary: 'Transactions crossing the risk threshold automatically trigger SAR filing packages formatted according to FinCEN XML specifications. Filings bundle SHAP evidence, transaction paths, and cryptographic signatures for direct SIEM export.',
    highlights: ['Automated FinCEN SAR XML 2.0 filing generation', 'XMLSec cryptographic digital signature sign-off', 'Direct integration with Splunk HEC & enterprise SIEM'],
    input: 'High-risk transaction flag',
    output: 'Cryptographically signed FinCEN SAR XML',
    badge: 'Stage 08'
  },
];

// Helper for smooth scrolling to sections
const scrollToSection = (id: string) => {
  const element = document.getElementById(id);
  if (element) {
    const yOffset = -70;
    const y = element.getBoundingClientRect().top + window.pageYOffset + yOffset;
    window.scrollTo({ top: y, behavior: 'smooth' });
  }
};

// ── 2026 HIGH-END FUTURISTIC CONSORTIUM NETWORK SVG ──────────────────────────
const HighEndConsortiumSVG = memo(function HighEndConsortiumSVG() {
  const [pulseTick, setPulseTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setPulseTick(p => p + 1), 60);
    return () => clearInterval(t);
  }, []);

  const banks = [
    { id: 'JPM', name: 'JPMorgan', x: 45,  y: 65,  color: '#6366f1', glow: '#818cf8' },
    { id: 'HSB', name: 'HSBC',     x: 185, y: 30,  color: '#a855f7', glow: '#c084fc' },
    { id: 'DBK', name: 'Deutsche', x: 185, y: 100, color: '#06b6d4', glow: '#38bdf8' },
    { id: 'SGX', name: 'Intel TEE',x: 115, y: 65,  color: '#10b981', glow: '#34d399' },
  ];
  const edges: [number, number][] = [[0,3],[1,3],[2,3]];

  return (
    <div className="relative w-full h-[125px] flex items-center justify-center overflow-hidden">
      <svg viewBox="0 0 230 120" className="w-full h-full max-w-full preserve-3d" preserveAspectRatio="xMidYMid meet">
        <defs>
          <filter id="glow-filter" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
          {banks.map(b => (
            <radialGradient key={b.id} id={`radial-${b.id}`} cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={b.color} stopOpacity="0.4" />
              <stop offset="100%" stopColor={b.color} stopOpacity="0" />
            </radialGradient>
          ))}
        </defs>

        {/* Dynamic Curved Connection Beams with Particles */}
        {edges.map(([a, b], i) => {
          const from = banks[a]!;
          const to = banks[b]!;
          const offset = ((pulseTick * 2.2) + i * 35) % 110;
          return (
            <g key={`beam-${i}`}>
              <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="#171738" strokeWidth="2.5" />
              <line
                x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                stroke={from.color} strokeWidth="1.5"
                strokeDasharray="8 35" strokeDashoffset={-offset} strokeOpacity="0.9"
                filter="url(#glow-filter)"
              />
            </g>
          );
        })}

        {/* Central Intel SGX Orbit Ring */}
        <circle cx={115} cy={65} r="22" fill="none" stroke="#10b981" strokeWidth="0.8" strokeDasharray="3 4" strokeOpacity="0.5" />

        {/* Nodes */}
        {banks.map((b, i) => {
          const isCentral = b.id === 'SGX';
          const r = isCentral ? 11 : 9;
          const radarRadius = r + ((pulseTick + i * 25) % 50) / 3.5;
          const opacity = 1 - ((pulseTick + i * 25) % 50) / 50;

          return (
            <g key={b.id}>
              <circle cx={b.x} cy={b.y} r={r * 2.2} fill={`url(#radial-${b.id})`} />
              <circle cx={b.x} cy={b.y} r={radarRadius} fill="none" stroke={b.glow} strokeWidth="0.8" strokeOpacity={opacity} />
              <circle cx={b.x} cy={b.y} r={r} fill="#060614" stroke={b.color} strokeWidth="1.8" filter="url(#glow-filter)" />
              <circle cx={b.x} cy={b.y} r={r * 0.35} fill={b.glow} />
              <text x={b.x} y={b.y + r + 10} textAnchor="middle" fontSize="6.5" fill="#94a3b8" fontFamily="monospace" fontWeight="700">
                {b.id}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
});

// ── 2026 DASHBOARD PREVIEW WIDGET WITH INTERACTIVE SIDEBAR NAV ───────────────
const InteractiveDashboardPreview = memo(function InteractiveDashboardPreview({ flRound, accuracy }: { flRound: number; accuracy: number }) {
  const [activeTab, setActiveTab] = useState<'home' | 'gnn' | 'privacy' | 'bft' | 'sar'>('home');
  const [alertTick, setAlertTick] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setAlertTick(p => p + 1), 3200);
    return () => clearInterval(t);
  }, []);

  const alerts = [
    { id: 'JPM-9912', bank: 'JPM', amount: '1,450,000', score: 0.94, high: true },
    { id: 'HSBC-8812', bank: 'HSB', amount: '87,400',   score: 0.31, high: false },
    { id: 'DBK-7734', bank: 'DBK', amount: '650,000',   score: 0.87, high: true },
    { id: 'JPM-9913', bank: 'JPM', amount: '12,200',    score: 0.11, high: false },
  ];

  const tabUrlPaths: Record<string, string> = {
    home: 'dashboard',
    gnn: 'dashboard/gnn-topology',
    privacy: 'dashboard/privacy-engine',
    bft: 'dashboard/bft-defense',
    sar: 'dashboard/sar-filings',
  };

  const sidebarButtons = [
    {
      id: 'home',
      label: 'Telemetry',
      title: 'FL Consortium Telemetry & Alerts',
      color: 'bg-indigo-600',
      icon: (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 3v18h18" /><path d="M18 17V9" /><path d="M13 17V5" /><path d="M8 17v-3" />
        </svg>
      )
    },
    {
      id: 'gnn',
      label: 'GNN',
      title: 'GNN Graph Node Topology',
      color: 'bg-purple-600',
      icon: (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" />
          <line x1="8.5" y1="6" x2="15.5" y2="6" /><line x1="7.5" y1="8" x2="10.5" y2="16" /><line x1="16.5" y1="8" x2="13.5" y2="16" />
        </svg>
      )
    },
    {
      id: 'privacy',
      label: 'Privacy',
      title: 'Differential Privacy & SGX Vault',
      color: 'bg-emerald-600',
      icon: (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><circle cx="12" cy="12" r="2.5" />
        </svg>
      )
    },
    {
      id: 'bft',
      label: 'BFT',
      title: 'Byzantine Attack Defense Lab',
      color: 'bg-cyan-600',
      icon: (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="9" /><path d="m9 12 2 2 4-4" />
        </svg>
      )
    },
    {
      id: 'sar',
      label: 'SAR',
      title: 'FinCEN SAR XML Generator',
      color: 'bg-amber-600',
      icon: (
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
        </svg>
      )
    },
  ];

  return (
    <div className="relative rounded-2xl overflow-hidden border border-white/10 shadow-[0_0_90px_rgba(99,102,241,0.2)] bg-[#070718] w-full max-w-full min-w-0">
      {/* Browser Chrome Header */}
      <div className="flex items-center justify-between px-2.5 sm:px-3.5 py-2.5 bg-[#0b0b20] border-b border-white/8 min-w-0">
        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0">
          <div className="flex gap-1 shrink-0">
            <div className="w-2.5 h-2.5 rounded-full bg-rose-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-amber-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
          </div>
          <div className="px-2 sm:px-3 py-0.5 rounded-md bg-white/4 text-[9px] sm:text-[10px] font-mono text-slate-400 flex items-center gap-1.5 border border-white/5 truncate">
            <span className="text-slate-600">🔒</span>
            cfi-platform.com/{tabUrlPaths[activeTab]}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[8px] sm:text-[9px] font-mono font-bold text-emerald-400 uppercase tracking-widest">LIVE</span>
        </div>
      </div>

      {/* Main Body */}
      <div className="flex min-h-[340px] sm:min-h-[360px] w-full min-w-0">
        {/* INTERACTIVE SIDEBAR NAV */}
        <div className="w-9 sm:w-11 shrink-0 border-r border-white/6 bg-[#08081b] py-2.5 flex flex-col items-center gap-3">
          {sidebarButtons.map(btn => (
            <button
              key={btn.id}
              onClick={() => setActiveTab(btn.id as any)}
              title={btn.title}
              className={`w-6 sm:w-7 h-6 sm:h-7 rounded-lg sm:rounded-xl flex items-center justify-center transition-all cursor-pointer ${
                activeTab === btn.id
                  ? `${btn.color} text-white shadow-[0_0_16px_rgba(99,102,241,0.6)] scale-105`
                  : 'text-slate-500 hover:text-slate-200 hover:bg-white/5'
              }`}
            >
              {btn.icon}
            </button>
          ))}
        </div>

        {/* DYNAMIC TAB VIEW CONTENT */}
        <div className="flex-1 p-2.5 sm:p-3.5 min-w-0 flex flex-col justify-between overflow-x-hidden">
          <AnimatePresence mode="wait">
            {activeTab === 'home' && (
              <motion.div key="home" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-2.5 min-w-0">
                <div className="grid grid-cols-2 gap-1.5 sm:gap-2">
                  <div className="p-1.5 sm:p-2 rounded-xl bg-indigo-600/10 border border-indigo-500/20">
                    <div className="text-[7.5px] sm:text-[8px] font-mono text-slate-400 uppercase">Active FL Round</div>
                    <div className="text-xs sm:text-sm font-bold font-mono text-indigo-400 mt-0.5">#{flRound}</div>
                  </div>
                  <div className="p-1.5 sm:p-2 rounded-xl bg-emerald-600/10 border border-emerald-500/20">
                    <div className="text-[7.5px] sm:text-[8px] font-mono text-slate-400 uppercase">Global Accuracy</div>
                    <div className="text-xs sm:text-sm font-bold font-mono text-emerald-400 mt-0.5">{accuracy}%</div>
                  </div>
                  <div className="p-1.5 sm:p-2 rounded-xl bg-purple-600/10 border border-purple-500/20">
                    <div className="text-[7.5px] sm:text-[8px] font-mono text-slate-400 uppercase">Privacy Budget</div>
                    <div className="text-xs sm:text-sm font-bold font-mono text-purple-400 mt-0.5">ε = 0.50</div>
                  </div>
                  <div className="p-1.5 sm:p-2 rounded-xl bg-cyan-600/10 border border-cyan-500/20">
                    <div className="text-[7.5px] sm:text-[8px] font-mono text-slate-400 uppercase">Stream Speed</div>
                    <div className="text-xs sm:text-sm font-bold font-mono text-cyan-400 mt-0.5">1.4 GB/s</div>
                  </div>
                </div>

                <div className="rounded-xl border border-white/6 overflow-hidden bg-white/2 min-w-0">
                  <div className="flex items-center justify-between px-2 py-1 border-b border-white/5 bg-white/3">
                    <span className="text-[8.5px] sm:text-[9px] font-mono text-slate-400 uppercase tracking-wider">Risk Alert Feed</span>
                    <span className="flex items-center gap-1 text-[8.5px] sm:text-[9px] font-mono text-emerald-400">
                      <span className="h-1 w-1 rounded-full bg-emerald-400 animate-ping" />streaming
                    </span>
                  </div>
                  <div className="divide-y divide-white/4">
                    {alerts.map((a) => (
                      <div key={`${a.id}-${alertTick}`} className="flex items-center gap-1.5 sm:gap-2 px-2 py-1 text-[8.5px] sm:text-[9px] font-mono min-w-0">
                        <span className={`w-1 h-3 rounded-full shrink-0 ${a.high ? 'bg-rose-500' : 'bg-emerald-500'}`} />
                        <span className="text-slate-400 shrink-0 w-10 sm:w-12">{a.bank} TX</span>
                        <span className="text-slate-300 flex-1 truncate">${a.amount}</span>
                        <span className={`font-bold shrink-0 ${a.high ? 'text-rose-400' : 'text-emerald-400'}`}>{a.score.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-white/6 bg-white/2 p-1.5 sm:p-2 min-w-0">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[8.5px] sm:text-[9px] font-mono text-slate-400 uppercase tracking-wider">Consortium Topology</span>
                    <span className="text-[8.5px] sm:text-[9px] font-mono text-emerald-400">3/3 Nodes Synced</span>
                  </div>
                  <HighEndConsortiumSVG />
                </div>
              </motion.div>
            )}

            {activeTab === 'gnn' && (
              <motion.div key="gnn" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-2.5 text-[9.5px] sm:text-[10px] font-mono min-w-0">
                <div className="p-2.5 sm:p-3 rounded-xl bg-purple-600/10 border border-purple-500/20 space-y-0.5">
                  <div className="text-purple-300 font-bold">GNN Subgraph Inspection</div>
                  <div className="text-slate-400 text-[8.5px] sm:text-[9px]">Multi-hop transaction graph attention embeddings (PyG 2.6)</div>
                </div>
                <div className="p-2.5 sm:p-3 rounded-xl bg-[#03030c] border border-white/6 space-y-1.5">
                  <div className="text-slate-500 text-[8.5px] sm:text-[9px]">GATConv Layer Attentions:</div>
                  <div className="text-purple-300 font-mono text-[8.5px] sm:text-[9px] truncate">head_0: [0.842, 0.191, 0.428, 0.912]</div>
                  <div className="text-purple-300 font-mono text-[8.5px] sm:text-[9px] truncate">head_1: [0.311, 0.774, 0.109, 0.881]</div>
                </div>
                <div className="p-2.5 sm:p-3 rounded-xl bg-white/3 border border-white/6">
                  <div className="text-slate-400 font-semibold mb-1">Detected Circular Smurfing Path:</div>
                  <div className="text-rose-400 font-bold break-all">JPM-01 ($1.45M) → HSBC-02 → SGX TEE → DBK-03</div>
                </div>
              </motion.div>
            )}

            {activeTab === 'privacy' && (
              <motion.div key="privacy" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-2.5 text-[9.5px] sm:text-[10px] font-mono min-w-0">
                <div className="p-2.5 sm:p-3 rounded-xl bg-emerald-600/10 border border-emerald-500/20 space-y-0.5">
                  <div className="text-emerald-300 font-bold">Intel SGX & (ε, δ)-DP Engine</div>
                  <div className="text-slate-400 text-[8.5px] sm:text-[9px]">Opacus RDP Accountant & Paillier Cryptography</div>
                </div>
                <div className="grid grid-cols-2 gap-1.5 sm:gap-2 text-[8.5px] sm:text-[9px]">
                  <div className="p-2 sm:p-2.5 rounded-xl bg-[#03030c] border border-white/6">
                    <div className="text-slate-500">Noise Scale (σ)</div>
                    <div className="text-emerald-400 font-bold mt-0.5 truncate">0.031 (C=1.0)</div>
                  </div>
                  <div className="p-2 sm:p-2.5 rounded-xl bg-[#03030c] border border-white/6">
                    <div className="text-slate-500">SGX Attestation</div>
                    <div className="text-emerald-400 font-bold mt-0.5 truncate">VERIFIED IAS</div>
                  </div>
                </div>
                <div className="p-2 sm:p-2.5 rounded-xl bg-[#03030c] border border-white/6 text-[8.5px] sm:text-[9px] text-slate-400 truncate">
                  Homomorphic Ciphertext: 0x99F188A2...FE01
                </div>
              </motion.div>
            )}

            {activeTab === 'bft' && (
              <motion.div key="bft" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-2.5 text-[9.5px] sm:text-[10px] font-mono min-w-0">
                <div className="p-2.5 sm:p-3 rounded-xl bg-cyan-600/10 border border-cyan-500/20 space-y-0.5">
                  <div className="text-cyan-300 font-bold">Byzantine Resilience Monitor</div>
                  <div className="text-slate-400 text-[8.5px] sm:text-[9px]">Krum & Trimmed Mean Poisoning Defense</div>
                </div>
                <div className="space-y-1 text-[8.5px] sm:text-[9px]">
                  <div className="flex items-center justify-between p-1.5 sm:p-2 rounded-lg bg-emerald-600/10 border border-emerald-500/20 text-emerald-400">
                    <span className="truncate">JPMorgan Node #01</span>
                    <span className="shrink-0 font-bold">100% HONEST</span>
                  </div>
                  <div className="flex items-center justify-between p-1.5 sm:p-2 rounded-lg bg-emerald-600/10 border border-emerald-500/20 text-emerald-400">
                    <span className="truncate">HSBC Node #02</span>
                    <span className="shrink-0 font-bold">100% HONEST</span>
                  </div>
                  <div className="flex items-center justify-between p-1.5 sm:p-2 rounded-lg bg-rose-600/10 border border-rose-500/20 text-rose-400">
                    <span className="truncate">Adversary Node</span>
                    <span className="shrink-0 font-bold">POISONED (KRUM)</span>
                  </div>
                </div>
              </motion.div>
            )}

            {activeTab === 'sar' && (
              <motion.div key="sar" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-2.5 text-[9.5px] sm:text-[10px] font-mono min-w-0">
                <div className="p-2.5 sm:p-3 rounded-xl bg-amber-600/10 border border-amber-500/20 space-y-0.5">
                  <div className="text-amber-300 font-bold">FinCEN SAR Automated Compliance</div>
                  <div className="text-slate-400 text-[8.5px] sm:text-[9px]">SHAP Feature Attributions & Signed XML Export</div>
                </div>
                <div className="p-2 sm:p-2.5 rounded-xl bg-[#03030c] border border-white/6 text-[8.5px] sm:text-[9px] text-amber-200/90 leading-relaxed font-mono break-all max-h-24 overflow-y-auto">
                  &lt;FinCEN_SAR&gt;&lt;Amount Ccy="USD"&gt;1450000.00&lt;/Amount&gt;&lt;RiskScore&gt;0.94&lt;/RiskScore&gt;&lt;/FinCEN_SAR&gt;
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="text-[8px] sm:text-[9px] font-mono text-slate-500 text-center border-t border-white/5 pt-1.5">
            Click sidebar icons ({sidebarButtons.map(b => b.label).join(' · ')}) to inspect live platform modules
          </div>
        </div>
      </div>
    </div>
  );
});

// ── FADE WRAPPER ─────────────────────────────────────────────────────────────
const FadeSection = memo(function FadeSection({ children, className = '', delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: '-60px' });
  return (
    <motion.div ref={ref} initial={{ opacity: 0, y: 20 }} animate={inView ? { opacity: 1, y: 0 } : {}} transition={{ duration: 0.5, delay, ease: 'easeOut' }} className={`will-change-transform transform-gpu ${className}`}>
      {children}
    </motion.div>
  );
});

// ── 2026 LUXURY GEOMETRIC CF MONOGRAM LOGO COMPONENT ────────────────────────
const BrandLogo = memo(({ className = 'w-9 h-9' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 120 120" fill="none">
    <defs>
      <linearGradient id="brand-border-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#6366f1" />
        <stop offset="50%" stopColor="#a855f7" />
        <stop offset="100%" stopColor="#06b6d4" />
      </linearGradient>
      <linearGradient id="brand-c-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#818cf8" />
        <stop offset="50%" stopColor="#6366f1" />
        <stop offset="100%" stopColor="#4f46e5" />
      </linearGradient>
      <linearGradient id="brand-f-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#c084fc" />
        <stop offset="50%" stopColor="#a855f7" />
        <stop offset="100%" stopColor="#06b6d4" />
      </linearGradient>
      <filter id="brand-glow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
    </defs>
    <rect x="4" y="4" width="112" height="112" rx="26" fill="#07071c" stroke="url(#brand-border-grad)" strokeWidth="2.5" />
    <path d="M 84,36 C 68,22 42,22 32,38 C 22,54 22,66 32,82 C 42,98 68,98 84,84" fill="none" stroke="url(#brand-c-grad)" strokeWidth="7.5" strokeLinecap="round" filter="url(#brand-glow)" />
    <path d="M 52,36 L 86,36 M 52,36 L 52,84 M 52,58 L 78,58" fill="none" stroke="url(#brand-f-grad)" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" filter="url(#brand-glow)" />
    <circle cx="32" cy="38" r="4.5" fill="#818cf8" stroke="#ffffff" strokeWidth="1.2" />
    <circle cx="86" cy="36" r="4.5" fill="#c084fc" stroke="#ffffff" strokeWidth="1.2" />
    <circle cx="78" cy="58" r="4" fill="#38bdf8" stroke="#ffffff" strokeWidth="1.2" />
    <circle cx="32" cy="82" r="4.5" fill="#34d399" stroke="#ffffff" strokeWidth="1.2" />
    <circle cx="60" cy="60" r="6.5" fill="#060616" stroke="url(#brand-border-grad)" strokeWidth="2" />
    <circle cx="60" cy="60" r="2.5" fill="#38bdf8" />
  </svg>
));

const MenuIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>
  </svg>
);
const CloseIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);
const ArrowRight = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>
  </svg>
);
const ChevronDown = () => (
  <svg className="w-3 h-3 text-slate-500 group-hover:text-slate-300 transition-transform group-hover:rotate-180" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

// ── MAIN LANDING PAGE ────────────────────────────────────────────────────────
export default function LandingPage() {
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isCapabilitiesDropdownOpen, setIsCapabilitiesDropdownOpen] = useState(false);
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);
  const [activeModule, setActiveModule] = useState<Module | null>(PLATFORM_MODULES[0] ?? null);
  const [activeArchNode, setActiveArchNode] = useState<ArchNode | null>(ARCH_NODES[0] ?? null);
  const [activeWorkflowStep, setActiveWorkflowStep] = useState<number>(1);
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

  const handleNavClick = (id: string) => {
    scrollToSection(id);
    setIsMobileMenuOpen(false);
    setIsCapabilitiesDropdownOpen(false);
  };

  const currentWorkflowStep = PRESENTATION_WORKFLOW.find(s => s.id === activeWorkflowStep)!;

  return (
    <div className="min-h-screen bg-[#05050f] text-slate-300 font-sans antialiased selection:bg-indigo-600 selection:text-white w-full max-w-full overflow-x-hidden min-w-0">

      {/* 2026 Ambient Fluid Glow background */}
      <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="absolute -top-72 -left-48 w-[700px] h-[700px] rounded-full bg-indigo-600/15 blur-[140px]" />
        <div className="absolute top-1/2 -right-72 w-[600px] h-[600px] rounded-full bg-purple-600/10 blur-[150px]" />
        <div className="absolute -bottom-40 left-1/3 w-[500px] h-[500px] rounded-full bg-cyan-600/10 blur-[130px]" />
        <div className="absolute inset-0" style={{ backgroundImage: 'linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)', backgroundSize: '64px 64px' }} />
      </div>

      <div className="relative z-10 w-full max-w-full min-w-0">

        {/* ── HEADER NAVBAR ────────────────────────────────────────── */}
        <header className="sticky top-0 z-50 h-16 flex items-center border-b border-white/6 bg-[#05050f]/80 backdrop-blur-2xl w-full max-w-full">
          <div className="w-full max-w-7xl mx-auto px-3.5 sm:px-6 flex items-center justify-between min-w-0">
            {/* Brand Logo & Clean Name */}
            <div className="flex items-center gap-2 cursor-pointer min-w-0" onClick={() => navigate('/')}>
              <BrandLogo className="w-8 h-8 sm:w-9 sm:h-9 shrink-0" />
              <span className="font-bold text-sm sm:text-base text-slate-100 tracking-tight truncate">CF-Intelligence</span>
            </div>

            {/* Desktop Navigation */}
            <nav aria-label="primary" className="hidden lg:flex items-center gap-7 text-[13px] font-medium text-slate-400">
              <a
                href="#hero"
                onClick={(e) => { e.preventDefault(); handleNavClick('hero'); }}
                className="hover:text-slate-100 transition-colors"
              >
                Overview
              </a>

              {/* Combined CAPABILITIES Dropdown */}
              <div
                className="relative group py-2"
                onMouseEnter={() => setIsCapabilitiesDropdownOpen(true)}
                onMouseLeave={() => setIsCapabilitiesDropdownOpen(false)}
              >
                <button
                  onClick={() => handleNavClick('problem-solution')}
                  className="flex items-center gap-1.5 hover:text-slate-100 transition-colors cursor-pointer py-1"
                >
                  <span>Capabilities</span>
                  <ChevronDown />
                </button>

                <div className={`absolute top-full left-1/2 -translate-x-1/2 w-64 p-2 bg-[#0a0a1c]/95 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-2xl transition-all duration-200 ${
                  isCapabilitiesDropdownOpen ? 'opacity-100 pointer-events-auto translate-y-1' : 'opacity-0 pointer-events-none translate-y-0'
                }`}>
                  <div className="text-[9px] font-mono text-slate-500 uppercase tracking-widest px-3 py-1.5 mb-1 border-b border-white/5">
                    Platform Features
                  </div>
                  <a
                    href="#problem-solution"
                    onClick={(e) => { e.preventDefault(); handleNavClick('problem-solution'); }}
                    className="flex items-center justify-between px-3 py-2 text-[12px] text-slate-300 hover:text-white hover:bg-white/5 rounded-xl transition-colors"
                  >
                    <div>
                      <div className="font-semibold">Problem</div>
                      <div className="text-[10px] text-slate-500">Cross-bank fraud analysis</div>
                    </div>
                  </a>
                  <a
                    href="#how-it-works"
                    onClick={(e) => { e.preventDefault(); handleNavClick('how-it-works'); }}
                    className="flex items-center justify-between px-3 py-2 text-[12px] text-slate-300 hover:text-white hover:bg-white/5 rounded-xl transition-colors"
                  >
                    <div>
                      <div className="font-semibold">Workflow</div>
                      <div className="text-[10px] text-slate-500">8-stage federated pipeline</div>
                    </div>
                  </a>
                  <a
                    href="#product"
                    onClick={(e) => { e.preventDefault(); handleNavClick('product'); }}
                    className="flex items-center justify-between px-3 py-2 text-[12px] text-slate-300 hover:text-white hover:bg-white/5 rounded-xl transition-colors"
                  >
                    <div>
                      <div className="font-semibold">Engine Specs</div>
                      <div className="text-[10px] text-slate-500">Platform engine specs</div>
                    </div>
                  </a>
                </div>
              </div>

              <a href="#platform" onClick={(e) => { e.preventDefault(); handleNavClick('platform'); }} className="hover:text-slate-100 transition-colors">Platform</a>
              <a href="#architecture" onClick={(e) => { e.preventDefault(); handleNavClick('architecture'); }} className="hover:text-slate-100 transition-colors">Architecture</a>
              <a href="#security" onClick={(e) => { e.preventDefault(); handleNavClick('security'); }} className="hover:text-slate-100 transition-colors">Security</a>
              <a href="#api" onClick={(e) => { e.preventDefault(); handleNavClick('api'); }} className="hover:text-slate-100 transition-colors">API & Docs</a>
            </nav>

            <div className="flex items-center gap-2 sm:gap-3 shrink-0">
              <button
                aria-label="Toggle Navigation Menu"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-xl bg-white/5 border border-white/10 text-slate-300 hover:text-white cursor-pointer transition-colors"
              >
                {isMobileMenuOpen ? <CloseIcon /> : <MenuIcon />}
              </button>
              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:flex items-center gap-2 px-5 py-2 rounded-xl text-[13px] font-semibold text-white bg-gradient-to-r from-indigo-600 via-indigo-500 to-purple-600 hover:opacity-95 transition-all cursor-pointer shadow-[0_0_25px_rgba(99,102,241,0.4)] shrink-0"
              >
                Launch Demo <ArrowRight />
              </button>
            </div>
          </div>
        </header>

        {/* ── 2026 MODERN ULTRA-FLUID MOBILE OVERLAY DRAWER ────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.nav
              initial={{ opacity: 0, scale: 0.98, y: -10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.98, y: -10 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className="lg:hidden fixed inset-x-0 top-16 bottom-0 z-40 bg-[#05050f]/95 backdrop-blur-3xl p-4 sm:p-6 overflow-y-auto flex flex-col justify-between border-b border-white/10 shadow-2xl w-full max-w-full"
            >
              <div className="space-y-4 min-w-0">
                <div className="flex items-center justify-between border-b border-white/8 pb-3 min-w-0">
                  <div className="text-xs font-mono font-bold uppercase tracking-widest text-indigo-400 truncate">Navigation Menu</div>
                  <span className="text-[10px] font-mono text-slate-500 shrink-0">CF-Intelligence</span>
                </div>

                {/* Mobile Menu Matching Desktop Headers Exactly */}
                <div className="space-y-3 min-w-0">
                  {/* Overview */}
                  <a
                    href="#hero"
                    onClick={(e) => { e.preventDefault(); handleNavClick('hero'); }}
                    className="p-3.5 rounded-2xl bg-white/3 border border-white/8 hover:bg-indigo-600/10 hover:border-indigo-500/30 transition-all cursor-pointer block min-w-0"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-[9px] font-mono font-bold text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-md">Overview</span>
                      <ArrowRight />
                    </div>
                    <div className="text-xs font-bold text-slate-200 mt-1">Overview</div>
                    <div className="text-[10px] font-mono text-slate-500">System introduction & stats</div>
                  </a>

                  {/* Capabilities Group (Desktop Dropdown mirror) */}
                  <div className="p-3.5 rounded-2xl bg-white/3 border border-indigo-500/20 space-y-2.5 min-w-0">
                    <button
                      onClick={() => handleNavClick('problem-solution')}
                      className="w-full flex items-center justify-between text-left cursor-pointer group"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-[9px] font-mono font-bold text-purple-400 bg-purple-500/10 border border-purple-500/20 px-2 py-0.5 rounded-md">Capabilities</span>
                          <span className="text-[9px] font-mono text-slate-500">(3 Sections)</span>
                        </div>
                        <div className="text-xs font-bold text-slate-100 group-hover:text-purple-300 transition-colors mt-1">Capabilities</div>
                      </div>
                      <ArrowRight />
                    </button>

                    {/* 3 Capabilities Sub-items */}
                    <div className="grid grid-cols-1 gap-1.5 pt-1 border-t border-white/6 font-mono text-xs">
                      <a
                        href="#problem-solution"
                        onClick={(e) => { e.preventDefault(); handleNavClick('problem-solution'); }}
                        className="flex items-center justify-between p-2 rounded-xl bg-white/3 hover:bg-white/7 transition-colors"
                      >
                        <div>
                          <div className="font-semibold text-slate-200 text-xs">Problem</div>
                          <div className="text-[9.5px] text-slate-500">Cross-bank fraud analysis</div>
                        </div>
                        <span className="text-[10px] text-purple-400">#01</span>
                      </a>
                      <a
                        href="#how-it-works"
                        onClick={(e) => { e.preventDefault(); handleNavClick('how-it-works'); }}
                        className="flex items-center justify-between p-2 rounded-xl bg-white/3 hover:bg-white/7 transition-colors"
                      >
                        <div>
                          <div className="font-semibold text-slate-200 text-xs">Workflow</div>
                          <div className="text-[9.5px] text-slate-500">8-stage federated pipeline</div>
                        </div>
                        <span className="text-[10px] text-purple-400">#02</span>
                      </a>
                      <a
                        href="#product"
                        onClick={(e) => { e.preventDefault(); handleNavClick('product'); }}
                        className="flex items-center justify-between p-2 rounded-xl bg-white/3 hover:bg-white/7 transition-colors"
                      >
                        <div>
                          <div className="font-semibold text-slate-200 text-xs">Engine Specs</div>
                          <div className="text-[9.5px] text-slate-500">Platform engine specs</div>
                        </div>
                        <span className="text-[10px] text-purple-400">#03</span>
                      </a>
                    </div>
                  </div>

                  {/* Other Desktop Headers */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 min-w-0">
                    {[
                      { label: 'Platform',      targetId: 'platform',     badge: 'Consortium',   desc: 'Bank node inspector' },
                      { label: 'Architecture',  targetId: 'architecture', badge: 'Architecture', desc: 'Service layer mesh' },
                      { label: 'Security',      targetId: 'security',     badge: 'Security',     desc: 'DP & trust boundaries' },
                      { label: 'API & Docs',    targetId: 'api',          badge: 'SDK & API',    desc: 'REST API & TypeScript SDK' },
                    ].map(link => (
                      <a
                        key={link.label}
                        href={`#${link.targetId}`}
                        onClick={(e) => { e.preventDefault(); handleNavClick(link.targetId); }}
                        className="p-3.5 rounded-2xl bg-white/3 border border-white/8 hover:bg-indigo-600/10 hover:border-indigo-500/30 transition-all cursor-pointer group space-y-1 block min-w-0"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-[9px] font-mono font-bold text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-md truncate">
                            {link.badge}
                          </span>
                          <ArrowRight />
                        </div>
                        <div className="text-xs font-bold text-slate-200 group-hover:text-white transition-colors truncate">{link.label}</div>
                        <div className="text-[10px] font-mono text-slate-500 truncate">{link.desc}</div>
                      </a>
                    ))}
                  </div>
                </div>
              </div>

              {/* Bottom Mobile Action */}
              <div className="pt-4 border-t border-white/8 space-y-2.5 min-w-0 mt-4">
                <button
                  onClick={() => { setIsMobileMenuOpen(false); navigate('/dashboard'); }}
                  className="w-full py-3 px-5 rounded-2xl text-xs font-bold text-white bg-gradient-to-r from-indigo-600 via-indigo-500 to-purple-600 shadow-[0_0_30px_rgba(99,102,241,0.4)] cursor-pointer flex items-center justify-center gap-2"
                >
                  Launch Live Platform Demo <ArrowRight />
                </button>
                <div className="text-center text-[9.5px] font-mono text-slate-600 truncate">
                  Privacy-Preserving Federated Intelligence Network
                </div>
              </div>
            </motion.nav>
          )}
        </AnimatePresence>

        {/* ══════════════════════════════════════════════════════════
            SECTION 1 — HERO / OVERVIEW (#hero)
        ══════════════════════════════════════════════════════════ */}
        <section id="hero" className="relative py-10 sm:py-20 px-3.5 sm:px-6 max-w-7xl mx-auto w-full min-w-0">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 sm:gap-12 items-center min-w-0">

            {/* Left Hero Content */}
            <motion.div initial={{opacity:0,y:25}} animate={{opacity:1,y:0}} transition={{duration:0.6}} className="lg:col-span-7 space-y-6 sm:space-y-8 min-w-0">
              <div className="space-y-3 sm:space-y-4 min-w-0">
                <div className="inline-flex items-center gap-1.5 sm:gap-2 px-3 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-[11px] sm:text-xs font-mono max-w-full truncate">
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse shrink-0" />
                  <span className="truncate">Privacy-Preserving Federated Infrastructure</span>
                </div>
                <h1 className="text-3xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight leading-[1.15] break-words">
                  <span className="bg-gradient-to-r from-slate-100 via-indigo-200 to-slate-300 bg-clip-text text-transparent">
                    Collaborative Cross-Bank
                  </span>
                  <br />
                  <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">
                    Fraud Detection Platform
                  </span>
                </h1>
                <p className="text-slate-400 text-sm sm:text-base leading-relaxed max-w-xl font-sans">
                  CF-Intelligence enables banking institutions to collectively train Graph Neural Networks on transaction topologies to detect multi-bank money laundering networks without raw customer data exposure.
                </p>
              </div>

              {/* High impact feature highlights */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 sm:gap-3 w-full min-w-0">
                <div className="p-3.5 sm:p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl min-w-0">
                  <div className="text-xl sm:text-2xl font-black font-mono bg-gradient-to-r from-emerald-400 to-teal-400 bg-clip-text text-transparent">94.2%</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-0.5 sm:mt-1">Detection Gain</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">vs. 42% isolated</div>
                </div>
                <div className="p-3.5 sm:p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl min-w-0">
                  <div className="text-xl sm:text-2xl font-black font-mono bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">ε = 0.50</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-0.5 sm:mt-1">Differential Privacy</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">(ε, δ)-DP bounded</div>
                </div>
                <div className="p-3.5 sm:p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl min-w-0">
                  <div className="text-xl sm:text-2xl font-black font-mono bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">5× Gain</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-0.5 sm:mt-1">FPR Reduction</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">31% → 6.1% FPR</div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 sm:gap-4 w-full min-w-0">
                <button
                  onClick={() => navigate('/dashboard')}
                  className="flex items-center justify-center gap-2.5 px-6 sm:px-7 py-3.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 text-xs sm:text-sm font-semibold text-white transition-all cursor-pointer shadow-[0_0_35px_rgba(99,102,241,0.45)] hover:shadow-[0_0_50px_rgba(99,102,241,0.6)] w-full sm:w-auto text-center"
                >
                  Launch Live Platform Demo <ArrowRight />
                </button>
                <a
                  href="#architecture"
                  onClick={(e) => { e.preventDefault(); handleNavClick('architecture'); }}
                  className="flex items-center justify-center gap-2 px-5 sm:px-6 py-3.5 rounded-2xl border border-white/10 hover:border-white/25 text-xs sm:text-sm font-medium text-slate-300 hover:text-white transition-all backdrop-blur-lg w-full sm:w-auto text-center"
                >
                  Explore System Design
                </a>
              </div>
            </motion.div>

            {/* Right Interactive Dashboard Preview */}
            <motion.div initial={{opacity:0,y:25}} animate={{opacity:1,y:0}} transition={{duration:0.6,delay:0.15}} className="lg:col-span-5 w-full min-w-0">
              <InteractiveDashboardPreview flRound={flRound} accuracy={accuracy} />
            </motion.div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 2 — PROBLEM STATEMENT (#problem-solution)
        ══════════════════════════════════════════════════════════ */}
        <section id="problem-solution" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-8 sm:mb-10 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Problem Statement</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">Cross-Bank Money Laundering Fragmentation</h2>
              <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                Sophisticated financial crime syndicates structure transaction paths across multiple banks to bypass institution-local AML rules. Single-bank GNN models only see internal legs, leaving cross-border networks invisible.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 sm:gap-6 w-full min-w-0">
              <div className="p-5 sm:p-7 rounded-3xl bg-white/2 border border-rose-500/20 backdrop-blur-xl space-y-4 min-w-0">
                <div className="flex flex-wrap items-center justify-between border-b border-white/6 pb-3 sm:pb-4 gap-2">
                  <h3 className="text-sm sm:text-base font-bold text-slate-200">Traditional Bank-Isolated AML Systems</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold text-rose-400 bg-rose-500/10 border border-rose-500/20">42% Detection</span>
                </div>
                <ul className="space-y-2.5 text-xs font-mono text-slate-400 break-words">
                  <li className="flex items-start gap-2"><span className="text-rose-500 shrink-0">✕</span> Graph Neural Networks isolated to single institution ledgers</li>
                  <li className="flex items-start gap-2"><span className="text-rose-500 shrink-0">✕</span> Smurfing across multiple banks stays hidden below local thresholds</li>
                  <li className="flex items-start gap-2"><span className="text-rose-500 shrink-0">✕</span> High false positive rate (~31%) overwhelms fraud investigation teams</li>
                </ul>
              </div>

              <div className="p-5 sm:p-7 rounded-3xl bg-indigo-600/5 border border-indigo-500/30 backdrop-blur-xl space-y-4 shadow-[inset_0_0_50px_rgba(99,102,241,0.08)] min-w-0">
                <div className="flex flex-wrap items-center justify-between border-b border-white/6 pb-3 sm:pb-4 gap-2">
                  <h3 className="text-sm sm:text-base font-bold text-slate-100">CFI Federated Consortium Network</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">94.2% Detection</span>
                </div>
                <ul className="space-y-2.5 text-xs font-mono text-slate-300 break-words">
                  <li className="flex items-start gap-2"><span className="text-emerald-400 shrink-0">✓</span> Collaborative model optimization over consortium transaction topology</li>
                  <li className="flex items-start gap-2"><span className="text-emerald-400 shrink-0">✓</span> Zero PII exits bank boundary with provable (ε=0.50, δ=1e-5)-differential privacy</li>
                  <li className="flex items-start gap-2"><span className="text-emerald-400 shrink-0">✓</span> False positive rate reduced to 6.1% (5× investigator bandwidth improvement)</li>
                </ul>
              </div>
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 3 — PRESENTATION-FRIENDLY WORKFLOW PIPELINE (#how-it-works)
        ══════════════════════════════════════════════════════════ */}
        <section id="how-it-works" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-8 sm:mb-10 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-purple-400 uppercase tracking-widest mb-2">Execution Pipeline</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">8-Stage Federated Training Architecture</h2>
              <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                Click any pipeline stage below to view the architectural overview, data input/output specifications, and cryptographic guarantees.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 sm:gap-8 w-full min-w-0">
              {/* Left Selector List */}
              <div className="lg:col-span-4 space-y-1.5 w-full min-w-0">
                {PRESENTATION_WORKFLOW.map(step => (
                  <button
                    key={step.id}
                    onClick={() => setActiveWorkflowStep(step.id)}
                    className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl border text-left transition-all cursor-pointer min-w-0 ${
                      activeWorkflowStep === step.id
                        ? 'bg-purple-600/15 border-purple-500/40 text-slate-100 shadow-[0_0_20px_rgba(168,85,247,0.2)]'
                        : 'border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/4'
                    }`}
                  >
                    <span className={`w-5 sm:w-6 h-5 sm:h-6 rounded-lg flex items-center justify-center text-[11px] sm:text-xs font-mono font-bold shrink-0 ${
                      activeWorkflowStep === step.id ? 'bg-purple-600 text-white' : 'bg-white/5 text-slate-500'
                    }`}>
                      {step.id}
                    </span>
                    <span className="text-xs font-semibold truncate">{step.short}</span>
                  </button>
                ))}
              </div>

              {/* Right Executive Presentation Card */}
              <div className="lg:col-span-8 w-full min-w-0">
                <div className="p-4 sm:p-7 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-4 sm:space-y-6 w-full min-w-0 overflow-hidden">
                  <div className="flex flex-wrap items-center justify-between border-b border-white/6 pb-3 sm:pb-4 gap-2 min-w-0">
                    <div className="min-w-0">
                      <span className="text-[9px] sm:text-[10px] font-mono font-bold text-purple-400 uppercase tracking-widest">
                        {currentWorkflowStep.badge} of 08
                      </span>
                      <h3 className="text-base sm:text-xl font-bold text-slate-100 mt-0.5 truncate">{currentWorkflowStep.label}</h3>
                    </div>
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] sm:text-xs font-mono font-semibold bg-purple-500/10 border border-purple-500/20 text-purple-300 shrink-0">
                      Production Pipeline
                    </span>
                  </div>

                  <p className="text-xs sm:text-sm text-slate-300 leading-relaxed font-sans">
                    {currentWorkflowStep.summary}
                  </p>

                  {/* Highlights Bullet List */}
                  <div className="space-y-2 pt-1 min-w-0">
                    <div className="text-[9px] sm:text-[10px] font-mono uppercase tracking-wider text-slate-500">Key Engineering & Privacy Highlights</div>
                    <div className="space-y-1.5 font-sans text-xs text-slate-300 min-w-0">
                      {currentWorkflowStep.highlights.map(hl => (
                        <div key={hl} className="flex items-start gap-2.5 p-2.5 sm:p-3 rounded-xl bg-white/3 border border-white/5 min-w-0">
                          <span className="text-purple-400 font-bold shrink-0">✓</span>
                          <span className="break-words">{hl}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Data Input/Output Specifications */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1 w-full min-w-0">
                    <div className="p-3 rounded-2xl bg-[#03030c] border border-white/6 font-mono text-xs min-w-0">
                      <div className="text-[8.5px] sm:text-[9px] text-slate-500 uppercase tracking-wider mb-0.5">Data Input</div>
                      <div className="text-purple-300 font-semibold truncate">{currentWorkflowStep.input}</div>
                    </div>
                    <div className="p-3 rounded-2xl bg-[#03030c] border border-white/6 font-mono text-xs min-w-0">
                      <div className="text-[8.5px] sm:text-[9px] text-slate-500 uppercase tracking-wider mb-0.5">Data Output</div>
                      <div className="text-emerald-400 font-semibold truncate">{currentWorkflowStep.output}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 4 — ENGINE CAPABILITIES (#product)
        ══════════════════════════════════════════════════════════ */}
        <section id="product" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-8 sm:mb-10 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-cyan-400 uppercase tracking-widest mb-2">Engine Specifications</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">Platform Engineering Capabilities</h2>
              <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                Inspect platform component specifications, algorithms, input/output tensors, and stack requirements.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 sm:gap-8 w-full min-w-0">
              <div className="lg:col-span-4 space-y-1.5 w-full min-w-0">
                {PLATFORM_MODULES.map(mod => (
                  <button
                    key={mod.id}
                    onClick={() => setActiveModule(mod)}
                    className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl border text-left transition-all cursor-pointer min-w-0 ${
                      activeModule?.id === mod.id
                        ? 'bg-cyan-600/15 border-cyan-500/40 text-slate-100 shadow-[0_0_20px_rgba(6,182,212,0.2)]'
                        : 'border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/4'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-semibold truncate">{mod.name}</div>
                      <div className="text-[9.5px] font-mono text-slate-600 truncate">{mod.category}</div>
                    </div>
                  </button>
                ))}
              </div>

              {activeModule && (
                <div className="lg:col-span-8 p-4 sm:p-6 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-4 sm:space-y-5 w-full min-w-0 overflow-hidden">
                  <div className="border-b border-white/6 pb-3 sm:pb-4 min-w-0">
                    <span className="text-[9.5px] font-mono text-cyan-400 uppercase tracking-wider">{activeModule.category}</span>
                    <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-0.5 truncate">{activeModule.name}</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mt-1.5 font-sans">{activeModule.purpose}</p>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs font-mono w-full min-w-0">
                    {[
                      { label: 'Algorithm',  value: activeModule.algorithm },
                      { label: 'Technology', value: activeModule.tech },
                      { label: 'Inputs',     value: activeModule.inputs },
                      { label: 'Outputs',    value: activeModule.outputs },
                    ].map(row => (
                      <div key={row.label} className="p-3 rounded-xl bg-[#03030c] border border-white/6 min-w-0">
                        <div className="text-slate-500 text-[8.5px] sm:text-[9px] uppercase tracking-wider mb-0.5">{row.label}</div>
                        <div className="text-slate-200 break-words">{row.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 5 — PLATFORM / NODE INSPECTOR (#platform)
        ══════════════════════════════════════════════════════════ */}
        <section id="platform" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-8 sm:mb-10 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-emerald-400 uppercase tracking-widest mb-2">Consortium Architecture</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">Active Bank Node Inspector</h2>
              <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                Click any institution node to inspect hardware specs, PyTorch execution runtime, and ISO 20022 message streams.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5 sm:gap-5 w-full min-w-0">
              {Object.values(BANK_NODES).map((bank, i) => (
                <motion.div
                  key={bank.id}
                  initial={{opacity:0,y:15}} animate={{opacity:1,y:0}} transition={{delay:i*0.08}}
                  whileHover={{y:-4}}
                  onClick={() => setActiveBankDrawer(bank)}
                  className="p-4 sm:p-5 rounded-2xl bg-white/2 border border-white/8 hover:border-emerald-500/40 hover:shadow-[0_0_30px_rgba(16,185,129,0.15)] cursor-pointer transition-all space-y-2.5 sm:space-y-3 min-w-0"
                >
                  <div className="flex items-center justify-between">
                    <span className="px-2 py-0.5 rounded text-[9.5px] sm:text-[10px] font-mono font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">READY</span>
                    <span className="text-[9.5px] sm:text-[10px] font-mono text-slate-500">{bank.latency}</span>
                  </div>
                  <div>
                    <h3 className="text-xs sm:text-sm font-bold text-slate-200 truncate">{bank.name}</h3>
                    <div className="text-[9.5px] sm:text-[10px] font-mono text-slate-600 mt-0.5 truncate">{bank.ticker}</div>
                  </div>
                  <div className="text-[10.5px] sm:text-[11px] font-mono text-slate-500 border-t border-white/6 pt-2 leading-snug break-words">
                    {bank.hardware}
                  </div>
                </motion.div>
              ))}
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 6 — ARCHITECTURE (#architecture)
        ══════════════════════════════════════════════════════════ */}
        <section id="architecture" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-8 sm:mb-10 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Service Mesh Design</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">System Service Topology</h2>
              <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                Click any service layer node to inspect internal responsibilities, communication protocols, and technology stack.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 sm:gap-8 w-full min-w-0">
              <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full min-w-0">
                {ARCH_NODES.map(node => (
                  <button
                    key={node.id}
                    onClick={() => setActiveArchNode(node)}
                    className={`p-3 sm:p-4 rounded-xl border text-left transition-all cursor-pointer min-w-0 ${
                      activeArchNode?.id === node.id
                        ? 'bg-indigo-600/15 border-indigo-500/40 text-slate-100 shadow-[0_0_20px_rgba(99,102,241,0.2)]'
                        : 'bg-white/2 border-white/7 hover:border-white/15'
                    }`}
                  >
                    <div className="text-xs font-semibold mb-1 truncate">{node.label}</div>
                    <div className="text-[9.5px] font-mono text-slate-600 leading-snug truncate">{node.tech.slice(0, 2).join(' · ')}</div>
                  </button>
                ))}
              </div>

              {activeArchNode && (
                <div className="lg:col-span-7 p-4 sm:p-6 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-4 sm:space-y-5 w-full min-w-0 overflow-hidden">
                  <div className="border-b border-white/6 pb-3 sm:pb-4 min-w-0">
                    <span className="text-[9.5px] font-mono text-indigo-400 uppercase tracking-wider">Service Node</span>
                    <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-0.5 truncate">{activeArchNode.label}</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mt-1.5 font-sans">{activeArchNode.description}</p>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono w-full min-w-0">
                    {[
                      { label: 'Responsibilities', items: activeArchNode.responsibilities, dot: 'text-indigo-400' },
                      { label: 'Protocols',        items: activeArchNode.protocols,       dot: 'text-purple-400' },
                      { label: 'Technology',       items: activeArchNode.tech,            dot: 'text-cyan-400' },
                    ].map(col => (
                      <div key={col.label} className="min-w-0">
                        <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-1.5">{col.label}</div>
                        <ul className="space-y-1">
                          {col.items.map(it => (
                            <li key={it} className="text-slate-300 flex items-center gap-1.5 truncate"><span className={`${col.dot} shrink-0`}>•</span><span className="truncate">{it}</span></li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 7 — SECURITY & PRIVACY (#security)
        ══════════════════════════════════════════════════════════ */}
        <section id="security" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <FadeSection>
            <div className="max-w-3xl mb-6 sm:mb-8 min-w-0">
              <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-purple-400 uppercase tracking-widest mb-2">Cryptographic Boundaries</div>
              <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">Privacy & Trust Boundary Model</h2>
            </div>

            <div className="flex gap-1 p-1 bg-white/3 border border-white/8 rounded-2xl w-full sm:w-fit mb-6 sm:mb-8 overflow-x-auto min-w-0">
              {[{id:'flow',label:'Data Flow'},{id:'threat',label:'Threat Model'},{id:'compliance',label:'Compliance'}].map(tab=>(
                <button
                  key={tab.id}
                  onClick={() => setActivePrivacyTab(tab.id as 'flow'|'threat'|'compliance')}
                  className={`px-4 sm:px-5 py-1.5 sm:py-2 text-xs font-semibold rounded-xl transition-all cursor-pointer shrink-0 ${
                    activePrivacyTab === tab.id
                      ? 'bg-indigo-600 text-white shadow-[0_0_16px_rgba(99,102,241,0.5)]'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <AnimatePresence mode="wait">
              <motion.div key={activePrivacyTab} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-8}} transition={{duration:0.2}} className="w-full min-w-0">
                {activePrivacyTab === 'flow' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 sm:gap-6 w-full min-w-0">
                    {[
                      {title:'Inside Bank Perimeter',items:['Raw transaction ledgers','Customer PII & Identity','Account balance histories','Local GNN graph embeddings'],note:'← Strict non-export policy',noteColor:'text-rose-400'},
                      {title:'Transmitted Tensors (DP)',items:['DP Gaussian-noised gradients','Paillier homomorphic ciphertexts','Round participation tokens','HSM-signed attestations'],note:'← (ε=0.50, δ=1e-5)-DP guarantee',noteColor:'text-emerald-400'},
                      {title:'SGX TEE Enclave Node',items:['HE encrypted sum aggregation','Intel IAS attestation quotes','Isolated enclave memory pages','No external network access'],note:'← Hardware cryptographic vault',noteColor:'text-purple-400'},
                    ].map(col => (
                      <div key={col.title} className="p-4 sm:p-6 rounded-3xl bg-white/2 border border-white/8 space-y-3 sm:space-y-4 min-w-0">
                        <div className="text-xs font-mono font-bold text-slate-300 uppercase tracking-wider truncate">{col.title}</div>
                        <div className="space-y-1.5 sm:space-y-2 font-mono text-xs text-slate-400 min-w-0">
                          {col.items.map(item => (
                            <div key={item} className="p-2 sm:p-2.5 rounded-xl bg-[#03030c] border border-white/6 break-words">{item}</div>
                          ))}
                        </div>
                        <div className={`text-[10px] font-mono ${col.noteColor} truncate`}>{col.note}</div>
                      </div>
                    ))}
                  </div>
                )}

                {activePrivacyTab === 'threat' && (
                  <div className="space-y-2.5 sm:space-y-3 w-full min-w-0">
                    {[
                      {threat:'Gradient Inversion Attack',    mitigation:'Gaussian DP noise (σ calibrated to ε=0.50) makes gradient inversion mathematically infeasible. Clipping bound C=1.0.'},
                      {threat:'Byzantine Gradient Poisoning', mitigation:'Krum + Trimmed Mean Byzantine-robust aggregation neutralises up to f < n/2 adversarial bank nodes per round.'},
                      {threat:'Coordinator Compromise',       mitigation:'Intel SGX TEE handles aggregation. Central coordinator sees ciphertexts only; plaintext updates never exist outside enclave.'},
                      {threat:'Membership Inference',         mitigation:'RDP accountant bounds cumulative budget. Training halts if ε limit is reached. Per-sample clipping prevents memorisation.'},
                      {threat:'Model Extraction',             mitigation:'Global model weights are distributed exclusively to authenticated bank nodes via mutual TLS (mTLS).' },
                    ].map(row => (
                      <div key={row.threat} className="flex flex-col sm:flex-row items-start gap-2.5 sm:gap-5 p-4 sm:p-5 rounded-2xl bg-white/2 border border-white/8 w-full min-w-0">
                        <div className="w-full sm:w-48 shrink-0">
                          <div className="text-xs font-bold text-rose-400 break-words">{row.threat}</div>
                          <div className="text-[9.5px] font-mono text-emerald-400 mt-0.5">Mitigated</div>
                        </div>
                        <div className="text-xs text-slate-400 font-mono leading-relaxed break-words flex-1 min-w-0">{row.mitigation}</div>
                      </div>
                    ))}
                  </div>
                )}

                {activePrivacyTab === 'compliance' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 sm:gap-5 w-full min-w-0">
                    {[
                      {standard:'GDPR Article 25',        status:'Privacy by Design',detail:'DP guarantees built into tensor aggregation. Zero customer PII ever exits bank boundary.'},
                      {standard:'FinCEN SAR Regulation',  status:'Compliant',        detail:'Automated SAR XML filing generation with cryptographic evidence sign-off.'},
                      {standard:'EU AML Directive 6AMLD', status:'Compliant',        detail:'Detects cross-border money laundering networks without cross-border data transfer.'},
                      {standard:'NIST SP 800-188',        status:'Aligned',          detail:'Strict de-identification via Rényi Differential Privacy following NIST specifications.'},
                      {standard:'ISO 20022',              status:'Native Integration',detail:'Parses pacs.008 and camt.053 XML messages natively in the bank data plane.'},
                      {standard:'SOC 2 Type II',          status:'In Audit',         detail:'Full immutable audit trail logging, HSM keys, and telemetry controls.'},
                    ].map(row => (
                      <div key={row.standard} className="p-4 sm:p-5 rounded-2xl bg-white/2 border border-white/8 space-y-2 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-mono font-bold text-slate-200 truncate">{row.standard}</span>
                          <span className="text-[9.5px] font-mono font-bold text-emerald-400 shrink-0">{row.status}</span>
                        </div>
                        <p className="text-xs text-slate-500 leading-relaxed font-sans">{row.detail}</p>
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 8 — EXPANDED REST API & SDK DOCUMENTATION (#api, #docs)
        ══════════════════════════════════════════════════════════ */}
        <section id="api" className="py-12 sm:py-24 px-3.5 sm:px-6 max-w-7xl mx-auto border-t border-white/6 [content-visibility:auto] [contain-intrinsic-size:1px_600px] w-full min-w-0">
          <div id="docs" className="w-full min-w-0">
            <FadeSection>
              <div className="max-w-3xl mb-6 sm:mb-8 min-w-0">
                <div className="text-[10px] sm:text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Developer & Integration Suite</div>
                <h2 className="text-2xl sm:text-4xl font-bold text-slate-100 tracking-tight">Enterprise REST API & SDK Specs</h2>
                <p className="text-slate-400 text-xs sm:text-base mt-2 sm:mt-3 leading-relaxed">
                  CF-Intelligence provides high-throughput gRPC channels for bank agents and OpenAPI 3.0 REST endpoints for orchestration, compliance audit, and SIEM integrations.
                </p>
              </div>

              {/* Integration Features Banner */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5 sm:gap-4 mb-6 sm:mb-8 w-full min-w-0">
                <div className="p-4 sm:p-5 rounded-2xl bg-indigo-600/10 border border-indigo-500/20 min-w-0">
                  <div className="text-xs font-mono font-bold text-indigo-400 uppercase mb-1">mTLS & API Keys</div>
                  <p className="text-xs text-slate-400 font-sans leading-relaxed">Mutual TLS authentication for all gRPC bank agent channels, with scoped API keys for REST management.</p>
                </div>
                <div className="p-4 sm:p-5 rounded-2xl bg-purple-600/10 border border-purple-500/20 min-w-0">
                  <div className="text-xs font-mono font-bold text-purple-400 uppercase mb-1">WebSocket Telemetry</div>
                  <p className="text-xs text-slate-400 font-sans leading-relaxed">Real-time bi-directional WebSocket event stream for round progress, gradient norms, and risk alerts.</p>
                </div>
                <div className="p-4 sm:p-5 rounded-2xl bg-cyan-600/10 border border-cyan-500/20 min-w-0">
                  <div className="text-xs font-mono font-bold text-cyan-400 uppercase mb-1">SDK & WebHooks</div>
                  <p className="text-xs text-slate-400 font-sans leading-relaxed">Official Python (`cfi-sdk`) and TypeScript (`@cfi/sdk`) packages with automated WebHook alert dispatchers.</p>
                </div>
              </div>

              {/* Interactive Code Console Tabs */}
              <div className="flex gap-1 p-1 bg-white/3 border border-white/8 rounded-2xl w-full sm:w-fit mb-4 overflow-x-auto min-w-0">
                {[{id:'curl',label:'cURL'},{id:'python',label:'Python SDK'},{id:'ts',label:'TypeScript SDK'}].map(tab => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveApiTab(tab.id as 'curl'|'python'|'ts')}
                    className={`px-3.5 sm:px-4 py-1.5 text-xs font-mono rounded-xl transition-all cursor-pointer shrink-0 ${
                      activeApiTab === tab.id ? 'bg-indigo-600 text-white font-bold shadow-[0_0_15px_rgba(99,102,241,0.5)]' : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* Code Display Window */}
              <div className="rounded-2xl sm:rounded-3xl bg-[#03030c] border border-white/8 p-4 sm:p-6 overflow-x-auto mb-8 sm:mb-10 shadow-2xl max-w-full">
                <pre className="text-[11px] sm:text-xs font-mono text-indigo-200/90 leading-relaxed whitespace-pre min-w-0">
                  {activeApiTab === 'curl' && `# Trigger a new federated training round across consortium bank nodes
curl -X POST https://api.cfi-platform.com/v1/rounds \\
  -H "Authorization: Bearer cfi_api_key_991823" \\
  -H "Content-Type: application/json" \\
  -d '{
    "consortium_id": "cfi-prod-001",
    "node_ids": ["jpmorgan-01", "hsbc-02", "deutsche-03"],
    "privacy_config": {
      "target_epsilon": 0.50,
      "target_delta": 1e-5,
      "max_grad_norm": 1.0
    },
    "byzantine_defense": "krum"
  }'`}
                  {activeApiTab === 'python' && `# Install SDK: pip install cfi-sdk
from cfi_sdk import CFIClient

client = CFIClient(
    api_key="cfi_api_key_991823",
    environment="production"
)

# Start federated training round
round_session = client.rounds.start(
    consortium_id="cfi-prod-001",
    node_ids=["jpmorgan-01", "hsbc-02", "deutsche-03"],
    privacy={"target_epsilon": 0.50, "target_delta": 1e-5},
    byzantine_defense="krum"
)

# Stream live telemetry updates
for event in client.rounds.stream_telemetry(round_session.id):
    print(f"Round #{event.round_id} [{event.stage}]: Accuracy={event.accuracy:.3f}")`}
                  {activeApiTab === 'ts' && `// Install SDK: npm install @cfi/sdk
import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({
  apiKey: 'cfi_api_key_991823',
  baseUrl: 'https://api.cfi-platform.com'
});

// Trigger training round
const session = await client.rounds.start({
  consortiumId: 'cfi-prod-001',
  nodeIds: ['jpmorgan-01', 'hsbc-02', 'deutsche-03'],
  privacyConfig: { targetEpsilon: 0.50, targetDelta: 1e-5 },
  byzantineDefense: 'krum',
});

// Listen to WebSocket telemetry
const telemetry = client.telemetry.connect(session.id);
telemetry.on('round.stage', (evt) => {
  console.log(\`Round \${evt.roundId}: Accuracy \${evt.accuracy}%\`);
});`}
                </pre>
              </div>

              {/* Endpoint Detailed Table */}
              <div className="rounded-2xl sm:rounded-3xl border border-white/8 overflow-hidden backdrop-blur-xl w-full min-w-0">
                <div className="px-4 sm:px-6 py-3.5 sm:py-4 bg-white/3 border-b border-white/6 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-mono text-slate-300 font-bold uppercase tracking-wider">REST & WebSocket API Endpoints Reference</span>
                  <span className="text-[9.5px] font-mono text-indigo-400">OpenAPI 3.0 Spec</span>
                </div>
                <div className="divide-y divide-white/5 font-mono text-xs min-w-0">
                  {[
                    { method: 'POST', path: '/v1/rounds',        desc: 'Trigger a new federated training round across authenticated bank nodes', req: 'consortium_id, node_ids, privacy_config', res: 'round_id, status: STARTED' },
                    { method: 'GET',  path: '/v1/rounds/:id',    desc: 'Fetch real-time round metrics, global accuracy, and L2 gradient norms', req: 'round_id (path parameter)', res: 'accuracy, epsilon_used, node_statuses' },
                    { method: 'GET',  path: '/v1/nodes',         desc: 'List active bank node connector statuses, hardware specs, and latencies', req: 'none', res: 'array of BankNode objects' },
                    { method: 'POST', path: '/v1/connectors',    desc: 'Register a new core-banking ISO 20022 message stream connector', req: 'bank_id, xsd_schema_url, mtls_cert', res: 'connector_id, status: ACTIVE' },
                    { method: 'GET',  path: '/v1/reports/sar',   desc: 'Retrieve cryptographically signed FinCEN SAR XML export packages', req: 'risk_threshold (optional)', res: 'FinCEN_SAR XML + SHA-256 signature' },
                    { method: 'WS',   path: '/v1/telemetry',     desc: 'Bi-directional WebSocket streaming live training rounds & risk alerts', req: 'jwt_token', res: 'JSON event telemetry stream' },
                  ].map(row => (
                    <div key={row.path} className="p-4 sm:p-5 hover:bg-white/3 transition-colors space-y-2 w-full min-w-0">
                      <div className="flex flex-wrap items-center justify-between gap-2 min-w-0">
                        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
                          <span className={`px-2 py-0.5 rounded text-[9.5px] font-bold shrink-0 ${
                            row.method === 'GET'  ? 'bg-sky-500/10 text-sky-400 border border-sky-500/20' :
                            row.method === 'POST' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                                                    'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                          }`}>
                            {row.method}
                          </span>
                          <span className="text-indigo-300 font-bold text-xs sm:text-sm break-all">{row.path}</span>
                        </div>
                        <span className="text-[9.5px] text-slate-500 font-mono shrink-0">v1 endpoint</span>
                      </div>
                      <p className="text-xs text-slate-400 font-sans leading-relaxed break-words">{row.desc}</p>
                      <div className="flex flex-wrap gap-3 sm:gap-4 text-[9.5px] sm:text-[10px] text-slate-500 pt-1.5 border-t border-white/4 min-w-0">
                        <div className="truncate"><span className="text-slate-600">Payload Request:</span> <span className="text-slate-300">{row.req}</span></div>
                        <div className="truncate"><span className="text-slate-600">Response Data:</span> <span className="text-emerald-400">{row.res}</span></div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </FadeSection>
          </div>
        </section>

        {/* ── FOOTER ──────────────────────────────────────────────── */}
        <footer className="border-t border-white/6 py-10 sm:py-12 px-3.5 sm:px-6 w-full min-w-0">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-6 min-w-0">
            <div className="flex items-center gap-3 min-w-0">
              <BrandLogo className="w-7 h-7 sm:w-8 sm:h-8 shrink-0" />
              <div className="min-w-0">
                <div className="text-xs sm:text-sm font-bold text-slate-200 truncate">Federated Intelligence Network</div>
                <div className="text-[10.5px] sm:text-xs font-mono text-slate-600 truncate">Privacy-Preserving Fraud Intelligence Platform</div>
              </div>
            </div>

            <div className="text-[10.5px] sm:text-xs font-mono text-slate-500 break-words">
              PyTorch · Intel SGX · ISO 20022 · FinCEN SAR
            </div>

            <button
              onClick={() => navigate('/dashboard')}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 transition-all cursor-pointer shadow-[0_0_20px_rgba(99,102,241,0.4)] w-full sm:w-auto justify-center"
            >
              Open Platform Demo <ArrowRight />
            </button>
          </div>
        </footer>

        {/* ── BANK NODE INSPECTOR DRAWER ──────────────────────────── */}
        <AnimatePresence>
          {activeBankDrawer && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setActiveBankDrawer(null)}
              className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex justify-end w-full max-w-full"
            >
              <motion.div
                initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 28, stiffness: 200 }}
                onClick={e => e.stopPropagation()}
                className="w-full max-w-lg bg-[#070714] border-l border-white/10 p-5 sm:p-7 overflow-y-auto space-y-6 max-w-full"
              >
                <div className="flex items-start justify-between border-b border-white/6 pb-4 sm:pb-5 gap-2">
                  <div className="min-w-0">
                    <span className="text-[9.5px] sm:text-[10px] font-mono text-emerald-400 uppercase tracking-wider">Institution Node Detail</span>
                    <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-0.5 truncate">{activeBankDrawer.name}</h3>
                    <div className="text-xs font-mono text-slate-500 mt-0.5 truncate">{activeBankDrawer.location}</div>
                  </div>
                  <button
                    onClick={() => setActiveBankDrawer(null)}
                    className="px-3 py-1.5 rounded-xl text-xs font-mono bg-white/5 border border-white/10 text-slate-400 hover:text-white transition-colors shrink-0"
                  >
                    Close
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2.5 sm:gap-3 font-mono text-xs min-w-0">
                  {[
                    { k: 'Ticker',   v: activeBankDrawer.ticker },
                    { k: 'Latency',  v: activeBankDrawer.latency },
                    { k: 'Hardware', v: activeBankDrawer.hardware },
                    { k: 'Host RAM', v: activeBankDrawer.ram },
                    { k: 'PyTorch',  v: activeBankDrawer.pytorch },
                    { k: 'Status',   v: 'READY (On-Premises Agent)' },
                  ].map(row => (
                    <div key={row.k} className="p-3 rounded-xl bg-white/3 border border-white/8 min-w-0">
                      <div className="text-slate-500 text-[8.5px] sm:text-[9px] uppercase tracking-wider mb-0.5">{row.k}</div>
                      <div className="text-slate-200 truncate">{row.v}</div>
                    </div>
                  ))}
                </div>

                <div className="min-w-0">
                  <div className="text-[9.5px] sm:text-[10px] font-mono text-slate-500 uppercase tracking-wider mb-2.5">ISO 20022 Stream Log</div>
                  <div className="rounded-2xl bg-[#03030c] border border-white/8 p-3.5 sm:p-4 space-y-2.5 font-mono text-[10.5px] sm:text-[11px] text-slate-400 overflow-x-auto max-w-full">
                    {activeBankDrawer.xmlLogs.map((log, i) => (
                      <div key={i} className="flex items-start gap-2 border-b border-white/5 pb-2 last:border-0 last:pb-0 min-w-0">
                        <span className="text-indigo-400 shrink-0">[{String(i + 1).padStart(2, '0')}]</span>
                        <span className="break-all leading-relaxed">{log}</span>
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
