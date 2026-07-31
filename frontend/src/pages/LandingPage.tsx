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
  { id: 'fl-engine', name: 'Federated Learning Engine', category: 'Core Engine', purpose: 'Orchestrates distributed training rounds across bank nodes using FedAvg with asynchronous straggler tolerance.', algorithm: 'FedAvg, FedProx, asynchronous SGD', inputs: 'Local gradients from bank nodes', outputs: 'Aggregated global model weights', tech: 'PyTorch 2.2, gRPC, Protocol Buffers' },
  { id: 'dp-engine', name: 'Differential Privacy Engine', category: 'Privacy Layer', purpose: 'Applies calibrated Gaussian noise to local gradient updates before transmission, providing provable (ε, δ)-DP guarantees.', algorithm: 'Gaussian Mechanism, RDP Accountant', inputs: 'Raw local gradients, sensitivity bounds', outputs: 'Noise-perturbed gradient tensors', tech: 'Opacus 1.4, Rényi DP' },
  { id: 'secure-agg', name: 'Secure Aggregation', category: 'Cryptography', purpose: 'Aggregates model updates using Paillier homomorphic encryption so no single node ever accesses raw gradients.', algorithm: 'Paillier HE, Shamir Secret Sharing', inputs: 'Encrypted gradient ciphertexts', outputs: 'Homomorphically aggregated ciphertext', tech: 'Intel SGX Enclave v2, python-phe' },
  { id: 'bft-agg', name: 'Byzantine-Robust Aggregation', category: 'BFT Defense', purpose: 'Detects and neutralises gradient poisoning attacks from compromised nodes before global model updates.', algorithm: 'Krum, Trimmed Mean, Flame', inputs: 'Gradient updates from all nodes', outputs: 'Byzantine-filtered aggregated gradient', tech: 'Custom PyTorch, scikit-learn' },
  { id: 'gnn-engine', name: 'Graph Neural Network Engine', category: 'ML Runtime', purpose: 'Builds transaction graphs from ISO 20022 message streams and computes multi-hop structural embeddings.', algorithm: 'GAT (Graph Attention Network), GraphSAGE', inputs: 'ISO 20022 XML pacs.008 / camt.053', outputs: '512-dim node embeddings, risk scores', tech: 'PyTorch Geometric 2.6, DGL' },
  { id: 'risk-engine', name: 'Risk Scoring Engine', category: 'Intelligence', purpose: 'Combines GNN embeddings with tabular features to produce calibrated transaction risk scores with SHAP explanations.', algorithm: 'XGBoost + GNN ensemble, SHAP, LIME', inputs: 'GNN embeddings, transaction features', outputs: 'Risk score [0-1], SHAP attributions', tech: 'XGBoost 2.0, SHAP, Platt Calibration' },
  { id: 'telemetry', name: 'Telemetry & Monitoring', category: 'Observability', purpose: 'Streams real-time training round metrics, privacy budget consumption, and node status to the monitoring plane.', algorithm: 'EWMA smoothing, anomaly detection', inputs: 'Node heartbeats, round metrics', outputs: 'Prometheus metrics, InfluxDB time-series', tech: 'Prometheus, Grafana, OpenTelemetry' },
  { id: 'bank-connector', name: 'Bank Connector Framework', category: 'Integration', purpose: 'Standardises ingestion of ISO 20022 XML financial message streams from heterogeneous core banking systems.', algorithm: 'Schema validation, normalisation pipeline', inputs: 'Raw pacs.008, camt.053 XML streams', outputs: 'Normalised transaction graph tensors', tech: 'Apache Kafka, lxml, xmlschema' },
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

const WORKFLOW_STEPS = [
  { id: 1, short: 'Ingestion', label: 'ISO 20022 Ingestion', description: 'Each bank node ingests raw ISO 20022 XML financial messages (pacs.008 credit transfers, camt.053 statements) via the Bank Connector Framework. Messages are validated against XSD schemas, normalised, and streamed into the local transaction graph store.', tech: ['Apache Kafka', 'lxml', 'xmlschema', 'Protocol Buffers'], code: `# ISO 20022 XML Parser\nfrom lxml import etree\n\nschema = etree.XMLSchema(etree.parse("pacs.008.001.08.xsd"))\nmsg = etree.parse("transaction.xml")\nassert schema.validate(msg)\ngraph_builder.ingest(msg)` },
  { id: 2, short: 'Local Training', label: 'Local GNN Training', description: 'The local PyTorch Geometric GAT model is trained exclusively on data stored within the bank premises. The model learns multi-hop transaction graph embeddings capturing structural patterns associated with money laundering networks without ever transmitting raw data externally.', tech: ['PyTorch Geometric 2.6', 'GATConv', 'GraphSAGE', 'Adam optimiser'], code: `# Local GATConv Training\nfrom torch_geometric.nn import GATConv\n\nmodel = GATConv(in_channels=512,\n                out_channels=256,\n                heads=8, dropout=0.1)\noptimiser = Adam(model.parameters(), lr=3e-4)\n\nfor batch in local_loader:\n    loss = criterion(model(batch.x, batch.edge_index), batch.y)\n    loss.backward()` },
  { id: 3, short: 'Diff. Privacy', label: 'Differential Privacy', description: 'Before any gradient leaves the bank, Opacus injects calibrated Gaussian noise proportional to the gradient L2-sensitivity. The (ε, δ)-DP guarantee is tracked by the Rényi Differential Privacy accountant.', tech: ['Opacus 1.4', 'Gaussian Mechanism', 'RDP Accountant', 'Gradient Clipping'], code: `# Opacus DP Training\nfrom opacus import PrivacyEngine\n\nprivacy_engine = PrivacyEngine()\nmodel, optimiser, loader = privacy_engine.make_private_with_epsilon(\n    module=model,\n    optimizer=optimiser,\n    data_loader=loader,\n    epochs=10, target_epsilon=0.50,\n    target_delta=1e-5, max_grad_norm=1.0,\n)` },
  { id: 4, short: 'Sec. Aggregation', label: 'Secure Aggregation', description: 'Noised gradient tensors are encrypted using Paillier additive homomorphic encryption and transmitted to the Intel SGX hardware enclave. The enclave performs homomorphic summation without decrypting individual bank contributions.', tech: ['Paillier HE', 'Intel SGX v2', 'Shamir Secret Sharing', 'LibTorch C++'], code: `// SGX Enclave Aggregation (C++)\nsgx_status_t ecall_homomorphic_aggregate(\n    sgx_enclave_id_t eid,\n    const uint8_t* cipher_a, size_t len_a,\n    const uint8_t* cipher_b, size_t len_b,\n    uint8_t* cipher_sum, size_t* out_len\n) {\n    auto sum = paillier_add(cipher_a, cipher_b);\n    memcpy(cipher_sum, sum.data(), sum.size());\n}` },
  { id: 5, short: 'BFT Filter', label: 'Byzantine Aggregation', description: 'The FL Coordinator applies Byzantine-robust aggregation using the Krum algorithm and Trimmed Mean to neutralise adversarial gradient poisoning attacks from any compromised bank node before updating the global model.', tech: ['Krum Algorithm', 'Trimmed Mean', 'Flame', 'Cosine similarity'], code: `# Krum Byzantine Filtering\ndef krum(gradients: list[Tensor], f: int) -> Tensor:\n    n = len(gradients)\n    scores = []\n    for i, g_i in enumerate(gradients):\n        dists = sorted(torch.norm(g_i - g_j)**2\n                       for j, g_j in enumerate(gradients) if i != j)\n        scores.append(sum(dists[:n - f - 2]))\n    return gradients[scores.index(min(scores))]` },
  { id: 6, short: 'Global Update', label: 'Global Model Update', description: 'The aggregated and validated global model weights are committed to the Model Registry. All bank nodes receive the updated weights for the next training round. A Coordinator audit log records the cryptographic hash of each round.', tech: ['FedAvg', 'Model Registry', 'SHA-256 audit', 'gRPC broadcast'], code: `# FedAvg Global Aggregation\ndef fedavg(updates: list[dict], weights: list[int]) -> dict:\n    total = sum(weights)\n    global_state = {}\n    for key in updates[0]:\n        global_state[key] = sum(\n            w * u[key] for w, u in zip(weights, updates)\n        ) / total\n    return global_state` },
  { id: 7, short: 'Risk Intelligence', label: 'Risk Scoring', description: 'The global GNN model produces 512-dimensional transaction embeddings passed to the Risk Scoring Engine. XGBoost ensemble classifier scores each transaction [0-1], with SHAP value decomposition providing interpretable feature attributions.', tech: ['XGBoost 2.0', 'SHAP', 'Platt Calibration', 'LIME'], code: `# Risk Score + SHAP Explanation\nimport shap\n\nrisk_model = xgboost.XGBClassifier()\nrisk_score = risk_model.predict_proba(embedding)[0][1]\n\nexplainer = shap.TreeExplainer(risk_model)\nshap_values = explainer.shap_values(embedding)\n# → interpretable feature attributions` },
  { id: 8, short: 'SAR Export', label: 'SAR Export', description: 'Transactions crossing the risk threshold automatically trigger SAR generation in FinCEN-compliant XML format. Reports include SHAP explanations, evidence chains, and are cryptographically signed before export to SIEM.', tech: ['FinCEN SAR XML', 'XMLSec', 'SIEM Integration', 'Splunk HEC'], code: `<!-- FinCEN SAR Export -->\n<FinCEN_SAR version="2.0">\n  <FilingHeader>\n    <FilerID>CFI-PLATFORM-991</FilerID>\n    <FilingType>COMPLETE</FilingType>\n  </FilingHeader>\n  <SuspiciousActivity>\n    <Amount Ccy="USD">1450000.00</Amount>\n    <RiskScore>0.94</RiskScore>\n    <EvidenceHash>sha256:e3b0c...</EvidenceHash>\n  </SuspiciousActivity>\n</FinCEN_SAR>` },
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
function HighEndConsortiumSVG() {
  const [pulseTick, setPulseTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setPulseTick(p => p + 1), 50);
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
    <div className="relative w-full h-[125px] flex items-center justify-center">
      <svg viewBox="0 0 230 120" className="w-full h-full">
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
              {/* Outer Glow Circle */}
              <circle cx={b.x} cy={b.y} r={r * 2.2} fill={`url(#radial-${b.id})`} />

              {/* Pulsing Radar Ring */}
              <circle cx={b.x} cy={b.y} r={radarRadius} fill="none" stroke={b.glow} strokeWidth="0.8" strokeOpacity={opacity} />

              {/* Core Solid Node */}
              <circle cx={b.x} cy={b.y} r={r} fill="#060614" stroke={b.color} strokeWidth="1.8" filter="url(#glow-filter)" />
              <circle cx={b.x} cy={b.y} r={r * 0.35} fill={b.glow} />

              {/* Label */}
              <text x={b.x} y={b.y + r + 10} textAnchor="middle" fontSize="6.5" fill="#94a3b8" fontFamily="monospace" fontWeight="700">
                {b.id}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── 2026 DASHBOARD PREVIEW WIDGET WITH INTERACTIVE SIDEBAR NAV ───────────────
function InteractiveDashboardPreview({ flRound, accuracy }: { flRound: number; accuracy: number }) {
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

  const sidebarButtons = [
    { id: 'home',    symbol: 'C', title: 'FL Consortium Telemetry & Alerts', color: 'bg-indigo-600' },
    { id: 'gnn',     symbol: '⬡', title: 'GNN Graph Node Topology',          color: 'bg-purple-600' },
    { id: 'privacy', symbol: '◎', title: 'Differential Privacy & SGX Vault',  color: 'bg-emerald-600' },
    { id: 'bft',     symbol: '△', title: 'Byzantine Attack Defense Lab',     color: 'bg-cyan-600' },
    { id: 'sar',     symbol: '▦', title: 'FinCEN SAR XML Generator',          color: 'bg-amber-600' },
  ];

  const currentTabInfo = sidebarButtons.find(b => b.id === activeTab)!;

  return (
    <div className="relative rounded-2xl overflow-hidden border border-white/10 shadow-[0_0_90px_rgba(99,102,241,0.2)] bg-[#070718]">
      {/* Browser Chrome Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-[#0b0b20] border-b border-white/8">
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5 shrink-0">
            <div className="w-2.5 h-2.5 rounded-full bg-rose-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-amber-500/60" />
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
          </div>
          <div className="px-3 py-0.5 rounded-md bg-white/4 text-[10px] font-mono text-slate-400 flex items-center gap-2 border border-white/5">
            <span className="text-slate-600">🔒</span>
            cfi-platform.com/dashboard/{activeTab}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-[9px] font-mono font-bold text-emerald-400 uppercase tracking-widest">LIVE</span>
        </div>
      </div>

      {/* Main Body */}
      <div className="flex min-h-[360px]">
        {/* INTERACTIVE SIDEBAR NAV (RED RECTANGLE BUTTONS) */}
        <div className="w-11 shrink-0 border-r border-white/6 bg-[#08081b] py-3 flex flex-col items-center gap-3.5">
          {sidebarButtons.map(btn => (
            <button
              key={btn.id}
              onClick={() => setActiveTab(btn.id as any)}
              title={btn.title}
              className={`w-7 h-7 rounded-xl flex items-center justify-center text-xs font-mono font-bold transition-all cursor-pointer ${
                activeTab === btn.id
                  ? `${btn.color} text-white shadow-[0_0_16px_rgba(99,102,241,0.6)] scale-105`
                  : 'text-slate-500 hover:text-slate-200 hover:bg-white/5'
              }`}
            >
              {btn.symbol}
            </button>
          ))}
        </div>

        {/* DYNAMIC TAB VIEW CONTENT */}
        <div className="flex-1 p-3.5 min-w-0 flex flex-col justify-between">
          <AnimatePresence mode="wait">
            {activeTab === 'home' && (
              <motion.div key="home" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3">
                {/* Stat HUD Cards (Preserves test assertions: Active FL Round, Global Accuracy, Stream Speed) */}
                <div className="grid grid-cols-2 gap-2">
                  <div className="p-2 rounded-xl bg-indigo-600/10 border border-indigo-500/20">
                    <div className="text-[8px] font-mono text-slate-400 uppercase">Active FL Round</div>
                    <div className="text-sm font-bold font-mono text-indigo-400 mt-0.5">#{flRound}</div>
                  </div>
                  <div className="p-2 rounded-xl bg-emerald-600/10 border border-emerald-500/20">
                    <div className="text-[8px] font-mono text-slate-400 uppercase">Global Accuracy</div>
                    <div className="text-sm font-bold font-mono text-emerald-400 mt-0.5">{accuracy}%</div>
                  </div>
                  <div className="p-2 rounded-xl bg-purple-600/10 border border-purple-500/20">
                    <div className="text-[8px] font-mono text-slate-400 uppercase">Privacy Budget</div>
                    <div className="text-sm font-bold font-mono text-purple-400 mt-0.5">ε = 0.50</div>
                  </div>
                  <div className="p-2 rounded-xl bg-cyan-600/10 border border-cyan-500/20">
                    <div className="text-[8px] font-mono text-slate-400 uppercase">Stream Speed</div>
                    <div className="text-sm font-bold font-mono text-cyan-400 mt-0.5">1.4 GB/s</div>
                  </div>
                </div>

                {/* Risk Alert Feed */}
                <div className="rounded-xl border border-white/6 overflow-hidden bg-white/2">
                  <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-white/5 bg-white/3">
                    <span className="text-[9px] font-mono text-slate-400 uppercase tracking-wider">Risk Alert Feed</span>
                    <span className="flex items-center gap-1 text-[9px] font-mono text-emerald-400">
                      <span className="h-1 w-1 rounded-full bg-emerald-400 animate-ping" />streaming
                    </span>
                  </div>
                  <div className="divide-y divide-white/4">
                    {alerts.map((a, i) => (
                      <div key={`${a.id}-${alertTick}`} className="flex items-center gap-2 px-2.5 py-1.5 text-[9px] font-mono">
                        <span className={`w-1 h-3.5 rounded-full shrink-0 ${a.high ? 'bg-rose-500' : 'bg-emerald-500'}`} />
                        <span className="text-slate-400 w-12">{a.bank} TX</span>
                        <span className="text-slate-300 flex-1 truncate">${a.amount}</span>
                        <span className={`font-bold ${a.high ? 'text-rose-400' : 'text-emerald-400'}`}>{a.score.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* 2026 Futuristic High-End Consortium Network */}
                <div className="rounded-xl border border-white/6 bg-white/2 p-2">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[9px] font-mono text-slate-400 uppercase tracking-wider">Consortium Topology</span>
                    <span className="text-[9px] font-mono text-emerald-400">3/3 Nodes Synced</span>
                  </div>
                  <HighEndConsortiumSVG />
                </div>
              </motion.div>
            )}

            {activeTab === 'gnn' && (
              <motion.div key="gnn" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3 text-[10px] font-mono">
                <div className="p-3 rounded-xl bg-purple-600/10 border border-purple-500/20 space-y-1">
                  <div className="text-purple-300 font-bold">GNN Subgraph Inspection</div>
                  <div className="text-slate-400 text-[9px]">Multi-hop transaction graph attention embeddings (PyG 2.6)</div>
                </div>
                <div className="p-3 rounded-xl bg-[#03030c] border border-white/6 space-y-2">
                  <div className="text-slate-500 text-[9px]">GATConv Layer Attentions:</div>
                  <div className="text-purple-300 font-mono text-[9px]">head_0: [0.842, 0.191, 0.428, 0.912]</div>
                  <div className="text-purple-300 font-mono text-[9px]">head_1: [0.311, 0.774, 0.109, 0.881]</div>
                </div>
                <div className="p-3 rounded-xl bg-white/3 border border-white/6">
                  <div className="text-slate-400 font-semibold mb-1">Detected Circular Smurfing Path:</div>
                  <div className="text-rose-400 font-bold">JPM-01 ($1.45M) → HSBC-02 → SGX TEE → DBK-03</div>
                </div>
              </motion.div>
            )}

            {activeTab === 'privacy' && (
              <motion.div key="privacy" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3 text-[10px] font-mono">
                <div className="p-3 rounded-xl bg-emerald-600/10 border border-emerald-500/20 space-y-1">
                  <div className="text-emerald-300 font-bold">Intel SGX & (ε, δ)-DP Engine</div>
                  <div className="text-slate-400 text-[9px]">Opacus RDP Accountant & Paillier Cryptography</div>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[9px]">
                  <div className="p-2.5 rounded-xl bg-[#03030c] border border-white/6">
                    <div className="text-slate-500">Noise Scale (σ)</div>
                    <div className="text-emerald-400 font-bold mt-0.5">0.031 (L2 Clip = 1.0)</div>
                  </div>
                  <div className="p-2.5 rounded-xl bg-[#03030c] border border-white/6">
                    <div className="text-slate-500">SGX Attestation</div>
                    <div className="text-emerald-400 font-bold mt-0.5">VERIFIED BY IAS</div>
                  </div>
                </div>
                <div className="p-2.5 rounded-xl bg-[#03030c] border border-white/6 text-[9px] text-slate-400 truncate">
                  Homomorphic Ciphertext: 0x99F188A2...FE01
                </div>
              </motion.div>
            )}

            {activeTab === 'bft' && (
              <motion.div key="bft" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3 text-[10px] font-mono">
                <div className="p-3 rounded-xl bg-cyan-600/10 border border-cyan-500/20 space-y-1">
                  <div className="text-cyan-300 font-bold">Byzantine Resilience Monitor</div>
                  <div className="text-slate-400 text-[9px]">Krum & Trimmed Mean Poisoning Defense</div>
                </div>
                <div className="space-y-1.5 text-[9px]">
                  <div className="flex items-center justify-between p-2 rounded-lg bg-emerald-600/10 border border-emerald-500/20 text-emerald-400">
                    <span>JPMorgan Node #01</span>
                    <span>100% HONEST</span>
                  </div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-emerald-600/10 border border-emerald-500/20 text-emerald-400">
                    <span>HSBC Node #02</span>
                    <span>100% HONEST</span>
                  </div>
                  <div className="flex items-center justify-between p-2 rounded-lg bg-rose-600/10 border border-rose-500/20 text-rose-400">
                    <span>Adversary Node</span>
                    <span>POISONED (QUENCHED BY KRUM)</span>
                  </div>
                </div>
              </motion.div>
            )}

            {activeTab === 'sar' && (
              <motion.div key="sar" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3 text-[10px] font-mono">
                <div className="p-3 rounded-xl bg-amber-600/10 border border-amber-500/20 space-y-1">
                  <div className="text-amber-300 font-bold">FinCEN SAR Automated Compliance</div>
                  <div className="text-slate-400 text-[9px]">SHAP Feature Attributions & Signed XML Export</div>
                </div>
                <div className="p-2.5 rounded-xl bg-[#03030c] border border-white/6 text-[9px] text-amber-200/90 leading-relaxed font-mono">
                  &lt;FinCEN_SAR&gt;&lt;Amount Ccy="USD"&gt;1450000.00&lt;/Amount&gt;&lt;RiskScore&gt;0.94&lt;/RiskScore&gt;&lt;/FinCEN_SAR&gt;
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="text-[9px] font-mono text-slate-500 text-center border-t border-white/5 pt-2">
            Click sidebar icons ({sidebarButtons.map(b=>b.symbol).join(' · ')}) to inspect engine components
          </div>
        </div>
      </div>
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

  const currentWorkflowStep = WORKFLOW_STEPS.find(s => s.id === activeWorkflowStep)!;

  return (
    <div className="min-h-screen bg-[#05050f] text-slate-300 font-sans antialiased selection:bg-indigo-600 selection:text-white overflow-x-hidden">

      {/* 2026 Ambient Fluid Glow background */}
      <div className="fixed inset-0 pointer-events-none z-0 overflow-hidden">
        <div className="absolute -top-72 -left-48 w-[700px] h-[700px] rounded-full bg-indigo-600/15 blur-[140px]" />
        <div className="absolute top-1/2 -right-72 w-[600px] h-[600px] rounded-full bg-purple-600/10 blur-[150px]" />
        <div className="absolute -bottom-40 left-1/3 w-[500px] h-[500px] rounded-full bg-cyan-600/10 blur-[130px]" />
        <div className="absolute inset-0" style={{ backgroundImage: 'linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)', backgroundSize: '64px 64px' }} />
      </div>

      <div className="relative z-10">

        {/* ── HEADER NAVBAR ────────────────────────────────────────── */}
        <header className="sticky top-0 z-50 h-16 flex items-center border-b border-white/6 bg-[#05050f]/80 backdrop-blur-2xl">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            {/* Brand Logo & Clean Name */}
            <div className="flex items-center gap-2.5 cursor-pointer" onClick={() => navigate('/')}>
              <BrandLogo />
              <span className="font-bold text-base text-slate-100 tracking-tight">CF-Intelligence</span>
              <span className="text-[10px] font-mono text-slate-500/70 font-normal">v2.4.0</span>
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
                  onClick={() => handleNavClick('product')}
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
                      <div className="font-semibold">Engine Modules</div>
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

            <div className="flex items-center gap-3">
              <button
                aria-label="Toggle Navigation Menu"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-xl bg-white/5 border border-white/10 text-slate-300 hover:text-white cursor-pointer transition-colors"
              >
                {isMobileMenuOpen ? <CloseIcon /> : <MenuIcon />}
              </button>
              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:flex items-center gap-2 px-5 py-2 rounded-xl text-[13px] font-semibold text-white bg-gradient-to-r from-indigo-600 via-indigo-500 to-purple-600 hover:opacity-95 transition-all cursor-pointer shadow-[0_0_25px_rgba(99,102,241,0.4)]"
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
              className="lg:hidden fixed inset-x-0 top-16 bottom-0 z-40 bg-[#05050f]/95 backdrop-blur-3xl p-6 overflow-y-auto flex flex-col justify-between border-b border-white/10 shadow-2xl"
            >
              <div className="space-y-6">
                <div className="flex items-center justify-between border-b border-white/8 pb-3">
                  <div className="text-xs font-mono font-bold uppercase tracking-widest text-indigo-400">Navigation Menu</div>
                  <span className="text-[10px] font-mono text-slate-500">CF-Intelligence Platform</span>
                </div>

                {/* Mobile Menu Cards Grid */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {[
                    { label: 'Overview (3D Architecture)',    targetId: 'hero',              badge: 'Overview',      desc: 'System introduction & stats' },
                    { label: 'The Problem & Solution',        targetId: 'problem-solution',  badge: 'Problem',       desc: 'Cross-bank fraud analysis' },
                    { label: 'Streaming GNN Collusion Simulator', targetId: 'how-it-works',  badge: 'Workflow',      desc: '8-stage pipeline runner' },
                    { label: 'Privacy Engine & Capabilities', targetId: 'product',           badge: 'Capabilities',  desc: 'Engine specifications' },
                    { label: 'Deployment Blueprint Wizard',   targetId: 'platform',          badge: 'Consortium',    desc: 'Bank node inspector' },
                    { label: 'System Architecture',           targetId: 'architecture',      badge: 'Architecture',  desc: 'Service layer mesh' },
                    { label: 'Security & Attack Defense Lab', targetId: 'security',          badge: 'Security',      desc: 'DP & trust boundaries' },
                    { label: 'API & Docs',                    targetId: 'api',               badge: 'Developer SDK', desc: 'REST API & TypeScript SDK' },
                  ].map(link => (
                    <a
                      key={link.label}
                      href={`#${link.targetId}`}
                      onClick={(e) => { e.preventDefault(); handleNavClick(link.targetId); }}
                      className="p-4 rounded-2xl bg-white/3 border border-white/8 hover:bg-indigo-600/10 hover:border-indigo-500/30 transition-all cursor-pointer group space-y-1 block"
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-[9px] font-mono font-bold text-indigo-400 bg-indigo-500/10 border border-indigo-500/20 px-2 py-0.5 rounded-md">
                          {link.badge}
                        </span>
                        <ArrowRight />
                      </div>
                      <div className="text-xs font-bold text-slate-200 group-hover:text-white transition-colors">{link.label}</div>
                      <div className="text-[10px] font-mono text-slate-500">{link.desc}</div>
                    </a>
                  ))}
                </div>
              </div>

              {/* Bottom Mobile Action */}
              <div className="pt-6 border-t border-white/8 space-y-3">
                <button
                  onClick={() => { setIsMobileMenuOpen(false); navigate('/dashboard'); }}
                  className="w-full py-3.5 px-6 rounded-2xl text-xs font-bold text-white bg-gradient-to-r from-indigo-600 via-indigo-500 to-purple-600 shadow-[0_0_30px_rgba(99,102,241,0.4)] cursor-pointer flex items-center justify-center gap-2"
                >
                  Launch Live Platform Demo <ArrowRight />
                </button>
                <div className="text-center text-[10px] font-mono text-slate-600">
                  Privacy-Preserving Federated Intelligence Network
                </div>
              </div>
            </motion.nav>
          )}
        </AnimatePresence>

        {/* ══════════════════════════════════════════════════════════
            SECTION 1 — HERO / OVERVIEW
        ══════════════════════════════════════════════════════════ */}
        <section id="hero" className="relative py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">

            {/* Left Hero Content */}
            <motion.div initial={{opacity:0,y:25}} animate={{opacity:1,y:0}} transition={{duration:0.6}} className="lg:col-span-7 space-y-8">
              <div className="space-y-4">
                <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-mono">
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
                  Privacy-Preserving Federated Intelligence Architecture
                </div>
                <h1 className="text-4xl sm:text-6xl font-extrabold tracking-tight leading-[1.1]">
                  <span className="bg-gradient-to-r from-slate-100 via-indigo-200 to-slate-300 bg-clip-text text-transparent">
                    Collaborative Cross-Bank
                  </span>
                  <br />
                  <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 bg-clip-text text-transparent">
                    Fraud Detection Network
                  </span>
                </h1>
                <p className="text-slate-400 text-base leading-relaxed max-w-xl">
                  CF-Intelligence enables banking institutions to train Graph Neural Networks collectively on transaction graph topologies — preserving total data sovereignty with zero raw data sharing.
                </p>
              </div>

              {/* High impact feature highlights */}
              <div className="grid grid-cols-3 gap-3">
                <div className="p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl">
                  <div className="text-2xl font-black font-mono bg-gradient-to-r from-emerald-400 to-teal-400 bg-clip-text text-transparent">94.2%</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-1">Detection Gain</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">vs. 42% isolated</div>
                </div>
                <div className="p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl">
                  <div className="text-2xl font-black font-mono bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">ε = 0.50</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-1">Differential Privacy</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">(ε, δ)-DP bounded</div>
                </div>
                <div className="p-4 rounded-2xl bg-white/3 border border-white/8 backdrop-blur-xl">
                  <div className="text-2xl font-black font-mono bg-gradient-to-r from-cyan-400 to-blue-400 bg-clip-text text-transparent">5× Gain</div>
                  <div className="text-[11px] text-slate-400 font-medium mt-1">FPR Reduction</div>
                  <div className="text-[9px] font-mono text-slate-600 mt-0.5">31% → 6.1% FPR</div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex flex-wrap items-center gap-4">
                <button
                  onClick={() => navigate('/dashboard')}
                  className="flex items-center gap-2.5 px-7 py-3.5 rounded-2xl bg-indigo-600 hover:bg-indigo-500 text-sm font-semibold text-white transition-all cursor-pointer shadow-[0_0_35px_rgba(99,102,241,0.45)] hover:shadow-[0_0_50px_rgba(99,102,241,0.6)]"
                >
                  Launch Live Platform Demo <ArrowRight />
                </button>
                <a
                  href="#architecture"
                  onClick={(e) => { e.preventDefault(); handleNavClick('architecture'); }}
                  className="flex items-center gap-2 px-6 py-3.5 rounded-2xl border border-white/10 hover:border-white/25 text-sm font-medium text-slate-300 hover:text-white transition-all backdrop-blur-lg"
                >
                  Explore System Design
                </a>
              </div>
            </motion.div>

            {/* Right Interactive Dashboard Preview (WITH FULLY INTERACTIVE SIDEBAR BUTTONS) */}
            <motion.div initial={{opacity:0,y:25}} animate={{opacity:1,y:0}} transition={{duration:0.6,delay:0.15}} className="lg:col-span-5">
              <InteractiveDashboardPreview flRound={flRound} accuracy={accuracy} />
            </motion.div>
          </div>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 2 — PROBLEM STATEMENT (#problem-solution)
        ══════════════════════════════════════════════════════════ */}
        <section id="problem-solution" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-10">
              <div className="text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Problem Statement</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">Cross-Bank Financial Crime Networks</h2>
              <p className="text-slate-400 text-base mt-3 leading-relaxed">
                Money laundering networks deliberately divide transaction paths across multiple banks to bypass institution-local AML rules. CFI detects multi-hop graph patterns while keeping all ledger data strictly on-premise.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="p-7 rounded-3xl bg-white/2 border border-rose-500/20 backdrop-blur-xl space-y-4">
                <div className="flex items-center justify-between border-b border-white/6 pb-4">
                  <h3 className="text-base font-bold text-slate-200">Traditional Bank-Isolated AML</h3>
                  <span className="px-3 py-1 rounded-full text-xs font-mono font-bold text-rose-400 bg-rose-500/10 border border-rose-500/20">42% Detection</span>
                </div>
                <ul className="space-y-3 text-xs font-mono text-slate-400">
                  <li className="flex items-start gap-2.5"><span className="text-rose-500 mt-0.5">✕</span> Graph Neural Networks isolated to single institution ledger</li>
                  <li className="flex items-start gap-2.5"><span className="text-rose-500 mt-0.5">✕</span> Smurfing across multiple banks stays hidden below individual thresholds</li>
                  <li className="flex items-start gap-2.5"><span className="text-rose-500 mt-0.5">✕</span> High false positive rate (~31%) overwhelms fraud investigation teams</li>
                </ul>
              </div>

              <div className="p-7 rounded-3xl bg-indigo-600/5 border border-indigo-500/30 backdrop-blur-xl space-y-4 shadow-[inset_0_0_50px_rgba(99,102,241,0.08)]">
                <div className="flex items-center justify-between border-b border-white/6 pb-4">
                  <h3 className="text-base font-bold text-slate-100">CFI Federated Consortium Network</h3>
                  <span className="px-3 py-1 rounded-full text-xs font-mono font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">94.2% Detection</span>
                </div>
                <ul className="space-y-3 text-xs font-mono text-slate-300">
                  <li className="flex items-start gap-2.5"><span className="text-emerald-400 mt-0.5">✓</span> Collaborative model optimization over consortium transaction topology</li>
                  <li className="flex items-start gap-2.5"><span className="text-emerald-400 mt-0.5">✓</span> Zero PII leaves bank perimeter — mathematical (ε=0.50, δ=1e-5)-DP guarantee</li>
                  <li className="flex items-start gap-2.5"><span className="text-emerald-400 mt-0.5">✓</span> False positive rate reduced to 6.1% (5× investigator bandwidth improvement)</li>
                </ul>
              </div>
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 3 — WORKFLOW PIPELINE (#how-it-works)
        ══════════════════════════════════════════════════════════ */}
        <section id="how-it-works" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-10">
              <div className="text-[11px] font-mono font-semibold text-purple-400 uppercase tracking-widest mb-2">Execution Pipeline</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">8-Stage Federated Training Pipeline</h2>
              <p className="text-slate-400 text-base mt-3 leading-relaxed">
                Click any stage below to inspect the production implementation, cryptography, and code references.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
              <div className="lg:col-span-4 space-y-2">
                {WORKFLOW_STEPS.map(step => (
                  <button
                    key={step.id}
                    onClick={() => setActiveWorkflowStep(step.id)}
                    className={`w-full flex items-center gap-3.5 px-4 py-3 rounded-xl border text-left transition-all cursor-pointer ${
                      activeWorkflowStep === step.id
                        ? 'bg-purple-600/15 border-purple-500/40 text-slate-100 shadow-[0_0_20px_rgba(168,85,247,0.2)]'
                        : 'border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/4'
                    }`}
                  >
                    <span className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs font-mono font-bold ${
                      activeWorkflowStep === step.id ? 'bg-purple-600 text-white' : 'bg-white/5 text-slate-500'
                    }`}>
                      {step.id}
                    </span>
                    <span className="text-xs font-semibold">{step.short}</span>
                  </button>
                ))}
              </div>

              <div className="lg:col-span-8">
                <div className="p-6 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-5">
                  <div className="border-b border-white/6 pb-4">
                    <span className="text-[10px] font-mono text-purple-400 uppercase tracking-wider">Stage {currentWorkflowStep.id} of 8</span>
                    <h3 className="text-lg font-bold text-slate-100 mt-1">{currentWorkflowStep.label}</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mt-2">{currentWorkflowStep.description}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {currentWorkflowStep.tech.map(t => (
                      <span key={t} className="px-2.5 py-1 rounded-lg text-[10px] font-mono text-purple-300 bg-purple-600/10 border border-purple-500/20">{t}</span>
                    ))}
                  </div>
                  <div className="rounded-2xl bg-[#03030c] border border-white/8 p-4 font-mono text-xs text-purple-200/90 overflow-x-auto leading-relaxed">
                    <pre>{currentWorkflowStep.code}</pre>
                  </div>
                </div>
              </div>
            </div>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 4 — ENGINE CAPABILITIES (#product)
        ══════════════════════════════════════════════════════════ */}
        <section id="product" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-10">
              <div className="text-[11px] font-mono font-semibold text-cyan-400 uppercase tracking-widest mb-2">Engine Specifications</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">Platform Engineering Capabilities</h2>
              <p className="text-slate-400 text-base mt-3 leading-relaxed">
                Inspect platform component specifications, algorithms, input/output tensors, and stack requirements.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
              <div className="lg:col-span-4 space-y-2">
                {PLATFORM_MODULES.map(mod => (
                  <button
                    key={mod.id}
                    onClick={() => setActiveModule(mod)}
                    className={`w-full flex items-center justify-between px-4 py-3 rounded-xl border text-left transition-all cursor-pointer ${
                      activeModule?.id === mod.id
                        ? 'bg-cyan-600/15 border-cyan-500/40 text-slate-100 shadow-[0_0_20px_rgba(6,182,212,0.2)]'
                        : 'border-transparent text-slate-500 hover:text-slate-300 hover:bg-white/4'
                    }`}
                  >
                    <div>
                      <div className="text-xs font-semibold">{mod.name}</div>
                      <div className="text-[10px] font-mono text-slate-600">{mod.category}</div>
                    </div>
                  </button>
                ))}
              </div>

              {activeModule && (
                <div className="lg:col-span-8 p-6 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-5">
                  <div className="border-b border-white/6 pb-4">
                    <span className="text-[10px] font-mono text-cyan-400 uppercase tracking-wider">{activeModule.category}</span>
                    <h3 className="text-lg font-bold text-slate-100 mt-1">{activeModule.name}</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mt-2">{activeModule.purpose}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                    {[
                      { label: 'Algorithm',  value: activeModule.algorithm },
                      { label: 'Technology', value: activeModule.tech },
                      { label: 'Inputs',     value: activeModule.inputs },
                      { label: 'Outputs',    value: activeModule.outputs },
                    ].map(row => (
                      <div key={row.label} className="p-3.5 rounded-xl bg-[#03030c] border border-white/6">
                        <div className="text-slate-500 text-[9px] uppercase tracking-wider mb-1">{row.label}</div>
                        <div className="text-slate-200">{row.value}</div>
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
        <section id="platform" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-10">
              <div className="text-[11px] font-mono font-semibold text-emerald-400 uppercase tracking-widest mb-2">Consortium Architecture</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">Active Bank Node Inspector</h2>
              <p className="text-slate-400 text-base mt-3 leading-relaxed">
                Click any institution node to inspect hardware specs, PyTorch execution runtime, and ISO 20022 message streams.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
              {Object.values(BANK_NODES).map((bank, i) => (
                <motion.div
                  key={bank.id}
                  initial={{opacity:0,y:15}} animate={{opacity:1,y:0}} transition={{delay:i*0.08}}
                  whileHover={{y:-4}}
                  onClick={() => setActiveBankDrawer(bank)}
                  className="p-5 rounded-2xl bg-white/2 border border-white/8 hover:border-emerald-500/40 hover:shadow-[0_0_30px_rgba(16,185,129,0.15)] cursor-pointer transition-all space-y-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20">READY</span>
                    <span className="text-[10px] font-mono text-slate-500">{bank.latency}</span>
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-slate-200">{bank.name}</h3>
                    <div className="text-[10px] font-mono text-slate-600 mt-0.5">{bank.ticker}</div>
                  </div>
                  <div className="text-[11px] font-mono text-slate-500 border-t border-white/6 pt-2.5 leading-snug">
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
        <section id="architecture" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-10">
              <div className="text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Service Mesh Design</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">System Service Topology</h2>
              <p className="text-slate-400 text-base mt-3 leading-relaxed">
                Click any service layer node to inspect internal responsibilities, communication protocols, and technology stack.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
              <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {ARCH_NODES.map(node => (
                  <button
                    key={node.id}
                    onClick={() => setActiveArchNode(node)}
                    className={`p-4 rounded-xl border text-left transition-all cursor-pointer ${
                      activeArchNode?.id === node.id
                        ? 'bg-indigo-600/15 border-indigo-500/40 text-slate-100 shadow-[0_0_20px_rgba(99,102,241,0.2)]'
                        : 'bg-white/2 border-white/7 hover:border-white/15'
                    }`}
                  >
                    <div className="text-xs font-semibold mb-1">{node.label}</div>
                    <div className="text-[10px] font-mono text-slate-600 leading-snug">{node.tech.slice(0, 2).join(' · ')}</div>
                  </button>
                ))}
              </div>

              {activeArchNode && (
                <div className="lg:col-span-7 p-6 rounded-3xl bg-white/2 border border-white/8 backdrop-blur-xl space-y-5">
                  <div className="border-b border-white/6 pb-4">
                    <span className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider">Service Node</span>
                    <h3 className="text-lg font-bold text-slate-100 mt-1">{activeArchNode.label}</h3>
                    <p className="text-xs text-slate-400 leading-relaxed mt-2">{activeArchNode.description}</p>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs font-mono">
                    {[
                      { label: 'Responsibilities', items: activeArchNode.responsibilities, dot: 'text-indigo-400' },
                      { label: 'Protocols',        items: activeArchNode.protocols,       dot: 'text-purple-400' },
                      { label: 'Technology',       items: activeArchNode.tech,            dot: 'text-cyan-400' },
                    ].map(col => (
                      <div key={col.label}>
                        <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-2">{col.label}</div>
                        <ul className="space-y-1.5">
                          {col.items.map(it => (
                            <li key={it} className="text-slate-300 flex items-center gap-1.5"><span className={col.dot}>•</span>{it}</li>
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
        <section id="security" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <FadeSection>
            <div className="max-w-3xl mb-8">
              <div className="text-[11px] font-mono font-semibold text-purple-400 uppercase tracking-widest mb-2">Cryptographic Boundaries</div>
              <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">Privacy & Trust Boundary Model</h2>
            </div>

            <div className="flex gap-1.5 p-1.5 bg-white/3 border border-white/8 rounded-2xl w-fit mb-8">
              {[{id:'flow',label:'Data Flow'},{id:'threat',label:'Threat Model'},{id:'compliance',label:'Compliance'}].map(tab=>(
                <button
                  key={tab.id}
                  onClick={() => setActivePrivacyTab(tab.id as 'flow'|'threat'|'compliance')}
                  className={`px-5 py-2 text-xs font-semibold rounded-xl transition-all cursor-pointer ${
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
              <motion.div key={activePrivacyTab} initial={{opacity:0,y:8}} animate={{opacity:1,y:0}} exit={{opacity:0,y:-8}} transition={{duration:0.2}}>
                {activePrivacyTab === 'flow' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {[
                      {title:'Inside Bank Perimeter',items:['Raw transaction ledgers','Customer PII & Identity','Account balance histories','Local GNN graph embeddings'],note:'← Strict non-export policy',noteColor:'text-rose-400'},
                      {title:'Transmitted Tensors (DP)',items:['DP Gaussian-noised gradients','Paillier homomorphic ciphertexts','Round participation tokens','HSM-signed attestations'],note:'← (ε=0.50, δ=1e-5)-DP guarantee',noteColor:'text-emerald-400'},
                      {title:'SGX TEE Enclave Node',items:['HE encrypted sum aggregation','Intel IAS attestation quotes','Isolated enclave memory pages','No external network access'],note:'← Hardware cryptographic vault',noteColor:'text-purple-400'},
                    ].map(col => (
                      <div key={col.title} className="p-6 rounded-3xl bg-white/2 border border-white/8 space-y-4">
                        <div className="text-xs font-mono font-bold text-slate-300 uppercase tracking-wider">{col.title}</div>
                        <div className="space-y-2 font-mono text-xs text-slate-400">
                          {col.items.map(item => (
                            <div key={item} className="p-2.5 rounded-xl bg-[#03030c] border border-white/6">{item}</div>
                          ))}
                        </div>
                        <div className={`text-[10px] font-mono ${col.noteColor}`}>{col.note}</div>
                      </div>
                    ))}
                  </div>
                )}

                {activePrivacyTab === 'threat' && (
                  <div className="space-y-3">
                    {[
                      {threat:'Gradient Inversion Attack',    mitigation:'Gaussian DP noise (σ calibrated to ε=0.50) makes gradient inversion mathematically infeasible. Clipping bound C=1.0.'},
                      {threat:'Byzantine Gradient Poisoning', mitigation:'Krum + Trimmed Mean Byzantine-robust aggregation neutralises up to f < n/2 adversarial bank nodes per round.'},
                      {threat:'Coordinator Compromise',       mitigation:'Intel SGX TEE handles aggregation. Central coordinator sees ciphertexts only; plaintext updates never exist outside enclave.'},
                      {threat:'Membership Inference',         mitigation:'RDP accountant bounds cumulative budget. Training halts if ε limit is reached. Per-sample clipping prevents memorisation.'},
                      {threat:'Model Extraction',             mitigation:'Global model weights are distributed exclusively to authenticated bank nodes via mutual TLS (mTLS).' },
                    ].map(row => (
                      <div key={row.threat} className="flex items-start gap-5 p-5 rounded-2xl bg-white/2 border border-white/8">
                        <div className="w-48 shrink-0">
                          <div className="text-xs font-bold text-rose-400">{row.threat}</div>
                          <div className="text-[10px] font-mono text-emerald-400 mt-0.5">Mitigated</div>
                        </div>
                        <div className="text-xs text-slate-400 font-mono leading-relaxed">{row.mitigation}</div>
                      </div>
                    ))}
                  </div>
                )}

                {activePrivacyTab === 'compliance' && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                    {[
                      {standard:'GDPR Article 25',        status:'Privacy by Design',detail:'DP guarantees built into tensor aggregation. Zero customer PII ever exits bank boundary.'},
                      {standard:'FinCEN SAR Regulation',  status:'Compliant',        detail:'Automated SAR XML filing generation with cryptographic evidence sign-off.'},
                      {standard:'EU AML Directive 6AMLD', status:'Compliant',        detail:'Detects cross-border money laundering networks without cross-border data transfer.'},
                      {standard:'NIST SP 800-188',        status:'Aligned',          detail:'Strict de-identification via Rényi Differential Privacy following NIST specifications.'},
                      {standard:'ISO 20022',              status:'Native Integration',detail:'Parses pacs.008 and camt.053 XML messages natively in the bank data plane.'},
                      {standard:'SOC 2 Type II',          status:'In Audit',         detail:'Full immutable audit trail logging, HSM keys, and telemetry controls.'},
                    ].map(row => (
                      <div key={row.standard} className="p-5 rounded-2xl bg-white/2 border border-white/8 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-mono font-bold text-slate-200">{row.standard}</span>
                          <span className="text-[10px] font-mono font-bold text-emerald-400">{row.status}</span>
                        </div>
                        <p className="text-xs text-slate-500 leading-relaxed">{row.detail}</p>
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </FadeSection>
        </section>

        {/* ══════════════════════════════════════════════════════════
            SECTION 8 — REST API & SDK (#api, #docs)
        ══════════════════════════════════════════════════════════ */}
        <section id="api" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-white/6">
          <div id="docs">
            <FadeSection>
              <div className="max-w-3xl mb-8">
                <div className="text-[11px] font-mono font-semibold text-indigo-400 uppercase tracking-widest mb-2">Developer Integration</div>
                <h2 className="text-3xl sm:text-4xl font-bold text-slate-100 tracking-tight">REST API & Bank Connector SDK</h2>
                <p className="text-slate-400 text-base mt-3 leading-relaxed">
                  FastAPI REST endpoints for round scheduling, WebSocket streams for live telemetry, and python SDK for connector agents.
                </p>
              </div>

              <div className="flex gap-1.5 p-1.5 bg-white/3 border border-white/8 rounded-2xl w-fit mb-6">
                {[{id:'curl',label:'cURL'},{id:'python',label:'Python'},{id:'ts',label:'TypeScript'}].map(tab => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveApiTab(tab.id as 'curl'|'python'|'ts')}
                    className={`px-4 py-1.5 text-xs font-mono rounded-xl transition-all cursor-pointer ${
                      activeApiTab === tab.id ? 'bg-slate-700 text-white font-bold' : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <div className="rounded-3xl bg-[#03030c] border border-white/8 p-6 overflow-x-auto mb-8 shadow-2xl">
                <pre className="text-xs font-mono text-indigo-200/90 leading-relaxed whitespace-pre">
                  {activeApiTab === 'curl' && `# Trigger a new federated training round across consortium
curl -X POST https://api.cfi-platform.com/v1/rounds \\
  -H "Authorization: Bearer cfi_api_key_991823" \\
  -H "Content-Type: application/json" \\
  -d '{
    "consortium_id": "cfi-prod-001",
    "node_ids": ["jpmorgan-01", "hsbc-02", "deutsche-03"],
    "privacy_config": {"epsilon": 0.50, "delta": 1e-5},
    "aggregation": "krum"
  }'`}
                  {activeApiTab === 'python' && `from cfi_sdk import CFIClient

client = CFIClient(api_key="cfi_api_key_991823")

round_ = client.rounds.start(
    consortium_id="cfi-prod-001",
    node_ids=["jpmorgan-01", "hsbc-02", "deutsche-03"],
    privacy={"epsilon": 0.50, "delta": 1e-5},
)

for event in client.rounds.stream(round_.id):
    print(f"Round {event.round_id}: {event.stage} — {event.accuracy:.3f}")`}
                  {activeApiTab === 'ts' && `import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({ apiKey: 'cfi_api_key_991823' });

const round = await client.rounds.start({
  consortiumId: 'cfi-prod-001',
  nodeIds: ['jpmorgan-01', 'hsbc-02', 'deutsche-03'],
  privacyConfig: { epsilon: 0.50, delta: 1e-5 },
});

const ws = client.telemetry.subscribe(round.id);
ws.on('round.stage', (e) => console.log(e.stage, e.accuracy));`}
                </pre>
              </div>

              {/* Endpoint Table */}
              <div className="rounded-3xl border border-white/8 overflow-hidden backdrop-blur-xl">
                <div className="px-6 py-4 bg-white/2 border-b border-white/6">
                  <span className="text-xs font-mono text-slate-400 font-semibold">API Endpoint Reference — v1</span>
                </div>
                <table className="w-full text-xs font-mono">
                  <tbody>
                    {[
                      { method: 'POST', path: '/v1/rounds',        desc: 'Trigger a new federated learning round' },
                      { method: 'GET',  path: '/v1/rounds/:id',    desc: 'Get round status, accuracy and gradient norms' },
                      { method: 'GET',  path: '/v1/nodes',         desc: 'List active bank node connector statuses' },
                      { method: 'POST', path: '/v1/connectors',    desc: 'Register new bank node connector' },
                      { method: 'GET',  path: '/v1/reports/sar',   desc: 'Retrieve FinCEN SAR XML export packages' },
                      { method: 'WS',   path: '/v1/telemetry',     desc: 'Stream real-time round metrics via WebSocket' },
                    ].map(row => (
                      <tr key={row.path} className="border-b border-white/5 hover:bg-white/3 transition-colors">
                        <td className="px-6 py-3.5 w-20">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                            row.method === 'GET'  ? 'bg-sky-500/10 text-sky-400 border border-sky-500/20' :
                            row.method === 'POST' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                                                    'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                          }`}>
                            {row.method}
                          </span>
                        </td>
                        <td className="px-6 py-3.5 text-indigo-300 font-semibold">{row.path}</td>
                        <td className="px-6 py-3.5 text-slate-500">{row.desc}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </FadeSection>
          </div>
        </section>

        {/* ── FOOTER ──────────────────────────────────────────────── */}
        <footer className="border-t border-white/6 py-12 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <BrandLogo className="w-8 h-8" />
              <div>
                <div className="text-sm font-bold text-slate-200">Federated Intelligence Network</div>
                <div className="text-xs font-mono text-slate-600">Privacy-Preserving Fraud Intelligence Platform</div>
              </div>
            </div>

            <div className="text-xs font-mono text-slate-500">
              PyTorch · Intel SGX · ISO 20022 · FinCEN SAR
            </div>

            <button
              onClick={() => navigate('/dashboard')}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 transition-all cursor-pointer shadow-[0_0_20px_rgba(99,102,241,0.4)]"
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
              className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex justify-end"
            >
              <motion.div
                initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 28, stiffness: 200 }}
                onClick={e => e.stopPropagation()}
                className="w-full max-w-lg bg-[#070714] border-l border-white/10 p-7 overflow-y-auto space-y-6"
              >
                <div className="flex items-start justify-between border-b border-white/6 pb-5">
                  <div>
                    <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-wider">Institution Node Detail</span>
                    <h3 className="text-lg font-bold text-slate-100 mt-1">{activeBankDrawer.name}</h3>
                    <div className="text-xs font-mono text-slate-500 mt-0.5">{activeBankDrawer.location}</div>
                  </div>
                  <button
                    onClick={() => setActiveBankDrawer(null)}
                    className="px-3.5 py-1.5 rounded-xl text-xs font-mono bg-white/5 border border-white/10 text-slate-400 hover:text-white transition-colors"
                  >
                    Close
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3 font-mono text-xs">
                  {[
                    { k: 'Ticker',   v: activeBankDrawer.ticker },
                    { k: 'Latency',  v: activeBankDrawer.latency },
                    { k: 'Hardware', v: activeBankDrawer.hardware },
                    { k: 'Host RAM', v: activeBankDrawer.ram },
                    { k: 'PyTorch',  v: activeBankDrawer.pytorch },
                    { k: 'Status',   v: 'READY — On-Prem Agent' },
                  ].map(row => (
                    <div key={row.k} className="p-3.5 rounded-xl bg-white/3 border border-white/8">
                      <div className="text-slate-500 text-[9px] uppercase tracking-wider mb-1">{row.k}</div>
                      <div className="text-slate-200 truncate">{row.v}</div>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider mb-3">ISO 20022 Stream Log</div>
                  <div className="rounded-2xl bg-[#03030c] border border-white/8 p-4 space-y-3 font-mono text-[11px] text-slate-400 overflow-x-auto">
                    {activeBankDrawer.xmlLogs.map((log, i) => (
                      <div key={i} className="flex items-start gap-2.5 border-b border-white/5 pb-2.5 last:border-0 last:pb-0">
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
