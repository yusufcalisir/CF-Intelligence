import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// ── TYPE DEFINITIONS ────────────────────────────────────────────────────────
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

interface Module {
  id: string;
  name: string;
  category: string;
  purpose: string;
  algorithm: string;
  inputs: string;
  outputs: string;
  tech: string;
}

interface ArchNode {
  id: string;
  label: string;
  description: string;
  tech: string[];
  responsibilities: string[];
  protocols: string[];
}

// ── DATA ────────────────────────────────────────────────────────────────────
const BANK_NODES: Record<string, BankInfoDetail> = {
  jpmorgan: {
    id: 'jpmorgan', name: 'JPMorgan Chase & Co.', ticker: 'NYSE: JPM',
    location: 'New York Data Center, US (Node #01)',
    hardware: 'NVIDIA DGX H100 (8× Tensor Core GPUs)', ram: '128 GB Host RAM',
    pytorch: '2.2.0+cu121', latency: '1.2 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'GATConv (in=512, heads=8, out=256) embedding computed in 14.2ms.',
      'DP Gaussian noise σ=0.031 injected. ε=0.50, δ=1e-5. HSM-signed: 0x99F1.',
    ],
  },
  hsbc: {
    id: 'hsbc', name: 'HSBC Holdings plc', ticker: 'LSE: HSBA',
    location: 'London Canary Wharf, UK (Node #02)',
    hardware: 'Dell PowerEdge R760 (4× NVIDIA A100 GPUs)', ram: '64 GB Host RAM',
    pytorch: '2.1.2+cu118', latency: '1.8 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt><Id>HSBC-GBP-8812</Id></Stmt></BkToCstmrStmt></Document>',
      'Subgraph feature extraction complete. 12,840 nodes, 47,291 edges ingested.',
      'Paillier ciphertext [[W_hsbc]] emitted. Ready for secure aggregation.',
    ],
  },
  deutsche: {
    id: 'deutsche', name: 'Deutsche Bank AG', ticker: 'XETRA: DBK',
    location: 'Frankfurt, DE (Node #03)',
    hardware: 'Intel Xeon Platinum (CPU Monolith)', ram: '32 GB Host RAM',
    pytorch: '2.1.2+cpu', latency: '2.9 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>DBK-2026-7734</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="EUR">650000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'Heterogeneous negotiator: batch_size scaled to 32. grad_accum_steps=2.',
      'CPU threadpool straggler quenched. Round latency 342ms.',
    ],
  },
  sgx: {
    id: 'sgx', name: 'Intel SGX Hardware TEE Enclave', ticker: 'HARDWARE TEE',
    location: 'Consortium Secure Vault Node',
    hardware: 'Intel SGX Enclave v2 (Hardware Isolation)', ram: '256 GB Enclave Page Cache (EPC)',
    pytorch: 'C++ Native LibTorch Enclave Runtime', latency: '0.2 ms',
    xmlLogs: [
      'Remote Attestation Quote verified by Intel IAS. Status: SUCCESS.',
      'Homomorphic Sum: [[W_global]] = Σ([[W_jpm]], [[W_hsbc]], [[W_db]])',
      'DP Gaussian noise injected (ε=0.50, δ=1e-5). [[W_global]] published to consortium.',
    ],
  },
};

const PLATFORM_MODULES: Module[] = [
  { id: 'fl-engine', name: 'Federated Learning Engine', category: 'Core Engine', purpose: 'Orchestrates distributed training rounds across heterogeneous bank nodes using FedAvg with asynchronous straggler tolerance.', algorithm: 'FedAvg, FedProx, asynchronous SGD', inputs: 'Local gradients from bank nodes', outputs: 'Aggregated global model weights', tech: 'PyTorch 2.2, gRPC, Protocol Buffers' },
  { id: 'dp-engine', name: 'Differential Privacy Engine', category: 'Privacy Layer', purpose: 'Applies calibrated Gaussian noise to local gradient updates before they leave bank premises, providing (ε, δ)-DP guarantees.', algorithm: 'Gaussian Mechanism, RDP Accountant', inputs: 'Raw local gradients, sensitivity bounds', outputs: 'Noise-perturbed gradient tensors', tech: 'Opacus 1.4, Rényi DP' },
  { id: 'secure-agg', name: 'Secure Aggregation', category: 'Cryptography', purpose: 'Aggregates model updates using Paillier homomorphic encryption so the coordinator never sees individual institution gradients in plaintext.', algorithm: 'Paillier HE, Shamir Secret Sharing', inputs: 'Encrypted gradient ciphertexts', outputs: 'Homomorphically aggregated ciphertext', tech: 'Intel SGX Enclave v2, python-phe' },
  { id: 'bft-agg', name: 'Byzantine-Robust Aggregation', category: 'BFT Defense', purpose: 'Detects and neutralises gradient poisoning attacks from compromised bank nodes before global model update.', algorithm: 'Krum, Trimmed Mean, Flame', inputs: 'Set of gradient updates from all nodes', outputs: 'Byzantine-filtered aggregated gradient', tech: 'Custom PyTorch, scikit-learn' },
  { id: 'gnn-engine', name: 'Graph Neural Network Engine', category: 'ML Runtime', purpose: 'Builds heterogeneous transaction graphs from ISO 20022 feeds and computes multi-hop structural embeddings for fraud pattern detection.', algorithm: 'GAT (Graph Attention Network), GraphSAGE', inputs: 'ISO 20022 XML pacs.008 / camt.053', outputs: '512-dim node embeddings, risk scores', tech: 'PyTorch Geometric 2.6, DGL' },
  { id: 'risk-engine', name: 'Risk Scoring Engine', category: 'Intelligence', purpose: 'Combines GNN embeddings with tabular features and velocity metrics to produce calibrated transaction risk scores with SHAP explanations.', algorithm: 'XGBoost + GNN ensemble, SHAP, LIME', inputs: 'GNN embeddings, transaction features', outputs: 'Risk score [0-1], SHAP attributions', tech: 'XGBoost 2.0, SHAP, Platt Calibration' },
  { id: 'telemetry', name: 'Telemetry & Monitoring', category: 'Observability', purpose: 'Streams real-time FL round metrics, gradient norms, privacy budget consumption, and node health to the Coordinator dashboard.', algorithm: 'EWMA smoothing, anomaly detection', inputs: 'Node heartbeats, round metrics', outputs: 'Prometheus metrics, InfluxDB time-series', tech: 'Prometheus, Grafana, OpenTelemetry' },
  { id: 'bank-connector', name: 'Bank Connector Framework', category: 'Integration', purpose: 'Standardises ingestion of ISO 20022 XML financial message streams from heterogeneous bank core banking systems into the FL data plane.', algorithm: 'Schema validation, normalisation pipeline', inputs: 'Raw pacs.008, camt.053 XML streams', outputs: 'Normalised transaction graph tensors', tech: 'Apache Kafka, lxml, xmlschema' },
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
  { id: 1, label: 'ISO 20022 Ingestion', short: 'Ingestion', description: 'Each bank node ingests raw ISO 20022 XML financial messages (pacs.008 credit transfers, camt.053 statements) via the Bank Connector Framework. Messages are validated against XSD schemas, normalised, and streamed into the local transaction graph store.', tech: ['Apache Kafka', 'lxml', 'xmlschema', 'Protocol Buffers'], code: `# ISO 20022 XML Parser\nfrom lxml import etree\n\nschema = etree.XMLSchema(etree.parse("pacs.008.001.08.xsd"))\nmsg = etree.parse("transaction.xml")\nassert schema.validate(msg)\ngraph_builder.ingest(msg)` },
  { id: 2, label: 'Local GNN Training', short: 'Local Training', description: 'The local PyTorch Geometric GAT model is trained exclusively on data stored within the bank premises. The model learns multi-hop transaction graph embeddings capturing structural patterns associated with money laundering networks without ever transmitting raw data externally.', tech: ['PyTorch Geometric 2.6', 'GATConv', 'GraphSAGE', 'Adam optimiser'], code: `# Local GATConv Training\nfrom torch_geometric.nn import GATConv\n\nmodel = GATConv(in_channels=512,\n                out_channels=256,\n                heads=8, dropout=0.1)\noptimiser = Adam(model.parameters(), lr=3e-4)\n\nfor batch in local_loader:\n    loss = criterion(model(batch.x, batch.edge_index), batch.y)\n    loss.backward()` },
  { id: 3, label: 'Differential Privacy', short: 'Diff. Privacy', description: 'Before any gradient leaves the bank, Opacus injects calibrated Gaussian noise proportional to the gradient L2-sensitivity. The (ε, δ)-DP guarantee is tracked by the Rényi Differential Privacy accountant. Only after budget verification is the noised gradient forwarded.', tech: ['Opacus 1.4', 'Gaussian Mechanism', 'RDP Accountant', 'Gradient Clipping'], code: `# Opacus DP Training\nfrom opacus import PrivacyEngine\n\nprivacy_engine = PrivacyEngine()\nmodel, optimiser, loader = privacy_engine.make_private_with_epsilon(\n    module=model,\n    optimizer=optimiser,\n    data_loader=loader,\n    epochs=10, target_epsilon=0.50,\n    target_delta=1e-5, max_grad_norm=1.0,\n)` },
  { id: 4, label: 'Secure Aggregation', short: 'Sec. Aggregation', description: 'Noised gradient tensors are encrypted using Paillier additive homomorphic encryption and transmitted to the Intel SGX hardware enclave. The enclave performs homomorphic summation without decrypting individual bank contributions, providing cryptographic isolation guarantees.', tech: ['Paillier HE', 'Intel SGX v2', 'Shamir Secret Sharing', 'LibTorch C++'], code: `// SGX Enclave Aggregation (C++)\nsgx_status_t ecall_homomorphic_aggregate(\n    sgx_enclave_id_t eid,\n    const uint8_t* cipher_a, size_t len_a,\n    const uint8_t* cipher_b, size_t len_b,\n    uint8_t* cipher_sum, size_t* out_len\n) {\n    auto sum = paillier_add(cipher_a, cipher_b);\n    memcpy(cipher_sum, sum.data(), sum.size());\n}` },
  { id: 5, label: 'Byzantine Aggregation', short: 'BFT Filter', description: 'The FL Coordinator applies Byzantine-robust aggregation using the Krum algorithm and Trimmed Mean to neutralise adversarial gradient poisoning attacks from any compromised bank node before updating the global model.', tech: ['Krum Algorithm', 'Trimmed Mean', 'Flame', 'Cosine similarity'], code: `# Krum Byzantine Filtering\ndef krum(gradients: list[Tensor], f: int) -> Tensor:\n    """Select gradient maximally close to all others, excluding f adversaries.\"\"\"\n    n = len(gradients)\n    scores = []\n    for i, g_i in enumerate(gradients):\n        dists = sorted(torch.norm(g_i - g_j)**2 for j, g_j in enumerate(gradients) if i != j)\n        scores.append(sum(dists[:n - f - 2]))\n    return gradients[scores.index(min(scores))]` },
  { id: 6, label: 'Global Model Update', short: 'Global Update', description: 'The aggregated and validated global model weights are committed to the Model Registry. All bank nodes receive the updated weights for the next training round. A Coordinator audit log records the cryptographic hash of each round\'s aggregated model.', tech: ['FedAvg', 'Model Registry', 'SHA-256 audit', 'gRPC broadcast'], code: `# FedAvg Global Aggregation\ndef fedavg(updates: list[dict], weights: list[int]) -> dict:\n    total = sum(weights)\n    global_state = {}\n    for key in updates[0]:\n        global_state[key] = sum(\n            w * u[key] for w, u in zip(weights, updates)\n        ) / total\n    return global_state` },
  { id: 7, label: 'Risk Scoring', short: 'Risk Intelligence', description: 'The global GNN model produces 512-dimensional transaction embeddings which are passed to the Risk Scoring Engine. XGBoost ensemble classifier scores each transaction [0-1], with SHAP value decomposition providing interpretable feature attributions for each risk decision.', tech: ['XGBoost 2.0', 'SHAP', 'Platt Calibration', 'LIME'], code: `# Risk Score + SHAP Explanation\nimport shap\n\nrisk_model = xgboost.XGBClassifier()\nrisk_score = risk_model.predict_proba(embedding)[0][1]\n\nexplainer = shap.TreeExplainer(risk_model)\nshap_values = explainer.shap_values(embedding)\n# → interpretable feature attributions` },
  { id: 8, label: 'SAR Export', short: 'SAR Export', description: 'Transactions crossing the configured risk threshold automatically trigger SAR (Suspicious Activity Report) generation in FinCEN-compliant XML format. Reports include SHAP explanations, evidence chains, and are cryptographically signed before export to SIEM or regulatory submission.', tech: ['FinCEN SAR XML', 'XMLSec', 'SIEM Integration', 'Splunk HEC'], code: `<!-- FinCEN SAR Export -->\n<FinCEN_SAR version="2.0">\n  <FilingHeader>\n    <FilerID>CFI-PLATFORM-991</FilerID>\n    <FilingType>COMPLETE</FilingType>\n  </FilingHeader>\n  <SuspiciousActivity>\n    <Amount Ccy="USD">1450000.00</Amount>\n    <RiskScore>0.94</RiskScore>\n    <EvidenceHash>sha256:e3b0c...</EvidenceHash>\n  </SuspiciousActivity>\n</FinCEN_SAR>` },
];

// ── ICON PRIMITIVES ──────────────────────────────────────────────────────────
const BrandLogo = ({ className = 'w-9 h-9' }: { className?: string }) => (
  <svg className={className} viewBox="0 0 44 44" fill="none">
    <rect width="44" height="44" rx="10" fill="#1e1b4b" />
    <rect x="1" y="1" width="42" height="42" rx="9" stroke="#4f46e5" strokeWidth="1.5" strokeOpacity="0.6" />
    <path d="M14 22L20 28L30 16" stroke="#818cf8" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="22" cy="22" r="14" stroke="#4f46e5" strokeWidth="1" strokeDasharray="3 3" strokeOpacity="0.5" />
  </svg>
);

const MenuIcon = () => (
  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

const ArrowRight = () => (
  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const ChevronRight = () => (
  <svg className="w-4 h-4 text-slate-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="9 18 15 12 9 6" />
  </svg>
);



// ── MAIN COMPONENT ───────────────────────────────────────────────────────────
export default function LandingPage() {
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
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
    }, 6000);
    return () => clearInterval(t);
  }, []);

  const currentWorkflowStep = WORKFLOW_STEPS.find(s => s.id === activeWorkflowStep)!;

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-slate-300 font-sans antialiased selection:bg-indigo-600 selection:text-white relative overflow-x-hidden">
      {/* Minimal grid texture */}
      <div className="fixed inset-0 pointer-events-none z-0" style={{
        backgroundImage: 'linear-gradient(rgba(99,102,241,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,0.03) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }} />

      <div className="relative z-10">
        {/* ── HEADER ─────────────────────────────────────────────────────── */}
        <header className="sticky top-0 z-50 h-14 flex items-center border-b border-slate-800/70 bg-[#0a0a0f]/90 backdrop-blur-xl">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <BrandLogo />
              <div className="flex items-baseline gap-2">
                <span className="font-semibold text-sm text-slate-100 tracking-tight">CF-Intelligence</span>
                <span className="text-xs text-slate-500 font-mono">v2.4.0</span>
              </div>
            </div>

            <nav aria-label="primary" className="hidden lg:flex items-center gap-6 text-[13px] font-medium text-slate-400">
              {['Overview', 'Problem', 'Workflow', 'Capabilities', 'Platform', 'Architecture', 'Security', 'API & Docs'].map(label => (
                <a
                  key={label}
                  href={`#${label === 'API & Docs' ? 'api' : label === 'Architecture' ? 'architecture' : label.toLowerCase().replace(/ & /g, '-').replace(/ /g, '-')}`}
                  className="hover:text-slate-100 transition-colors"
                >
                  {label}
                </a>
              ))}
            </nav>

            <div className="flex items-center gap-3">
              <div className="hidden xl:flex items-center gap-1.5 text-[11px] font-mono text-emerald-500">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                3/3 nodes synced
              </div>
              <button
                aria-label="Toggle Navigation Menu"
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-100 cursor-pointer"
              >
                <MenuIcon />
              </button>
              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[13px] font-medium text-slate-100 bg-indigo-600 hover:bg-indigo-500 transition-colors cursor-pointer"
              >
                Launch Demo <ArrowRight />
              </button>
            </div>
          </div>
        </header>

        {/* ── MOBILE NAVIGATION DRAWER ────────────────────────────────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.nav
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="lg:hidden border-b border-slate-800 bg-[#0a0a0f]/95 backdrop-blur-xl px-4 py-4 space-y-1 z-40"
            >
              {[
                { label: 'Overview (3D Architecture)', href: '#hero' },
                { label: 'The Problem & Solution', href: '#problem-solution' },
                { label: 'Streaming GNN Collusion Simulator', href: '#how-it-works' },
                { label: 'Privacy Engine & Capabilities', href: '#product' },
                { label: 'Deployment Blueprint Wizard', href: '#platform' },
                { label: 'System Architecture', href: '#architecture' },
                { label: 'Security & Attack Defense Lab', href: '#security' },
                { label: 'API & Docs', href: '#api' },
              ].map(link => (
                <a
                  key={link.label}
                  href={link.href}
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="block px-3 py-2 text-[13px] text-slate-400 hover:text-slate-100 hover:bg-slate-900 rounded-lg transition-colors"
                >
                  {link.label}
                </a>
              ))}
              <div className="pt-2 border-t border-slate-800">
                <button
                  onClick={() => { setIsMobileMenuOpen(false); navigate('/dashboard'); }}
                  className="w-full py-2 text-[13px] font-medium text-slate-100 bg-indigo-600 rounded-lg"
                >
                  Launch Platform
                </button>
              </div>
            </motion.nav>
          )}
        </AnimatePresence>

        {/* ── SECTION 1: OVERVIEW (#hero) ─────────────────────────────────── */}
        <section id="hero" className="py-16 sm:py-24 px-4 sm:px-6 max-w-7xl mx-auto">
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-start">
            {/* Left: Platform introduction */}
            <div className="lg:col-span-7 space-y-8">
              <div>
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded bg-indigo-600/10 border border-indigo-600/20 text-indigo-400 text-xs font-mono mb-4">
                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" />
                  Enterprise Platform — Production Deployment
                </div>
                <h1 className="text-3xl sm:text-4xl font-bold text-slate-100 leading-tight tracking-tight mb-4">
                  Privacy-Preserving<br />Cross-Bank Fraud Detection
                </h1>
                <p className="text-slate-400 text-[15px] leading-relaxed max-w-xl">
                  CF-Intelligence is a federated machine learning platform that enables financial institutions to collaboratively train fraud detection models on transaction graph data without exposing raw customer data to any external party.
                </p>
              </div>

              {/* Key characteristics */}
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: 'Stack', value: 'Federated Learning + GNN' },
                  { label: 'Privacy Model', value: '(ε=0.50, δ=1e-5)-DP' },
                  { label: 'Cryptography', value: 'Paillier HE + Intel SGX TEE' },
                  { label: 'Transport', value: 'ISO 20022 / pacs.008' },
                  { label: 'Byzantine Tolerance', value: 'Krum + Trimmed Mean' },
                  { label: 'Regulatory', value: 'FinCEN SAR Compliant' },
                  { label: 'Detection Gain', value: '42% → 94.2% accuracy' },
                  { label: 'Graph Engine', value: 'PyTorch Geometric GATConv' },
                ].map(row => (
                  <div key={row.label} className="flex items-start gap-3 px-3 py-2.5 rounded-lg bg-slate-900/50 border border-slate-800/60">
                    <span className="text-[11px] font-mono text-slate-500 min-w-[80px] pt-px">{row.label}</span>
                    <span className="text-[11px] font-mono text-slate-200">{row.value}</span>
                  </div>
                ))}
              </div>

              {/* CTAs */}
              <div className="flex flex-wrap items-center gap-3">
                <button
                  onClick={() => navigate('/dashboard')}
                  className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-[13px] font-medium text-white transition-colors cursor-pointer"
                >
                  Launch Live Platform Demo <ArrowRight />
                </button>
                <a
                  href="#architecture"
                  className="flex items-center gap-2 px-5 py-2.5 rounded-lg border border-slate-700 hover:border-slate-500 text-[13px] font-medium text-slate-300 transition-colors"
                >
                  System Architecture
                </a>
              </div>
            </div>

            {/* Right: System stats & live telemetry */}
            <div className="lg:col-span-5 space-y-3">
              {/* Build status badge */}
              <div className="px-4 py-3 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-between">
                <span className="text-[11px] font-mono text-slate-500">Test Coverage</span>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-mono text-emerald-400">14 suites / 28 tests passing</span>
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-600/20 text-emerald-400 border border-emerald-600/20">PASS</span>
                </div>
              </div>
              <div className="px-4 py-3 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-between">
                <span className="text-[11px] font-mono text-slate-500">Deployment</span>
                <div className="flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-[11px] font-mono text-emerald-400">Hugging Face Spaces — Live</span>
                </div>
              </div>
              <div className="px-4 py-3 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-between">
                <span className="text-[11px] font-mono text-slate-500">GitHub</span>
                <span className="text-[11px] font-mono text-slate-300">yusufcalisir/CF-Intelligence</span>
              </div>

              {/* Live Telemetry HUD */}
              <div className="mt-2 p-4 rounded-lg bg-slate-900 border border-slate-800 space-y-3">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2.5 mb-2.5">
                  <span className="text-[11px] font-mono text-slate-500 uppercase tracking-wider">Live Consortium Telemetry</span>
                  <span className="flex items-center gap-1.5 text-[10px] font-mono text-emerald-500">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-ping" />STREAMING
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Active FL Round</div>
                    <div className="text-xl font-bold font-mono text-indigo-400 mt-0.5">#{flRound}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Global Accuracy</div>
                    <div className="text-xl font-bold font-mono text-emerald-400 mt-0.5">{accuracy}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Privacy Budget</div>
                    <div className="text-xl font-bold font-mono text-purple-400 mt-0.5">ε=0.50</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Stream Speed</div>
                    <div className="text-xl font-bold font-mono text-blue-400 mt-0.5">1.4 GB/s</div>
                  </div>
                </div>
              </div>

              {/* Node status */}
              <div className="space-y-1.5">
                {Object.values(BANK_NODES).map(bank => (
                  <div
                    key={bank.id}
                    onClick={() => setActiveBankDrawer(bank)}
                    className="flex items-center justify-between px-3.5 py-2.5 rounded-lg bg-slate-900 border border-slate-800 hover:border-slate-600 cursor-pointer transition-colors group"
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                      <span className="text-[12px] font-medium text-slate-300 group-hover:text-slate-100">{bank.name}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[11px] font-mono text-slate-500">{bank.latency}</span>
                      <ChevronRight />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 2: PROBLEM (#problem-solution) ──────────────────────── */}
        <section id="problem-solution" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-12">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">Problem Statement</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">
              Money Laundering Networks Are Cross-Institutional
            </h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              Modern financial crime — specifically smurfing and layering — deliberately fragments transactions across multiple regulated banking institutions to stay below detection thresholds at any single bank. Existing AML systems are institution-local and cannot detect this cross-bank pattern.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Traditional approach */}
            <div className="p-6 rounded-xl bg-slate-900/50 border border-slate-800 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-semibold text-slate-200">Isolated Bank Detection</h3>
                <span className="px-2 py-0.5 text-[10px] font-mono text-rose-400 bg-rose-600/10 border border-rose-600/20 rounded">42% detection rate</span>
              </div>
              <div className="font-mono text-[11px] text-slate-400 space-y-2 border-t border-slate-800 pt-4">
                <div className="flex items-start gap-2">
                  <span className="text-rose-500 mt-px">✗</span>
                  <span>GNN trained exclusively on local ledger — no cross-institution edges</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-rose-500 mt-px">✗</span>
                  <span>Smurfing detected at single bank is indistinguishable from normal traffic</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-rose-500 mt-px">✗</span>
                  <span>GDPR Article 9 & banking secrecy laws prohibit raw data sharing</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-rose-500 mt-px">✗</span>
                  <span>False positive rate: ~31% — significant investigator burden</span>
                </div>
              </div>
            </div>

            {/* CFI approach */}
            <div className="p-6 rounded-xl bg-slate-900/50 border border-indigo-600/30 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-semibold text-slate-200">Federated Consortium Intelligence</h3>
                <span className="px-2 py-0.5 text-[10px] font-mono text-emerald-400 bg-emerald-600/10 border border-emerald-600/20 rounded">94.2% detection rate</span>
              </div>
              <div className="font-mono text-[11px] text-slate-400 space-y-2 border-t border-slate-800 pt-4">
                <div className="flex items-start gap-2">
                  <span className="text-emerald-500 mt-px">✓</span>
                  <span>Shared GNN embeddings trained on consortium-wide graph topology</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-emerald-500 mt-px">✓</span>
                  <span>Zero raw transaction data leaves any institution's premises</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-emerald-500 mt-px">✓</span>
                  <span>(ε=0.50, δ=1e-5)-DP guarantees plausible deniability on gradient updates</span>
                </div>
                <div className="flex items-start gap-2">
                  <span className="text-emerald-500 mt-px">✓</span>
                  <span>False positive rate: ~6.1% — investigator efficiency 5× improvement</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 3: WORKFLOW (#how-it-works) ─────────────────────────── */}
        <section id="how-it-works" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">Execution Pipeline</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">End-to-End Federated Training Pipeline</h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              Each federated learning round traverses eight stages — from raw ISO 20022 message ingestion through to FinCEN SAR export. Click any stage to inspect its architecture and code reference.
            </p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
            {/* Step list */}
            <div className="lg:col-span-4 space-y-1">
              {WORKFLOW_STEPS.map(step => (
                <button
                  key={step.id}
                  onClick={() => setActiveWorkflowStep(step.id)}
                  className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg border text-left transition-all ${
                    activeWorkflowStep === step.id
                      ? 'bg-indigo-600/10 border-indigo-600/40 text-slate-100'
                      : 'border-transparent hover:bg-slate-900 text-slate-400'
                  }`}
                >
                  <span className={`shrink-0 w-5 h-5 rounded flex items-center justify-center text-[10px] font-mono font-bold border ${
                    activeWorkflowStep === step.id ? 'bg-indigo-600 border-indigo-500 text-white' : 'border-slate-700 text-slate-500'
                  }`}>
                    {step.id}
                  </span>
                  <span className="text-[12px] font-medium">{step.short}</span>
                </button>
              ))}
            </div>

            {/* Step detail */}
            <div className="lg:col-span-8 space-y-4">
              <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
                <div>
                  <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider mb-1">Stage {currentWorkflowStep.id} of 8</div>
                  <h3 className="text-base font-semibold text-slate-100">{currentWorkflowStep.label}</h3>
                </div>
                <p className="text-[13px] text-slate-400 leading-relaxed border-t border-slate-800 pt-3">
                  {currentWorkflowStep.description}
                </p>
                <div className="flex flex-wrap gap-2">
                  {currentWorkflowStep.tech.map(t => (
                    <span key={t} className="px-2 py-0.5 rounded text-[10px] font-mono text-indigo-300 bg-indigo-600/10 border border-indigo-600/20">{t}</span>
                  ))}
                </div>
                <div className="rounded-lg bg-[#0d0d14] border border-slate-800 p-4 overflow-x-auto">
                  <pre className="text-[11px] font-mono text-indigo-300 leading-relaxed whitespace-pre-wrap">{currentWorkflowStep.code}</pre>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 4: CAPABILITIES (#product) ──────────────────────────── */}
        <section id="product" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">System Modules</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Platform Engineering Components</h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              Each module represents a discrete engineering component with defined responsibilities, algorithms, I/O contracts, and technology stack.
            </p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Module list */}
            <div className="lg:col-span-4 space-y-1">
              {PLATFORM_MODULES.map(mod => (
                <button
                  key={mod.id}
                  onClick={() => setActiveModule(mod)}
                  className={`w-full flex items-center justify-between px-4 py-3 rounded-lg border text-left transition-all group ${
                    activeModule?.id === mod.id
                      ? 'bg-slate-800 border-slate-600 text-slate-100'
                      : 'border-transparent hover:bg-slate-900/60 text-slate-400'
                  }`}
                >
                  <div>
                    <div className="text-[12px] font-medium leading-snug">{mod.name}</div>
                    <div className="text-[10px] font-mono text-slate-600 mt-0.5">{mod.category}</div>
                  </div>
                  <ChevronRight />
                </button>
              ))}
            </div>

            {/* Module detail */}
            {activeModule && (
              <div className="lg:col-span-8 p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-5">
                <div className="flex items-start justify-between border-b border-slate-800 pb-4">
                  <div>
                    <span className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider">{activeModule.category}</span>
                    <h3 className="text-lg font-semibold text-slate-100 mt-0.5">{activeModule.name}</h3>
                  </div>
                </div>
                <p className="text-[13px] text-slate-400 leading-relaxed">{activeModule.purpose}</p>
                <div className="grid grid-cols-2 gap-3 text-[11px] font-mono">
                  <div className="p-3 rounded-lg bg-[#0d0d14] border border-slate-800">
                    <div className="text-slate-500 uppercase tracking-wider text-[9px] mb-1">Algorithm</div>
                    <div className="text-slate-200">{activeModule.algorithm}</div>
                  </div>
                  <div className="p-3 rounded-lg bg-[#0d0d14] border border-slate-800">
                    <div className="text-slate-500 uppercase tracking-wider text-[9px] mb-1">Technology</div>
                    <div className="text-slate-200">{activeModule.tech}</div>
                  </div>
                  <div className="p-3 rounded-lg bg-[#0d0d14] border border-slate-800">
                    <div className="text-slate-500 uppercase tracking-wider text-[9px] mb-1">Inputs</div>
                    <div className="text-slate-200">{activeModule.inputs}</div>
                  </div>
                  <div className="p-3 rounded-lg bg-[#0d0d14] border border-slate-800">
                    <div className="text-slate-500 uppercase tracking-wider text-[9px] mb-1">Outputs</div>
                    <div className="text-slate-200">{activeModule.outputs}</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ── SECTION 5: PLATFORM (#platform) ─────────────────────────────── */}
        <section id="platform" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">Consortium Node Registry</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Active Bank Node Inspector</h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              Each participating institution deploys a lightweight bank node agent. Click any node to inspect its hardware configuration, PyTorch runtime, and live ISO 20022 stream activity.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {Object.values(BANK_NODES).map(bank => (
              <div
                key={bank.id}
                onClick={() => setActiveBankDrawer(bank)}
                className="p-5 rounded-xl bg-slate-900 border border-slate-800 hover:border-slate-600 cursor-pointer transition-all group space-y-3"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    <span className="text-[10px] font-mono text-emerald-500">ACTIVE</span>
                  </div>
                  <span className="text-[10px] font-mono text-slate-500">{bank.latency}</span>
                </div>
                <div>
                  <div className="text-[13px] font-medium text-slate-200 group-hover:text-slate-100">{bank.name}</div>
                  <div className="text-[10px] font-mono text-slate-500 mt-0.5">{bank.ticker}</div>
                </div>
                <div className="text-[10px] font-mono text-slate-600 border-t border-slate-800 pt-2.5 leading-relaxed">
                  {bank.hardware}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── SECTION 6: ARCHITECTURE (#architecture-internal) ─────────────────────── */}
        <section id="architecture" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">System Design</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Service Layer Map</h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              Click any service node to inspect its responsibilities, communication protocols, and technology stack.
            </p>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
            {/* Architecture node list */}
            <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 gap-2">
              {ARCH_NODES.map(node => (
                <button
                  key={node.id}
                  onClick={() => setActiveArchNode(node)}
                  className={`p-4 rounded-lg border text-left transition-all ${
                    activeArchNode?.id === node.id
                      ? 'bg-indigo-600/10 border-indigo-600/40'
                      : 'bg-slate-900/50 border-slate-800 hover:border-slate-600'
                  }`}
                >
                  <div className={`text-[12px] font-medium mb-0.5 ${activeArchNode?.id === node.id ? 'text-indigo-300' : 'text-slate-300'}`}>
                    {node.label}
                  </div>
                  <div className="text-[10px] font-mono text-slate-600 leading-snug">{node.tech.slice(0, 2).join(' · ')}</div>
                </button>
              ))}
            </div>

            {/* Architecture node detail */}
            {activeArchNode && (
              <div className="lg:col-span-7 p-6 rounded-xl bg-slate-900 border border-slate-800 space-y-5">
                <div className="border-b border-slate-800 pb-4">
                  <div className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider mb-0.5">Service Component</div>
                  <h3 className="text-base font-semibold text-slate-100">{activeArchNode.label}</h3>
                  <p className="text-[13px] text-slate-400 mt-2 leading-relaxed">{activeArchNode.description}</p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-[11px] font-mono">
                  <div>
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-2">Responsibilities</div>
                    <ul className="space-y-1">
                      {activeArchNode.responsibilities.map(r => (
                        <li key={r} className="text-slate-300 flex items-center gap-1.5"><span className="text-indigo-500">·</span>{r}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-2">Protocols</div>
                    <ul className="space-y-1">
                      {activeArchNode.protocols.map(p => (
                        <li key={p} className="text-slate-300 flex items-center gap-1.5"><span className="text-purple-500">·</span>{p}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider mb-2">Technology</div>
                    <ul className="space-y-1">
                      {activeArchNode.tech.map(t => (
                        <li key={t} className="text-slate-300 flex items-center gap-1.5"><span className="text-cyan-500">·</span>{t}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ── SECTION 7: SECURITY (#security) ─────────────────────────────── */}
        <section id="security" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">Security Model</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">Privacy &amp; Trust Boundary Model</h2>
          </div>

          <div className="flex gap-2 border-b border-slate-800 pb-0 mb-6">
            {[
              { id: 'flow', label: 'Data Flow' },
              { id: 'threat', label: 'Threat Model' },
              { id: 'compliance', label: 'Compliance' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActivePrivacyTab(tab.id as 'flow' | 'threat' | 'compliance')}
                className={`px-4 py-2 text-[12px] font-medium border-b-2 transition-colors -mb-px ${
                  activePrivacyTab === tab.id
                    ? 'border-indigo-500 text-indigo-300'
                    : 'border-transparent text-slate-500 hover:text-slate-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {activePrivacyTab === 'flow' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
                <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Inside Bank Perimeter</div>
                <div className="space-y-2 font-mono text-[11px] text-slate-400">
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Raw transaction records</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Customer PII</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Account balances</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Local GNN graph</div>
                </div>
                <div className="text-[10px] text-rose-500 font-mono">← Never transmitted externally</div>
              </div>
              <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
                <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Transmitted (DP-Noised)</div>
                <div className="space-y-2 font-mono text-[11px] text-slate-400">
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">DP-noised gradient tensors</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Paillier ciphertexts</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Round participation flags</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">HSM-signed commitments</div>
                </div>
                <div className="text-[10px] text-emerald-500 font-mono">← (ε=0.50, δ=1e-5)-DP bounded</div>
              </div>
              <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
                <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">SGX Enclave (Hardware Isolated)</div>
                <div className="space-y-2 font-mono text-[11px] text-slate-400">
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">HE aggregation only</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">IAS attestation verified</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">Encrypted memory pages</div>
                  <div className="p-2 rounded bg-slate-950 border border-slate-800">No external network access</div>
                </div>
                <div className="text-[10px] text-purple-500 font-mono">← Hardware trust boundary</div>
              </div>
            </div>
          )}

          {activePrivacyTab === 'threat' && (
            <div className="space-y-3">
              {[
                { threat: 'Gradient Inversion Attack', mitigation: 'Gaussian DP noise (σ calibrated to ε=0.50) makes gradient inversion computationally infeasible. Clipping bound C=1.0.', severity: 'Mitigated' },
                { threat: 'Byzantine Gradient Poisoning', mitigation: 'Krum + Trimmed Mean Byzantine-robust aggregation neutralises up to f < n/2 compromised nodes per round.', severity: 'Mitigated' },
                { threat: 'Coordinator Compromise', mitigation: 'Intel SGX TEE handles aggregation. Coordinator sees only ciphertexts; plaintext gradients are never accessible outside enclave.', severity: 'Mitigated' },
                { threat: 'Membership Inference', mitigation: 'RDP accountant tracks cumulative budget. Training halted when ε threshold exceeded. Per-sample clipping prevents memorisation.', severity: 'Mitigated' },
                { threat: 'Model Extraction', mitigation: 'Global model is distributed only to authenticated bank nodes over mTLS. No external inference endpoint is exposed.', severity: 'Mitigated' },
              ].map(row => (
                <div key={row.threat} className="flex items-start gap-4 p-4 rounded-lg bg-slate-900 border border-slate-800">
                  <div className="w-40 shrink-0">
                    <div className="text-[11px] font-medium text-rose-400">{row.threat}</div>
                    <div className="text-[10px] font-mono text-emerald-500 mt-0.5">{row.severity}</div>
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono leading-relaxed">{row.mitigation}</div>
                </div>
              ))}
            </div>
          )}

          {activePrivacyTab === 'compliance' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[
                { standard: 'GDPR Article 25', status: 'Privacy by Design', detail: 'DP guarantees built into training pipeline. No PII ever leaves institution.' },
                { standard: 'FinCEN SAR Regulation', status: 'Compliant', detail: 'Automated SAR XML generation with cryptographic sign-off and audit trail.' },
                { standard: 'EU AML Directive 6AMLD', status: 'Compliant', detail: 'Cross-border pattern detection via federated architecture without data transfer.' },
                { standard: 'NIST SP 800-188', status: 'Aligned', detail: 'De-identification through differential privacy following NIST de-ID standard.' },
                { standard: 'ISO 20022', status: 'Native', detail: 'pacs.008 and camt.053 message formats parsed natively by Bank Connector.' },
                { standard: 'SOC 2 Type II', status: 'In Progress', detail: 'Audit logging, access controls, and telemetry pipeline support SOC 2 criteria.' },
              ].map(row => (
                <div key={row.standard} className="p-4 rounded-lg bg-slate-900 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-mono text-slate-300">{row.standard}</span>
                    <span className="text-[10px] font-mono text-emerald-400">{row.status}</span>
                  </div>
                  <p className="text-[11px] text-slate-500 leading-relaxed">{row.detail}</p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── SECTION 8: API & DOCS (#api, #docs) ─────────────────────────── */}
        <section id="api" className="py-16 sm:py-20 px-4 sm:px-6 max-w-7xl mx-auto border-t border-slate-800/60 space-y-8">
          <div id="docs" className="max-w-2xl">
            <div className="text-[11px] font-mono text-slate-500 uppercase tracking-widest mb-3">Developer API</div>
            <h2 className="text-2xl sm:text-3xl font-bold text-slate-100 leading-tight mb-4">REST API & Connector SDK</h2>
            <p className="text-slate-400 text-[14px] leading-relaxed">
              The CF-Intelligence platform exposes a REST API for coordinator control, a WebSocket stream for real-time telemetry, and a Bank Connector SDK for onboarding new institutions.
            </p>
          </div>

          {/* API tab switcher */}
          <div className="flex gap-1 p-1 bg-slate-900 border border-slate-800 rounded-lg w-fit">
            {[
              { id: 'curl', label: 'cURL' },
              { id: 'python', label: 'Python' },
              { id: 'ts', label: 'TypeScript' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveApiTab(tab.id as 'curl' | 'python' | 'ts')}
                className={`px-4 py-1.5 text-[12px] font-mono rounded-md transition-all ${
                  activeApiTab === tab.id
                    ? 'bg-slate-700 text-slate-100'
                    : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="rounded-xl bg-[#0d0d14] border border-slate-800 p-5 overflow-x-auto">
            <pre className="text-[12px] font-mono text-indigo-200 leading-relaxed whitespace-pre">
              {activeApiTab === 'curl' && `# Trigger a new federated learning round
curl -X POST https://api.cfi-platform.com/v1/rounds \\
  -H "Authorization: Bearer cfi_api_key_991823" \\
  -H "Content-Type: application/json" \\
  -d '{
    "consortium_id": "cfi-prod-001",
    "node_ids": ["jpmorgan-01", "hsbc-02", "deutsche-03"],
    "privacy_config": {"epsilon": 0.50, "delta": 1e-5},
    "aggregation": "krum",
    "max_round_duration_s": 300
  }'

# Stream live telemetry via WebSocket
wscat -c wss://api.cfi-platform.com/v1/telemetry \\
  -H "Authorization: Bearer cfi_api_key_991823"`}
              {activeApiTab === 'python' && `from cfi_sdk import CFIClient

client = CFIClient(api_key="cfi_api_key_991823")

# Configure a bank connector
connector = client.connectors.create(
    bank_id="jpmorgan-01",
    iso20022_endpoint="kafka://jpm-kafka:9092/pacs008",
    privacy={"epsilon": 0.50, "delta": 1e-5},
    hardware="nvidia-h100",
)

# Trigger federated round and stream results
round_ = client.rounds.start(
    consortium_id="cfi-prod-001",
    node_ids=["jpmorgan-01", "hsbc-02", "deutsche-03"],
)

for event in client.rounds.stream(round_.id):
    print(f"Round {event.round_id}: {event.stage} — {event.accuracy:.3f}")`}
              {activeApiTab === 'ts' && `import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({ apiKey: 'cfi_api_key_991823' });

// Start federated round
const round = await client.rounds.start({
  consortiumId: 'cfi-prod-001',
  nodeIds: ['jpmorgan-01', 'hsbc-02', 'deutsche-03'],
  privacyConfig: { epsilon: 0.50, delta: 1e-5 },
  aggregation: 'krum',
});

// Listen to real-time events
const ws = client.telemetry.subscribe(round.id);
ws.on('round.stage', (event) => {
  console.log(\`Stage: \${event.stage}, Accuracy: \${event.accuracy}\`);
});`}
            </pre>
          </div>

          {/* API endpoints table */}
          <div className="rounded-xl border border-slate-800 overflow-hidden">
            <div className="px-4 py-3 bg-slate-900 border-b border-slate-800">
              <span className="text-[11px] font-mono text-slate-400">API Endpoints</span>
            </div>
            <table className="w-full text-[11px] font-mono">
              <tbody>
                {[
                  { method: 'POST', path: '/v1/rounds', desc: 'Trigger a new federated learning round' },
                  { method: 'GET', path: '/v1/rounds/:id', desc: 'Get round status and metrics' },
                  { method: 'GET', path: '/v1/nodes', desc: 'List consortium bank nodes' },
                  { method: 'POST', path: '/v1/connectors', desc: 'Register new bank connector' },
                  { method: 'GET', path: '/v1/reports/sar', desc: 'Retrieve FinCEN SAR exports' },
                  { method: 'WS', path: '/v1/telemetry', desc: 'Real-time round telemetry stream' },
                ].map(row => (
                  <tr key={row.path} className="border-b border-slate-800/60 hover:bg-slate-900/50">
                    <td className="px-4 py-3 w-16">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        row.method === 'GET' ? 'bg-sky-600/10 text-sky-400' :
                        row.method === 'POST' ? 'bg-emerald-600/10 text-emerald-400' :
                        'bg-purple-600/10 text-purple-400'
                      }`}>
                        {row.method}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-indigo-300">{row.path}</td>
                    <td className="px-4 py-3 text-slate-500">{row.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* ── FOOTER ──────────────────────────────────────────────────────── */}
        <footer className="border-t border-slate-800/60 py-10 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
            <div className="flex items-center gap-3">
              <BrandLogo className="w-7 h-7" />
              <div>
                <div className="text-[12px] font-semibold text-slate-300">CF-Intelligence v2.4.0</div>
                <div className="text-[11px] font-mono text-slate-600">Privacy-Preserving Federated Fraud Intelligence</div>
              </div>
            </div>
            <div className="flex items-center gap-4 text-[11px] font-mono text-slate-600">
              <span>PyTorch · Intel SGX · ISO 20022 · FinCEN SAR</span>
            </div>
            <button
              onClick={() => navigate('/dashboard')}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-[12px] font-medium text-slate-100 bg-indigo-600 hover:bg-indigo-500 transition-colors cursor-pointer"
            >
              Open Platform <ArrowRight />
            </button>
          </div>
        </footer>

        {/* ── BANK NODE INSPECTOR DRAWER ───────────────────────────────── */}
        <AnimatePresence>
          {activeBankDrawer && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setActiveBankDrawer(null)}
              className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex justify-end"
            >
              <motion.div
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 28, stiffness: 200 }}
                onClick={e => e.stopPropagation()}
                className="w-full max-w-lg bg-[#0f0f18] border-l border-slate-800 p-6 overflow-y-auto space-y-5"
              >
                <div className="flex items-start justify-between border-b border-slate-800 pb-5">
                  <div>
                    <div className="text-[10px] font-mono text-indigo-400 uppercase tracking-wider">Bank Node Inspector</div>
                    <h3 className="text-base font-semibold text-slate-100 mt-1">{activeBankDrawer.name}</h3>
                    <div className="text-[11px] font-mono text-slate-500 mt-0.5">{activeBankDrawer.location}</div>
                  </div>
                  <button
                    onClick={() => setActiveBankDrawer(null)}
                    className="px-3 py-1.5 rounded-lg text-[11px] font-mono bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-100"
                  >
                    close
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
                  {[
                    { k: 'Ticker', v: activeBankDrawer.ticker },
                    { k: 'Latency', v: activeBankDrawer.latency },
                    { k: 'Hardware', v: activeBankDrawer.hardware },
                    { k: 'Host RAM', v: activeBankDrawer.ram },
                    { k: 'PyTorch', v: activeBankDrawer.pytorch },
                    { k: 'Status', v: 'ACTIVE — Round Participant' },
                  ].map(row => (
                    <div key={row.k} className="p-3 rounded-lg bg-slate-950 border border-slate-800">
                      <div className="text-slate-600 text-[9px] uppercase tracking-wider mb-0.5">{row.k}</div>
                      <div className="text-slate-200 truncate">{row.v}</div>
                    </div>
                  ))}
                </div>

                <div>
                  <div className="text-[10px] font-mono text-slate-500 uppercase tracking-wider mb-2">ISO 20022 Stream Activity</div>
                  <div className="rounded-lg bg-slate-950 border border-slate-800 p-4 space-y-3 font-mono text-[10px] text-slate-400 overflow-x-auto">
                    {activeBankDrawer.xmlLogs.map((log, i) => (
                      <div key={i} className="flex items-start gap-2 border-b border-slate-900 pb-2 last:border-0 last:pb-0">
                        <span className="text-indigo-600 shrink-0">[{String(i + 1).padStart(2, '0')}]</span>
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
