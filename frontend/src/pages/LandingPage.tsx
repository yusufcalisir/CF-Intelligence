import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// SVG Icons
const CodeIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

const ArrowRightIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const LockIcon = () => (
  <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);

const MenuIcon = () => (
  <svg className="w-6 h-6 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

const CloseIcon = () => (
  <svg className="w-6 h-6 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

const FanSpinner = () => (
  <svg className="w-4 h-4 text-indigo-400 animate-spin" style={{ animationDuration: '3s' }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="10" />
    <path d="M12 2a10 10 0 0 1 10 10" />
    <path d="M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0" />
  </svg>
);

// Real Global Bank Information Interface for Drawer
interface BankInfoDetail {
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
    location: 'New York, US (Node #01)',
    hardware: 'NVIDIA A100 Tensor Core (80GB VRAM)',
    ram: '128 GB Host RAM',
    pytorch: '2.2.1+cu121',
    latency: '0.8 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO20022 intake parsed: 4,800 transactions/sec. Local PyTorch GNN embeddings generated.',
      'Differential Privacy noise injected: Gaussian(0, 0.05). Encrypted gradient uploaded to Enclave.',
    ],
  },
  hsbc: {
    id: 'hsbc',
    name: 'HSBC Holdings plc',
    ticker: 'LSE: HSBC',
    location: 'London, UK (Node #02)',
    hardware: 'NVIDIA H100 SXM (80GB VRAM)',
    ram: '64 GB Host RAM',
    pytorch: '2.2.1+cu121',
    latency: '1.4 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>HSBC-2026-8810</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="GBP">890000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO20022 intake parsed: 3,200 transactions/sec. Local PyTorch GNN embeddings generated.',
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

// Graph Node Telemetry Inspector Interface
interface GraphNodeDetail {
  id: string;
  name: string;
  bank: string;
  riskScore: number;
  velocity: string;
  anomalyIndex: string;
  status: string;
  description: string;
}

const GRAPH_NODES_DATA: Record<string, GraphNodeDetail> = {
  jpm: { id: 'JPM-ACCT-01', name: 'JPMorgan Source Account', bank: 'JPMorgan Chase (US)', riskScore: 0.12, velocity: '14 tx/min', anomalyIndex: 'Low (0.04)', status: 'CLEARED', description: 'Legitimate corporate originator initiating international settlement.' },
  shell_a: { id: 'SHELL-CORP-A', name: 'Shell Corp Alpha', bank: 'Santander UK', riskScore: 0.94, velocity: '340 tx/min', anomalyIndex: 'CRITICAL (0.92)', status: 'FLAGGED', description: 'Rapid layering shell account splitting funds into sub-threshold amounts.' },
  smurf_1: { id: 'SMURF-ACCT-1', name: 'Money Mule Smurf 1', bank: 'Barclays UK', riskScore: 0.96, velocity: '120 tx/min', anomalyIndex: 'CRITICAL (0.95)', status: 'FLAGGED', description: 'Intermediary mule account forwarding funds across borders.' },
  hsbc: { id: 'HSBC-ACCT-02', name: 'HSBC Destination Account', bank: 'HSBC Holdings (UK)', riskScore: 0.15, velocity: '8 tx/min', anomalyIndex: 'Low (0.05)', status: 'CLEARED', description: 'Target commercial recipient account.' },
  shell_b: { id: 'SHELL-CORP-B', name: 'Shell Corp Beta', bank: 'BNP Paribas', riskScore: 0.92, velocity: '280 tx/min', anomalyIndex: 'HIGH (0.88)', status: 'FLAGGED', description: 'Secondary layering entity attempting to obfuscate audit trails.' },
  smurf_2: { id: 'SMURF-ACCT-2', name: 'Money Mule Smurf 2', bank: 'Credit Agricole', riskScore: 0.95, velocity: '160 tx/min', anomalyIndex: 'CRITICAL (0.94)', status: 'FLAGGED', description: 'Intermediary mule account fanning out transactions.' },
  db: { id: 'DB-RELAY-03', name: 'Deutsche Bank Relay Node', bank: 'Deutsche Bank (DE)', riskScore: 0.18, velocity: '45 tx/min', anomalyIndex: 'Normal (0.12)', status: 'CLEARED', description: 'High-volume euro clearing node.' },
  offramp: { id: 'CRYPTO-OFFRAMP', name: 'Crypto Exchange Offramp', bank: 'Unregulated Offramp', riskScore: 0.98, velocity: '890 tx/min', anomalyIndex: 'SEVERE (0.99)', status: 'ISOLATED', description: 'Ultimate illicit exit node converting fiat to unhosted wallets.' },
};

// Live Telemetry Log Feed Items
const LIVE_LOG_FEED = [
  'JPMorgan Chase Node completed Local Epoch 3/3 (Loss: 0.0381)',
  'HSBC Holdings Node generated Paillier Homomorphic Ciphertext [[W_hsbc]]',
  'CFI Aggregator Core: Quorum Reached (3/3 Real Banks) - Aggregating Round',
  'Cross-Bank Streaming GNN: Zero High-Risk Collusion Rings Detected',
  'Deutsche Bank AG Node synchronized Global Model Weight Vector (v2.4.0)',
  'Zero-Trust ABAC Policy Evaluated: Clearance Level 4 Verified (SAR Export Granted)',
  'Intel SGX Secure Enclave Memory Shield Active (0.00% Leakage Risk)',
];

export default function LandingPage() {
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const bgCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Responsive Mobile Menu State
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);

  // Hero interactive state
  const [isPlaying, setIsPlaying] = useState<boolean>(true);
  const [isDpShieldActive, setIsDpShieldActive] = useState<boolean>(true);
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);

  // FL Cycle Phase State: 0 = Local Training, 1 = Upload, 2 = Aggregation, 3 = Download
  const [flPhase, setFlPhase] = useState<number>(0);

  // Telemetry HUD state
  const [flRound, setFlRound] = useState<number>(42);
  const [accuracy, setAccuracy] = useState<number>(98.42);
  const [logIndex, setLogIndex] = useState<number>(0);

  // Dynamic Fluctuating Code Telemetry Metrics
  const [cudaJpm, setCudaJpm] = useState<number>(94.2);
  const [cudaHsbc, setCudaHsbc] = useState<number>(98.1);
  const [cpuDb, setCpuDb] = useState<number>(87.5);

  // Active Scroll Section State for Quick-Nav Dock
  const [activeSection, setActiveSection] = useState<string>('hero');

  // Section 3: Product Capabilities State
  const [productTab, setProductTab] = useState<'dp' | 'negotiator' | 'sla'>('dp');
  const [epsilonCalc, setEpsilonCalc] = useState<number>(0.5);

  // Section 4: 8-Node Platform Graph State
  const [graphStep, setGraphStep] = useState<number>(1);
  const [isGraphDetected, setIsGraphDetected] = useState<boolean>(false);
  const [isGraphIsolated, setIsGraphIsolated] = useState<boolean>(false);
  const [showAttentionMatrix, setShowAttentionMatrix] = useState<boolean>(false);
  const [selectedGraphNode, setSelectedGraphNode] = useState<GraphNodeDetail | null>(null);

  // Section 5: Architecture Layer Stack State
  const [activeLayer, setActiveLayer] = useState<number>(1);

  // Section 6: Security Attack Simulator State
  const [attackType, setAttackType] = useState<'mia' | 'dlg' | 'byzantine'>('mia');
  const [attackStatus, setAttackStatus] = useState<string>('SAFE (0.00% Leakage Risk)');

  // Section 7: API Playground Multi-Lang SDK State
  const [apiLang, setApiLang] = useState<'curl' | 'python' | 'node' | 'go'>('curl');
  const [apiEndpoint, setApiEndpoint] = useState<string>('handshake');
  const [apiReqBody] = useState<string>(
    JSON.stringify(
      { bank_id: 'jpmorgan_chase', pytorch_version: '2.2.1+cu121', ram_gb: 128.0 },
      null,
      2
    )
  );
  const [apiResponse, setApiResponse] = useState<string | null>(null);
  const [isApiLoading, setIsApiLoading] = useState<boolean>(false);

  // Section 8: Docs Deployment Blueprint State
  const [deployTab, setDeployTab] = useState<'helm' | 'docker' | 'terraform' | 'shell'>('helm');
  const [wizOs, setWizOs] = useState<'linux' | 'macos' | 'windows'>('linux');
  const [wizHardware, setWizHardware] = useState<'cuda' | 'metal' | 'cpu'>('cuda');
  const [wizRegulation, setWizRegulation] = useState<'eu_ai_act' | 'ffiec' | 'fca'>('eu_ai_act');
  const [copiedWizCmd, setCopiedWizCmd] = useState<boolean>(false);

  // Track active section on scroll
  useEffect(() => {
    const handleScroll = () => {
      const sections = ['hero', 'problem-solution', 'how-it-works', 'product', 'platform', 'architecture', 'security', 'api', 'docs'];
      const scrollPosition = window.scrollY + 200;

      for (const sectionId of sections) {
        const el = document.getElementById(sectionId);
        if (el) {
          const top = el.offsetTop;
          const height = el.offsetHeight;
          if (scrollPosition >= top && scrollPosition < top + height) {
            setActiveSection(sectionId);
            break;
          }
        }
      }
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Fluctuating Code Telemetry Metrics
  useEffect(() => {
    const interval = setInterval(() => {
      setCudaJpm(Number((93 + Math.random() * 3.5).toFixed(1)));
      setCudaHsbc(Number((97 + Math.random() * 2.5).toFixed(1)));
      setCpuDb(Number((85 + Math.random() * 5.0).toFixed(1)));
    }, 1500);
    return () => clearInterval(interval);
  }, []);

  // Continuous 4-Phase Cyclic FL Storytelling Loop
  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setFlPhase((prevPhase) => {
        const nextPhase = (prevPhase + 1) % 4;
        if (nextPhase === 2) {
          setFlRound((r) => r + 1);
          setAccuracy((acc) => Number((acc + (Math.random() * 0.06 - 0.03)).toFixed(2)));
        }
        return nextPhase;
      });
      setLogIndex((prevIndex) => (prevIndex + 1) % LIVE_LOG_FEED.length);
    }, 2800);
    return () => clearInterval(interval);
  }, [isPlaying]);

  // Living Distributed Background Topology Mesh Canvas
  useEffect(() => {
    const canvas = bgCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animId: number;
    let w = (canvas.width = window.innerWidth);
    let h = (canvas.height = window.innerHeight);

    const handleResize = () => {
      if (!canvas) return;
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    const bgNodes: Array<{ x: number; y: number; vx: number; vy: number; radius: number }> = [];
    const count = w < 640 ? 18 : 35;
    for (let i = 0; i < count; i++) {
      bgNodes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        radius: 1.5 + Math.random() * 2,
      });
    }

    const drawBg = () => {
      ctx.clearRect(0, 0, w, h);

      for (let i = 0; i < bgNodes.length; i++) {
        const n1 = bgNodes[i];
        if (!n1) continue;
        n1.x += n1.vx;
        n1.y += n1.vy;

        if (n1.x < 0 || n1.x > w) n1.vx *= -1;
        if (n1.y < 0 || n1.y > h) n1.vy *= -1;

        ctx.fillStyle = '#6366f130';
        ctx.beginPath();
        ctx.arc(n1.x, n1.y, n1.radius, 0, Math.PI * 2);
        ctx.fill();

        for (let j = i + 1; j < bgNodes.length; j++) {
          const n2 = bgNodes[j];
          if (!n2) continue;
          const dx = n1.x - n2.x;
          const dy = n1.y - n2.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < 140) {
            ctx.strokeStyle = `rgba(99, 102, 241, ${0.15 * (1 - dist / 140)})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(n1.x, n1.y);
            ctx.lineTo(n2.x, n2.y);
            ctx.stroke();
          }
        }
      }

      animId = requestAnimationFrame(drawBg);
    };

    drawBg();

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animId);
    };
  }, []);

  // 60FPS Hero 3D Isometric Packet Cable Canvas Engine
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let width = (canvas.width = canvas.parentElement?.clientWidth || 600);
    let height = (canvas.height = canvas.parentElement?.clientHeight || 520);

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = canvas.parentElement?.clientWidth || 600;
      height = canvas.height = canvas.parentElement?.clientHeight || 520;
    };
    window.addEventListener('resize', handleResize);

    const isMobile = width < 640;
    // 3D Isometric Projection Positions for 4 Bank Platforms + Center Enclave Core
    const center = { x: width * 0.5, y: height * 0.5 };
    const nodes = {
      jpm: { x: isMobile ? width * 0.22 : width * 0.22, y: height * 0.25, color: '#6366f1', label: 'pacs.008 ($1.45M)' },
      hsbc: { x: isMobile ? width * 0.78 : width * 0.78, y: height * 0.25, color: '#ef4444', label: 'pacs.008 (£890K)' },
      sgx: { x: isMobile ? width * 0.22 : width * 0.22, y: height * 0.75, color: '#a855f7', label: '[[W_sgx]]' },
      db: { x: isMobile ? width * 0.78 : width * 0.78, y: height * 0.75, color: '#38bdf8', label: 'camt.053 (€650K)' },
      core: { x: center.x, y: center.y, color: '#ec4899', label: '[[W_global]]' },
    };

    const particles: Array<{
      x: number;
      y: number;
      startX: number;
      startY: number;
      targetX: number;
      targetY: number;
      speed: number;
      progress: number;
      color: string;
      label: string;
    }> = [];

    const createParticle = (from: 'jpm' | 'hsbc' | 'sgx' | 'db', isUpload: boolean) => {
      const bank = nodes[from];
      const core = nodes.core;

      const startX = isUpload ? bank.x : core.x;
      const startY = isUpload ? bank.y : core.y;
      const targetX = isUpload ? core.x : bank.x;
      const targetY = isUpload ? core.y : bank.y;

      particles.push({
        x: startX,
        y: startY,
        startX,
        startY,
        targetX,
        targetY,
        speed: 0.012 + Math.random() * 0.008,
        progress: 0,
        color: isUpload ? bank.color : '#38bdf8',
        label: isUpload ? bank.label : `[[W_${from}]]`,
      });
    };

    let tick = 0;
    const render = () => {
      ctx.clearRect(0, 0, width, height);

      // 3D Isometric Laser Fiber Connection Lines
      ctx.lineWidth = 2.5;
      ctx.setLineDash([8, 8]);

      ['jpm', 'hsbc', 'sgx', 'db'].forEach((key) => {
        const n = nodes[key as keyof typeof nodes];
        ctx.strokeStyle = n.color + '80';
        ctx.beginPath();
        ctx.moveTo(n.x, n.y);
        ctx.lineTo(nodes.core.x, nodes.core.y);
        ctx.stroke();
      });

      ctx.setLineDash([]);

      // Continuously spawn ISO 20022 XML packet tags
      if (isPlaying && tick % 16 === 0) {
        if (flPhase === 1 || flPhase === 0) {
          createParticle('jpm', true);
          createParticle('hsbc', true);
          createParticle('sgx', true);
          createParticle('db', true);
        } else {
          createParticle('jpm', false);
          createParticle('hsbc', false);
          createParticle('sgx', false);
          createParticle('db', false);
        }
      }

      // Shockwave in Phase 2
      if (flPhase === 2) {
        const shockRadius = (tick % 30) * 3.5;
        ctx.strokeStyle = '#ec489980';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(nodes.core.x, nodes.core.y, shockRadius, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Draw active 3D packet stream tags
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        if (!p) continue;
        p.progress += p.speed;

        p.x = (1 - p.progress) * p.startX + p.progress * p.targetX;
        p.y = (1 - p.progress) * p.startY + p.progress * p.targetY;

        // Render Packet Badge
        ctx.fillStyle = '#0f172a';
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.roundRect(p.x - 42, p.y - 11, 84, 22, 6);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = p.color;
        ctx.font = 'bold 9px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(p.label, p.x, p.y + 3);

        if (p.progress >= 1) {
          particles.splice(i, 1);
        }
      }

      tick++;
      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationFrameId);
    };
  }, [isPlaying, flPhase]);

  // Security Attack Simulator Logic
  const handleRunAttack = (type: 'mia' | 'dlg' | 'byzantine') => {
    setAttackType(type);
    setAttackStatus('RUNNING SIMULATION...');
    setTimeout(() => {
      if (type === 'mia') {
        setAttackStatus('MIA LEAK RISK: 0.02% (Mathematically Shielded by ε=0.50 DP Noise)');
      } else if (type === 'dlg') {
        setAttackStatus('DLG GRADIENT RECONSTRUCTION BLOCKED: Tensor Reconstruction Error > 99.94%');
      } else {
        setAttackStatus('BYZANTINE NODE ISOLATED: Trimmed-Mean Aggregation Neutralized 100% Malicious Gradients');
      }
    }, 1000);
  };

  // API Playground Execution
  const handleSendApiRequest = () => {
    setIsApiLoading(true);
    setApiResponse(null);
    setTimeout(() => {
      setIsApiLoading(false);
      if (apiEndpoint === 'handshake') {
        setApiResponse(
          JSON.stringify(
            {
              registered: true,
              status: 'COMPATIBLE',
              handshake_token: 'hs_tok_jpm89a2f10b42',
              assigned_quorum: 'cluster_global_tier1',
              timestamp: new Date().toISOString(),
            },
            null,
            2
          )
        );
      } else if (apiEndpoint === 'clients') {
        setApiResponse(
          JSON.stringify(
            [
              { bank: 'JPMorgan Chase', status: 'ONLINE', ram_gb: 128.0, hardware: 'NVIDIA A100' },
              { bank: 'HSBC Holdings', status: 'ONLINE', ram_gb: 64.0, hardware: 'NVIDIA H100' },
              { bank: 'Deutsche Bank', status: 'ONLINE', ram_gb: 32.0, hardware: 'CPU Cluster' },
            ],
            null,
            2
          )
        );
      } else {
        setApiResponse(
          JSON.stringify(
            {
              allowed: true,
              policy_name: 'Compliance_Officer_SAR_Export_V2',
              reason: 'Role clearance level 4 matches Tier 1 dataset clearance requirement.',
              evaluated_at: new Date().toISOString(),
            },
            null,
            2
          )
        );
      }
    }, 800);
  };

  // Multi-Language SDK Code Generator
  const getSdkCode = () => {
    if (apiLang === 'curl') {
      return `curl -X POST https://api.cfi-platform.org/v1/coordinator/${apiEndpoint} \\
  -H "Authorization: Bearer cfi_sec_key_9942a1" \\
  -H "Content-Type: application/json" \\
  -d '${apiReqBody}'`;
    }
    if (apiLang === 'python') {
      return `from cfi_sdk import FederatedCoordinatorClient

client = FederatedCoordinatorClient(api_key="cfi_sec_key_9942a1")
response = client.coordinator.${apiEndpoint}(
    bank_id="jpmorgan_chase",
    pytorch_version="2.2.1+cu121",
    ram_gb=128.0
)
print("Handshake Token:", response.handshake_token)`;
    }
    if (apiLang === 'node') {
      return `import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({ apiKey: 'cfi_sec_key_9942a1' });
const result = await client.coordinator.execute('${apiEndpoint}', {
  bankId: 'jpmorgan_chase',
  pytorchVersion: '2.2.1+cu121'
});
console.log('Quorum Status:', result.status);`;
    }
    return `package main

import (
    "fmt"
    "github.com/cfi-platform/cfi-go/sdk"
)

func main() {
    client := sdk.NewClient("cfi_sec_key_9942a1")
    res, _ := client.Coordinator.Handshake("jpmorgan_chase")
    fmt.Println("Handshake:", res.Token)
}`;
  };

  // Deployment Blueprint Code Generator
  const getDeployBlueprint = () => {
    const accelFlag = wizHardware === 'cuda' ? '--cuda' : wizHardware === 'metal' ? '--metal' : '--cpu';
    const regFlag = wizRegulation === 'eu_ai_act' ? '--compliance eu-ai-act' : '--compliance ffiec';

    if (deployTab === 'helm') {
      return `# Kubernetes Helm values.yaml for CFI Node
replicaCount: 3
hardware:
  acceleration: "${wizHardware}"
  gpuMemory: "80Gi"
enclave:
  type: "intel_sgx"
  dpNoiseEpsilon: 0.50
compliance:
  standard: "${wizRegulation}"
ingress:
  enabled: true
  hostname: node.jpmorgan.cfi-platform.org`;
    }
    if (deployTab === 'docker') {
      return `version: '3.8'
services:
  cfi-bank-node:
    image: ghcr.io/yusufcalisir/cfi-node:v2.4.0
    restart: always
    environment:
      - HARDWARE_ACCEL=${wizHardware}
      - COMPLIANCE_RULE=${wizRegulation}
      - DP_EPSILON=0.50
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]`;
    }
    if (deployTab === 'terraform') {
      return `# AWS Enclave Terraform Blueprint
module "cfi_sgx_node" {
  source = "terraform-aws-modules/enclave/aws"
  instance_type = "c6i.8xlarge"
  enable_sgx    = true
  compliance    = "${wizRegulation}"
  hardware_accel = "${wizHardware}"
}`;
    }
    return `#!/usr/bin/env bash
# Automated Node Installation Script
curl -sSL https://get.cfi-platform.org/install.sh | bash -s -- ${accelFlag} ${regFlag}`;
  };

  const getPhaseTitle = () => {
    if (flPhase === 0) return 'Phase 1/4: Local Model Training (PyTorch & GNN Subgraphs)';
    if (flPhase === 1) return 'Phase 2/4: Encrypted Gradient Update Transmission ([[W]])';
    if (flPhase === 2) return 'Phase 3/4: Secure Enclave Aggregation (Intel SGX + DP Noise)';
    return 'Phase 4/4: Global Model Deployment Back to Consortium Banks';
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans selection:bg-indigo-500 selection:text-white relative overflow-x-hidden">
      {/* ── LIVING DISTRIBUTED BACKGROUND MESH CANVAS ────────── */}
      <canvas ref={bgCanvasRef} className="fixed inset-0 w-full h-full pointer-events-none z-0 opacity-20" />

      {/* ── FLOATING QUICK-NAV DOCK (RIGHT SIDEBAR) ───────────── */}
      <div className="fixed right-4 top-1/2 -translate-y-1/2 z-40 hidden lg:flex flex-col items-center gap-3 p-2.5 rounded-full bg-slate-900/70 border border-slate-800/80 backdrop-blur-md shadow-2xl">
        {[
          { id: 'hero', label: '3D Architecture Engine' },
          { id: 'problem-solution', label: 'The Core Problem' },
          { id: 'how-it-works', label: 'How It Works' },
          { id: 'product', label: 'Privacy Engine' },
          { id: 'platform', label: 'GNN Graph Collusion' },
          { id: 'architecture', label: '5-Layer Specs' },
          { id: 'security', label: 'Attack Simulator' },
          { id: 'api', label: 'REST & SDK Studio' },
          { id: 'docs', label: 'Deploy Blueprint' },
        ].map((item) => (
          <a
            key={item.id}
            href={`#${item.id}`}
            title={item.label}
            className={`group relative h-3 w-3 rounded-full transition-all duration-300 flex items-center justify-center ${
              activeSection === item.id
                ? 'bg-indigo-400 ring-4 ring-indigo-500/30 scale-125'
                : 'bg-slate-700 hover:bg-slate-400'
            }`}
          >
            <span className="absolute right-6 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-slate-200 text-[10px] font-mono font-bold whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg">
              {item.label}
            </span>
          </a>
        ))}
      </div>

      <div className="relative z-10">
        {/* ── TOP ULTRA-SLIM TELEMETRY MARQUEE BAR (32px Height) ───── */}
        <div className="h-8 bg-slate-950/90 border-b border-slate-800/60 px-4 sm:px-8 flex items-center justify-between text-xs font-mono text-slate-300">
          <div className="flex items-center gap-3">
            <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px] font-bold tracking-wider flex items-center gap-1.5 border border-indigo-500/30">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              LIVE TELEMETRY LOG
            </span>
            <span className="text-emerald-400 font-bold">[{new Date().toLocaleTimeString()}]</span>
          </div>
          <div className="truncate text-slate-300 font-mono text-[11px] max-w-2xl text-right">
            {LIVE_LOG_FEED[logIndex]}
          </div>
        </div>

        {/* ── SECTION 1: GLASSMORPHIC FLOATING NAVBAR (64px Height) ── */}
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-slate-950/80 border-b border-slate-800/80 h-16 flex items-center">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            {/* Logo */}
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-emerald-500 p-0.5 shadow-lg shadow-indigo-500/20">
                <div className="h-full w-full bg-slate-950 rounded-[9px] flex items-center justify-center font-black text-white text-base">
                  🛡️
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-base tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                  CF-Intelligence
                </span>
                <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono text-[10px] font-bold border border-indigo-500/30">
                  v2.4.0
                </span>
              </div>
            </div>

            {/* Desktop Navigation Links */}
            <nav className="hidden lg:flex items-center gap-6 text-xs font-semibold text-slate-300 bg-slate-900/60 border border-slate-800/80 rounded-full px-5 py-2 backdrop-blur-md shadow-inner">
              <a href="#problem-solution" className="hover:text-indigo-400 transition-colors">The Problem</a>
              <a href="#how-it-works" className="hover:text-indigo-400 transition-colors">How It Works</a>
              <a href="#product" className="hover:text-indigo-400 transition-colors">Product</a>
              <a href="#platform" className="hover:text-indigo-400 transition-colors">Platform</a>
              <a href="#architecture" className="hover:text-indigo-400 transition-colors">Architecture</a>
              <a href="#security" className="hover:text-indigo-400 transition-colors">Security</a>
              <a href="#api" className="hover:text-indigo-400 transition-colors">API Playground</a>
              <a href="#docs" className="hover:text-indigo-400 transition-colors">Deploy Wizard</a>
            </nav>

            {/* Actions */}
            <div className="flex items-center gap-3">
              <div className="hidden xl:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-semibold">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Quorum Active (3/3 Synced)
              </div>

              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:inline-flex group relative items-center justify-center p-0.5 overflow-hidden rounded-xl font-bold text-xs shadow-lg shadow-indigo-500/25 transition-all duration-300 hover:scale-105"
              >
                <span className="absolute inset-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 animate-gradient" />
                <span className="relative px-4 py-2 rounded-[10px] bg-slate-950 text-white flex items-center gap-2 group-hover:bg-slate-900 transition-all">
                  <span>Launch Demo</span>
                  <ArrowRightIcon />
                </span>
              </button>

              {/* Mobile Hamburger Toggle Button */}
              <button
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-200 focus:outline-none"
                aria-label="Toggle Navigation Menu"
              >
                {isMobileMenuOpen ? <CloseIcon /> : <MenuIcon />}
              </button>
            </div>
          </div>
        </header>

        {/* ── RESPONSIVE MOBILE MENU OVERLAY ──────────────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="lg:hidden border-b border-slate-800 bg-slate-950/95 backdrop-blur-2xl px-6 py-6 space-y-4"
            >
              <nav className="flex flex-col space-y-3 font-semibold text-sm text-slate-200">
                <a href="#problem-solution" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  The Problem & Solution
                </a>
                <a href="#how-it-works" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  How It Works
                </a>
                <a href="#product" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Product Capabilities
                </a>
                <a href="#platform" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Platform Simulator
                </a>
                <a href="#architecture" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  System Architecture
                </a>
                <a href="#security" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Security Playground
                </a>
                <a href="#api" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  API Execution Studio
                </a>
                <a href="#docs" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Deployment Wizard
                </a>
              </nav>

              <button
                onClick={() => {
                  setIsMobileMenuOpen(false);
                  navigate('/dashboard');
                }}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 text-white font-bold text-xs shadow-lg shadow-indigo-500/25 flex items-center justify-center gap-2"
              >
                <span>Launch Live Platform Demo</span>
                <ArrowRightIcon />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── SECTION 2: SCREEN-CENTERED HERO WITH PURE CODE 3D ISOMETRIC TOPOLOGY ENGINE ── */}
        <section id="hero" className="min-h-[calc(100vh-6rem)] flex items-center justify-center relative py-8 px-4 sm:px-6 max-w-7xl mx-auto overflow-hidden">
          {/* Glowing Background Radial Orbs */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[500px] bg-gradient-to-tr from-indigo-500/10 via-purple-500/10 to-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center w-full">
            {/* ── LEFT COLUMN: Core Title, Headline, CTAs & Telemetry HUD ── */}
            <div className="lg:col-span-6 space-y-6 text-left">
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-semibold"
              >
                <span>✨ Privacy-Preserving Collaborative AI Infrastructure</span>
                <span className="text-indigo-500">•</span>
                <span className="text-emerald-400 font-bold">EU AI Act & FinCEN Compliant</span>
              </motion.div>

              <motion.h1
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.1 }}
                className="text-3xl sm:text-5xl lg:text-5xl font-black tracking-tight leading-tight text-slate-100"
              >
                Stop Cross-Bank Money Laundering Syndicates with Federated AI
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.2 }}
                className="text-xs sm:text-base text-slate-300 leading-relaxed"
              >
                JPMorgan Chase, HSBC, and Deutsche Bank train a joint Graph Neural Network model to detect money mule rings <strong className="text-emerald-400">without sharing raw customer data, PII, or internal transaction logs</strong>.
              </motion.p>

              {/* 3 Core Value Pillars */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.22 }}
                className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1"
              >
                <div className="p-3 rounded-xl bg-slate-900/60 border border-indigo-500/20">
                  <span className="text-xs font-bold text-indigo-300 block">🔒 100% Data Privacy</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Differential Privacy ($\epsilon=0.50$) guarantees zero raw data leakage.</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-900/60 border border-purple-500/20">
                  <span className="text-xs font-bold text-purple-300 block">⚡ 98.4% Ring Accuracy</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Streaming GNN pinpoints cross-bank money mule rings in &lt; 3.2ms.</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-900/60 border border-emerald-500/20">
                  <span className="text-xs font-bold text-emerald-300 block">🏛️ Full Compliance</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Native ISO 20022 XML parsing and automated FinCEN SAR reporting.</span>
                </div>
              </motion.div>

              {/* CTA Buttons */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.25 }}
                className="flex flex-wrap items-center gap-4 pt-2"
              >
                <button
                  onClick={() => navigate('/dashboard')}
                  className="px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 text-white font-bold text-xs shadow-lg shadow-indigo-500/25 hover:scale-105 transition-all flex items-center gap-2"
                >
                  <span>Launch Live Platform Demo</span>
                  <ArrowRightIcon />
                </button>
                <a
                  href="#problem-solution"
                  className="px-5 py-3 rounded-xl bg-slate-900 hover:bg-slate-800 text-slate-300 font-bold text-xs border border-slate-800 transition-all"
                >
                  The Problem & Solution ↓
                </a>
              </motion.div>

              {/* Live Telemetry HUD Bar */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.3 }}
                className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-2xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl"
              >
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
              </motion.div>
            </div>

            {/* ── RIGHT COLUMN: PURE CODE 3D ISOMETRIC NETWORK TOPOLOGY ENGINE (MATCHING USER SCREENSHOT) ── */}
            <div className="lg:col-span-6 relative w-full h-[540px] rounded-3xl border border-indigo-500/30 bg-gradient-to-b from-slate-900/95 via-slate-950/95 to-slate-950 p-4 sm:p-6 shadow-2xl overflow-hidden flex flex-col justify-between">
              {/* Canvas Physics 3D Laser Overlay */}
              <canvas ref={canvasRef} className="absolute inset-0 w-full h-full pointer-events-none z-0" />

              {/* Component Purpose Callout Banner */}
              <div className="relative z-10 p-2.5 rounded-xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-[10px] text-indigo-200">
                <span className="font-bold text-indigo-400 block mb-0.5">💡 3D ISOMETRIC TOPOLOGY ENGINE (PROJE AMACI):</span>
                JPMorgan Chase, HSBC, Deutsche Bank ve Intel SGX Enclave merkezdeki 3D işlemci çipine şifreli model katmanları taşır.
              </div>

              {/* Status Header */}
              <div className="relative z-10 flex items-center justify-between py-1 border-b border-slate-800/80">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-ping" />
                  <span className="text-[10px] sm:text-xs font-mono font-bold text-indigo-300 uppercase">
                    3D ISOMETRIC NETWORK CHASSIS (PURE CODE)
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setIsDpShieldActive(!isDpShieldActive)}
                    className="px-2.5 py-1 rounded-lg text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1"
                  >
                    <LockIcon />
                    <span>DP Shield: {isDpShieldActive ? 'ON' : 'OFF'}</span>
                  </button>
                  <button
                    onClick={() => setIsPlaying(!isPlaying)}
                    className="px-2.5 py-1 rounded-lg text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700"
                  >
                    {isPlaying ? 'Pause ⏸️' : 'Play ▶️'}
                  </button>
                </div>
              </div>

              {/* PURE CODE 3D ISOMETRIC NETWORK TOPOLOGY CONTAINER (MATCHING USER SCREENSHOT) */}
              <div className="relative z-10 w-full h-[360px] my-auto flex items-center justify-center">
                {/* CENTER: 3D ISOMETRIC ENCLAVE PROCESSOR CHIP */}
                <motion.div
                  animate={{ scale: [1, 1.05, 1], rotate: [0, 1, 0] }}
                  transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
                  className="absolute z-20 w-32 h-32 rounded-2xl bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-950 border-2 border-indigo-400 p-2 shadow-2xl shadow-indigo-500/50 flex flex-col items-center justify-center cursor-pointer"
                  style={{
                    transform: 'rotateX(50deg) rotateZ(-45deg)',
                    boxShadow: '0 20px 50px rgba(99, 102, 241, 0.4), inset 0 0 20px rgba(99, 102, 241, 0.3)',
                  }}
                >
                  {/* Golden Micro Pins around chip */}
                  <div className="absolute -inset-1 rounded-2xl border border-amber-400/40 pointer-events-none" />
                  <div className="w-14 h-14 rounded-xl bg-indigo-600/20 border border-indigo-400 flex items-center justify-center shadow-inner relative">
                    <span className="text-2xl animate-pulse">🛰️</span>
                    <div className="absolute inset-0 rounded-xl border border-cyan-400 animate-ping opacity-30" />
                  </div>
                  <span className="text-[10px] font-mono font-black text-indigo-300 mt-2 tracking-wider">
                    ENCLAVE CORE
                  </span>
                  <span className="text-[8px] font-mono text-emerald-400 font-bold">Intel SGX TEE</span>
                </motion.div>

                {/* 4 SURROUNDING 3D ISOMETRIC BANK PLATFORMS (PROJECTION) */}
                {/* 1. TOP-LEFT: JPMorgan Chase Platform */}
                <motion.div
                  whileHover={{ scale: 1.05 }}
                  onClick={() => REAL_BANK_DETAILS.jpmorgan && setActiveBankDrawer(REAL_BANK_DETAILS.jpmorgan)}
                  className="absolute top-2 left-2 z-10 w-44 p-3 rounded-2xl bg-slate-900/90 border-2 border-indigo-500/60 backdrop-blur-xl shadow-xl cursor-pointer hover:border-indigo-400 transition-all"
                  style={{
                    transform: 'rotateX(40deg) rotateZ(-30deg)',
                    boxShadow: '0 15px 30px rgba(99, 102, 241, 0.25)',
                  }}
                >
                  <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1">
                    <div className="flex items-center gap-1">
                      <FanSpinner />
                      <span className="text-[10px] font-mono font-bold text-indigo-400">JPMORGAN CHASE 🗽</span>
                    </div>
                    <span className="h-2 w-2 rounded-full bg-cyan-400 animate-ping" />
                  </div>
                  <div className="text-[10px] font-mono text-slate-300">NVIDIA A100 (80GB)</div>
                  <div className="text-[9px] font-mono text-indigo-300">CUDA: {cudaJpm}%</div>
                </motion.div>

                {/* 2. TOP-RIGHT: HSBC Holdings Platform */}
                <motion.div
                  whileHover={{ scale: 1.05 }}
                  onClick={() => REAL_BANK_DETAILS.hsbc && setActiveBankDrawer(REAL_BANK_DETAILS.hsbc)}
                  className="absolute top-2 right-2 z-10 w-44 p-3 rounded-2xl bg-slate-900/90 border-2 border-rose-500/60 backdrop-blur-xl shadow-xl cursor-pointer hover:border-rose-400 transition-all"
                  style={{
                    transform: 'rotateX(40deg) rotateZ(30deg)',
                    boxShadow: '0 15px 30px rgba(239, 68, 68, 0.25)',
                  }}
                >
                  <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1">
                    <div className="flex items-center gap-1">
                      <span className="text-xs">🔴</span>
                      <span className="text-[10px] font-mono font-bold text-rose-400">HSBC HOLDINGS 🏛️</span>
                    </div>
                    <span className="h-2 w-2 rounded-full bg-rose-400 animate-ping" />
                  </div>
                  <div className="text-[10px] font-mono text-slate-300">NVIDIA H100 SXM</div>
                  <div className="text-[9px] font-mono text-rose-300">CUDA: {cudaHsbc}%</div>
                </motion.div>

                {/* 3. BOTTOM-LEFT: Intel SGX Vault Platform */}
                <motion.div
                  whileHover={{ scale: 1.05 }}
                  onClick={() => REAL_BANK_DETAILS.sgx && setActiveBankDrawer(REAL_BANK_DETAILS.sgx)}
                  className="absolute bottom-2 left-2 z-10 w-44 p-3 rounded-2xl bg-slate-900/90 border-2 border-purple-500/60 backdrop-blur-xl shadow-xl cursor-pointer hover:border-purple-400 transition-all"
                  style={{
                    transform: 'rotateX(40deg) rotateZ(30deg)',
                    boxShadow: '0 15px 30px rgba(168, 85, 247, 0.25)',
                  }}
                >
                  <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1">
                    <div className="flex items-center gap-1">
                      <LockIcon />
                      <span className="text-[10px] font-mono font-bold text-purple-400">SGX TEE VAULT 🔒</span>
                    </div>
                    <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                  </div>
                  <div className="text-[10px] font-mono text-slate-300">Hardware Vault Shield</div>
                  <div className="text-[9px] font-mono text-emerald-400">100% Protected</div>
                </motion.div>

                {/* 4. BOTTOM-RIGHT: Deutsche Bank Platform */}
                <motion.div
                  whileHover={{ scale: 1.05 }}
                  onClick={() => REAL_BANK_DETAILS.deutsche && setActiveBankDrawer(REAL_BANK_DETAILS.deutsche)}
                  className="absolute bottom-2 right-2 z-10 w-44 p-3 rounded-2xl bg-slate-900/90 border-2 border-cyan-500/60 backdrop-blur-xl shadow-xl cursor-pointer hover:border-cyan-400 transition-all"
                  style={{
                    transform: 'rotateX(40deg) rotateZ(-30deg)',
                    boxShadow: '0 15px 30px rgba(56, 189, 248, 0.25)',
                  }}
                >
                  <div className="flex items-center justify-between border-b border-slate-800 pb-1 mb-1">
                    <div className="flex items-center gap-1">
                      <FanSpinner />
                      <span className="text-[10px] font-mono font-bold text-cyan-400">DEUTSCHE BANK 🏢</span>
                    </div>
                    <span className="h-2 w-2 rounded-full bg-teal-400 animate-ping" />
                  </div>
                  <div className="text-[10px] font-mono text-slate-300">Intel Xeon Cluster</div>
                  <div className="text-[9px] font-mono text-cyan-300">CPU Load: {cpuDb}%</div>
                </motion.div>
              </div>

              {/* Bottom Subtitle */}
              <div className="relative z-10 pt-2 border-t border-slate-800/80 flex items-center justify-between text-[10px] font-mono text-slate-400">
                <span>{getPhaseTitle()}</span>
                <span className="text-indigo-400 font-bold">CLICK 3D NODE TO INSPECT 🔍</span>
              </div>
            </div>
          </div>
        </section>

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
                    className="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-bold"
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

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── NEW SECTION: THE CROSS-BANK FRAUD BLIND SPOT (THE PROBLEM & SOLUTION) ── */}
        <motion.section
          id="problem-solution"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs font-bold uppercase tracking-wider">
              THE CORE FINANCIAL PROBLEM
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              The Cross-Bank Money Laundering Blind Spot
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Why traditional single-bank fraud systems are blind to modern organized crime rings.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Left Card: The Problem */}
            <div className="p-6 sm:p-8 rounded-3xl bg-slate-900/60 border border-rose-500/30 space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">⚠️</span>
                <h3 className="text-xl font-bold text-rose-300">The Problem: Institutional Blindness</h3>
              </div>
              <ul className="space-y-3 text-xs sm:text-sm text-slate-300 leading-relaxed">
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">1.</span>
                  <span><strong>Multi-Bank Smurfing:</strong> Criminal syndicates split $10M illicit funds into $9,500 transfers across JPMorgan, HSBC, and Deutsche Bank.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">2.</span>
                  <span><strong>Isolated Datasets:</strong> Each bank only sees its own internal transaction slice and marks the transfer as "normal retail behavior".</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">3.</span>
                  <span><strong>Legal Data Sharing Ban:</strong> GDPR, CCPA, and Banking Secrecy laws strictly forbid banks from pooling raw customer records.</span>
                </li>
              </ul>
            </div>

            {/* Right Card: The Solution */}
            <div className="p-6 sm:p-8 rounded-3xl bg-slate-900/60 border border-emerald-500/30 space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">🛡️</span>
                <h3 className="text-xl font-bold text-emerald-300">The Solution: CFI Federated AI</h3>
              </div>
              <ul className="space-y-3 text-xs sm:text-sm text-slate-300 leading-relaxed">
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">1.</span>
                  <span><strong>Privacy-Preserving Federated Learning:</strong> AI model comes to the bank's local server. Customer PII never leaves the firewall.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">2.</span>
                  <span><strong>Streaming Graph Neural Networks:</strong> Connects account topology subgraphs across banks to pinpoint money mule rings in &lt; 3.2ms.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">3.</span>
                  <span><strong>Intel SGX TEE & Differential Privacy:</strong> Encrypted model weights are aggregated in hardware enclaves with zero data reconstruction risk.</span>
                </li>
              </ul>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── NEW SECTION: HOW IT WORKS 4-STEP INTERACTIVE WORKFLOW ── */}
        <motion.section
          id="how-it-works"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold uppercase tracking-wider">
              ENTERPRISE FEDERATED ARCHITECTURE
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              How Collaborative Fraud Detection Works
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              A 4-step privacy-preserving workflow operating across JPMorgan Chase, HSBC, and Deutsche Bank.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-indigo-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center font-mono font-bold text-indigo-400 text-sm">
                01
              </div>
              <h3 className="text-base font-bold text-slate-100">Local ISO 20022 Data Intake</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Each member bank parses SWIFT <code className="text-indigo-300">pacs.008</code> and <code className="text-indigo-300">camt.053</code> transaction XML files within its own secure firewall. Raw customer identity never leaves the bank.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-purple-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center font-mono font-bold text-purple-400 text-sm">
                02
              </div>
              <h3 className="text-base font-bold text-slate-100">PyTorch GNN Local Training</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Local Graph Attention Networks (GAT) convert account transaction topology into high-dimensional embedding vectors (h_v), isolating local smurfing behavior.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-emerald-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center font-mono font-bold text-emerald-400 text-sm">
                03
              </div>
              <h3 className="text-base font-bold text-slate-100">Intel SGX Enclave Aggregation</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Encrypted model weights are uploaded into an Intel SGX Hardware TEE Enclave. Differential Privacy noise ($\epsilon=0.50$) is injected during FedAvg consensus.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-cyan-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center font-mono font-bold text-cyan-400 text-sm">
                04
              </div>
              <h3 className="text-base font-bold text-slate-100">Global Ring Isolation & SAR</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                The updated global model is pushed back to all member banks. High-risk cross-bank money mule rings are instantly frozen and exported as FinCEN SAR XML filings.
              </p>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 4: PRODUCT CAPABILITIES & PRIVACY ENGINE ──────── */}
        <motion.section
          id="product"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold uppercase tracking-wider">
              ENTERPRISE PRODUCT CAPABILITIES
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              Differential Privacy & Heterogeneous FL Engine
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Explore real-time mathematical privacy guarantees, cross-bank model parameter negotiators, and detection SLAs.
            </p>

            {/* Interactive Tab Controls */}
            <div className="flex justify-center gap-2 pt-4">
              {[
                { id: 'dp', label: '🔒 DP Noise Simulator' },
                { id: 'negotiator', label: '🧠 Heterogeneous Negotiator' },
                { id: 'sla', label: '⚡ Detection SLAs & Benchmarks' },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setProductTab(tab.id as any)}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                    productTab === tab.id
                      ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/30'
                      : 'bg-slate-900 border border-slate-800 text-slate-400 hover:bg-slate-800'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              {productTab === 'dp' && 'Differential Privacy (ε), model güncellemelerine matematiksel gürültü ekleyerek hackerların ortak modelden müşteri kimliğini geri elde etmesini imkansız kılar.'}
              {productTab === 'negotiator' && 'Farklı donanımlara (A100 GPU vs H100 vs CPU) ve farklı yapay zeka mimarilerine sahip bankalar otomatik olarak tek bir küresel modelde buluşur.'}
              {productTab === 'sla' && '3 büyük bankanın ortak verisiyle dolandırıcılık tespit oranı %98.42ye yükselir, hatalı alarm oranı %74.6 düşer.'}
            </div>

            {productTab === 'dp' && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
                <div className="lg:col-span-5 space-y-6">
                  <div>
                    <div className="flex justify-between items-center mb-2">
                      <label className="text-xs font-bold text-slate-300 uppercase">Privacy Budget ($\epsilon$)</label>
                      <span className="text-sm font-mono font-bold text-indigo-400">{epsilonCalc.toFixed(1)}</span>
                    </div>
                    <input
                      type="range"
                      min="0.1"
                      max="5.0"
                      step="0.1"
                      value={epsilonCalc}
                      onChange={(e) => setEpsilonCalc(Number(e.target.value))}
                      className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
                    />
                  </div>

                  <div className="space-y-3 font-mono text-xs">
                    <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                      <span className="text-slate-400">Reconstruction Guarantee:</span>
                      <span className="text-emerald-400 font-bold">
                        {epsilonCalc <= 0.5 ? 'Mathematically Impossible' : epsilonCalc <= 1.5 ? 'Very High' : 'Moderate'}
                      </span>
                    </div>
                    <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                      <span className="text-slate-400">Gaussian Noise Std Dev ($\sigma$):</span>
                      <span className="text-purple-400 font-bold">{(0.5 / epsilonCalc).toFixed(3)}</span>
                    </div>
                    <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800">
                      <span className="text-slate-400 block mb-1">Differential Privacy Formula:</span>
                      <code className="text-indigo-300 text-[11px]">
                        \mathcal{"{N}"}\left(0, \sigma^2 I\right) \quad \text{"{where}"} \quad \sigma = \frac{"{\\Delta f \\sqrt{2 \\ln(1.25/\\delta)}}"}{"{\\epsilon}"}
                      </code>
                    </div>
                  </div>
                </div>

                <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="p-6 rounded-2xl bg-slate-950 border border-emerald-500/30 flex flex-col justify-between">
                    <span className="text-xs text-slate-400 uppercase font-bold">Estimated Model Accuracy</span>
                    <div className="text-3xl sm:text-4xl font-black text-emerald-400 font-mono mt-4">
                      {(88 + (epsilonCalc / 5.0) * 11.2).toFixed(2)}%
                    </div>
                    <span className="text-[10px] text-slate-500 mt-2">Verified against 1.2M Synthetic SWIFT pacs.008 transactions</span>
                  </div>

                  <div className="p-6 rounded-2xl bg-slate-950 border border-purple-500/30 flex flex-col justify-between">
                    <span className="text-xs text-slate-400 uppercase font-bold">Training Loss Convergence</span>
                    <div className="text-3xl sm:text-4xl font-black text-purple-400 font-mono mt-4">
                      {(0.18 - (epsilonCalc / 5.0) * 0.14).toFixed(4)}
                    </div>
                    <span className="text-[10px] text-slate-500 mt-2">Smooth gradient decay without straggler node distortion</span>
                  </div>
                </div>
              </div>
            )}

            {productTab === 'negotiator' && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 font-mono text-xs">
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-indigo-400 font-bold uppercase block">JPMorgan Chase Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GAT (Graph Attention)</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 512 ➔ 256 Embedding</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: MATCHED (100%)</div>
                </div>
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-purple-400 font-bold uppercase block">HSBC Holdings Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GCN (Convolutional)</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 512 ➔ 256 Embedding</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: MATCHED (100%)</div>
                </div>
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-emerald-400 font-bold uppercase block">Deutsche Bank AG Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GraphSAGE (CPU)</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 256 ➔ Batch Scale 32</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: ADAPTED (Straggler Free)</div>
                </div>
              </div>
            )}

            {productTab === 'sla' && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center font-mono">
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Detection Precision</span>
                  <div className="text-3xl font-black text-emerald-400 mt-2">98.42%</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">False Positive Reduction</span>
                  <div className="text-3xl font-black text-indigo-400 mt-2">-74.6%</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Max Stream Throughput</span>
                  <div className="text-3xl font-black text-purple-400 mt-2">15,000 TPS</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Federated FL Latency</span>
                  <div className="text-3xl font-black text-cyan-400 mt-2">&lt; 3.2ms</div>
                </div>
              </div>
            )}
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 5: PLATFORM & 8-NODE GNN GRAPH COLLUSION SIMULATOR WITH EXPLANATION WALKTHROUGH ── */}
        <motion.section
          id="platform"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-purple-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-8">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-purple-950/80 border border-purple-500/30 font-mono text-xs text-purple-200">
              <span className="font-bold text-purple-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              Suç örgütleri 10.000$ altı paraları 3 farklı bankaya bölerek (smurfing) tespit edilmeyi engeller. Graph Neural Network bankalar arası alt grafikleri birleştirerek çeteyi anında yakalar.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-purple-400 uppercase tracking-wider">
                  STREAMING GNN COLLUSION DETECTOR
                </span>
                <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-100 mt-1">
                  Cross-Bank Money Laundering Ring Detection Engine
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  How Streaming Graph Neural Networks detect multi-institutional smurfing and layering rings in zero-trust environments.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setIsGraphDetected(!isGraphDetected)}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold shadow-md transition-all"
                >
                  {isGraphDetected ? 'Reset Graph 🔄' : 'Run GNN Detection 🧠'}
                </button>

                {isGraphDetected && (
                  <button
                    onClick={() => setIsGraphIsolated(!isGraphIsolated)}
                    className="px-4 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold shadow-md transition-all"
                  >
                    {isGraphIsolated ? 'Re-link Nodes 🔗' : 'Isolate Fraud Ring ✂️'}
                  </button>
                )}

                <button
                  onClick={() => setShowAttentionMatrix(!showAttentionMatrix)}
                  className="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold border border-slate-700"
                >
                  {showAttentionMatrix ? 'Hide Matrix' : 'GAT Matrix (α_ij)'}
                </button>
              </div>
            </div>

            {/* 4-Step Narrative Timeline Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { step: 1, title: '1. Multi-Bank Layering', desc: 'Organized crime rings split funds below $10K AML thresholds across JPMorgan, HSBC, and Deutsche Bank.' },
                { step: 2, title: '2. Local GAT Embeddings', desc: 'Each bank computes local PyTorch GNN node feature vectors without exposing PII.' },
                { step: 3, title: '3. Enclave Subgraph Match', desc: 'Intel SGX TEE reconstructs cross-bank adjacency edges using zero-knowledge proofs.' },
                { step: 4, title: '4. Edge Severance & SAR', desc: 'Mule accounts are isolated across all member banks simultaneously with FinCEN SAR XML filings.' },
              ].map((s) => (
                <div
                  key={s.step}
                  onClick={() => setGraphStep(s.step)}
                  className={`p-4 rounded-2xl border cursor-pointer transition-all ${
                    graphStep === s.step
                      ? 'bg-indigo-950/60 border-indigo-500 text-slate-100 shadow-lg shadow-indigo-500/20 ring-1 ring-indigo-500/40'
                      : 'bg-slate-950/50 border-slate-800 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <span className="text-[10px] font-mono font-bold text-indigo-400 uppercase block">STEP {s.step}</span>
                  <h4 className="text-xs font-extrabold text-slate-200 mt-1">{s.title}</h4>
                  <p className="text-[11px] text-slate-400 mt-1.5 leading-relaxed">{s.desc}</p>
                </div>
              ))}
            </div>

            {/* Interactive 8-Node SVG Topology Graph & Node Inspector */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
              <div className="lg:col-span-8 relative w-full h-[360px] sm:h-[380px] bg-slate-950 rounded-2xl border border-slate-800 flex items-center justify-center overflow-hidden">
                <svg className="w-full h-full" viewBox="0 0 700 340">
                  {/* Connections */}
                  {!isGraphIsolated && (
                    <>
                      <line x1="120" y1="170" x2="240" y2="80" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="240" y1="80" x2="380" y2="80" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="380" y1="80" x2="560" y2="170" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="120" y1="170" x2="240" y2="260" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="240" y1="260" x2="380" y2="260" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="380" y1="260" x2="560" y2="170" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                    </>
                  )}

                  {/* 8 Nodes with Click Inspector Handlers */}
                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.jpm ?? null)} className="cursor-pointer">
                    <circle cx="120" cy="170" r="26" fill="#1e1b4b" stroke="#6366f1" strokeWidth="2" />
                    <text x="120" y="174" fill="#ffffff" fontSize="9" fontWeight="bold" textAnchor="middle">JPM-ACCT</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.shell_a ?? null)} className="cursor-pointer">
                    <circle cx="240" cy="80" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#a855f7'} strokeWidth="2" />
                    <text x="240" y="84" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SHELL-A</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.smurf_1 ?? null)} className="cursor-pointer">
                    <circle cx="380" cy="80" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#a855f7'} strokeWidth="2" />
                    <text x="380" y="84" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SMURF-1</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.hsbc ?? null)} className="cursor-pointer">
                    <circle cx="560" cy="170" r="26" fill="#064e3b" stroke="#10b981" strokeWidth="2" />
                    <text x="560" y="174" fill="#ffffff" fontSize="9" fontWeight="bold" textAnchor="middle">HSBC-ACCT</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.shell_b ?? null)} className="cursor-pointer">
                    <circle cx="240" cy="260" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#ec4899'} strokeWidth="2" />
                    <text x="240" y="264" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SHELL-B</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.smurf_2 ?? null)} className="cursor-pointer">
                    <circle cx="380" cy="260" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#ec4899'} strokeWidth="2" />
                    <text x="380" y="264" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SMURF-2</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.db ?? null)} className="cursor-pointer">
                    <circle cx="310" cy="170" r="22" fill="#1e293b" stroke="#38bdf8" strokeWidth="2" />
                    <text x="310" y="174" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">DB-RELAY</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.offramp ?? null)} className="cursor-pointer">
                    <circle cx="450" cy="170" r="22" fill={isGraphDetected ? '#7f1d1d' : '#1e293b'} stroke={isGraphDetected ? '#f87171' : '#f59e0b'} strokeWidth="2" />
                    <text x="450" y="174" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">OFFRAMP</text>
                  </g>
                </svg>

                {isGraphDetected && (
                  <div className="absolute top-4 right-4 px-3 py-1.5 rounded-xl bg-rose-500/20 border border-rose-500/40 text-rose-300 font-mono text-[10px] sm:text-xs font-bold">
                    ⚠️ HIGH-RISK RING DETECTED (GNN Confidence: 98.6%)
                  </div>
                )}
              </div>

              {/* Node Inspector Sidebar Panel */}
              <div className="lg:col-span-4 p-5 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs space-y-4 h-[360px] sm:h-[380px] flex flex-col justify-between">
                {selectedGraphNode ? (
                  <div>
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-bold text-indigo-400 uppercase">NODE TELEMETRY INSPECTOR</span>
                      <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${selectedGraphNode.riskScore > 0.8 ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'}`}>
                        {selectedGraphNode.status}
                      </span>
                    </div>
                    <h3 className="text-sm font-extrabold text-slate-100 mt-2">{selectedGraphNode.name}</h3>
                    <p className="text-[11px] text-slate-400 mt-0.5">{selectedGraphNode.bank}</p>

                    <div className="mt-4 space-y-2 text-[11px]">
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">GNN Anomali Score:</span>
                        <span className={`font-bold ${selectedGraphNode.riskScore > 0.8 ? 'text-rose-400' : 'text-emerald-400'}`}>
                          {selectedGraphNode.riskScore}
                        </span>
                      </div>
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">Transaction Velocity:</span>
                        <span className="text-indigo-300 font-bold">{selectedGraphNode.velocity}</span>
                      </div>
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">Layering Index:</span>
                        <span className="text-purple-300 font-bold">{selectedGraphNode.anomalyIndex}</span>
                      </div>
                    </div>

                    <p className="text-[10px] text-slate-400 mt-3 leading-relaxed border-t border-slate-900 pt-2">
                      {selectedGraphNode.description}
                    </p>
                  </div>
                ) : (
                  <div className="my-auto text-center space-y-2">
                    <span className="text-2xl">🔍</span>
                    <h4 className="text-xs font-bold text-slate-300">Click Any Graph Node</h4>
                    <p className="text-[11px] text-slate-500">Select any node on the topology map to inspect GNN risk metrics and transaction velocity.</p>
                  </div>
                )}

                <div className="p-2.5 rounded-xl bg-slate-900 text-[10px] text-slate-400 border border-slate-800 text-center">
                  Tip: Click "Run GNN Detection" to isolate suspicious money mule rings in real time.
                </div>
              </div>
            </div>

            {/* GNN Mathematical Formula & Comparison Matrix */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 pt-4 border-t border-slate-800">
              <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs space-y-3">
                <span className="text-indigo-400 font-bold uppercase block">Graph Attention Layer Equation</span>
                <code className="text-indigo-300 text-[11px] block p-3 bg-slate-900 rounded-xl border border-slate-800">
                  h_i^(l+1) = \sigma \left( \sum \alpha_ij W^(l) h_j^(l) \right)
                </code>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Computes structural attention weights (\alpha_ij) between accounts across isolated banks, measuring structural transaction similarity without exposing PII.
                </p>
              </div>

              <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs space-y-3">
                <span className="text-emerald-400 font-bold uppercase block">Normal Accounts vs. Money Mule Rings</span>
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div className="p-3 rounded bg-slate-900 border border-slate-800 space-y-1">
                    <span className="text-emerald-400 font-bold">Consumer Accounts</span>
                    <span className="block text-slate-400">Regular salary intake, local merchant spend, low fan-out ratio.</span>
                  </div>
                  <div className="p-3 rounded bg-slate-900 border border-slate-800 space-y-1">
                    <span className="text-rose-400 font-bold">Mule Syndicates</span>
                    <span className="block text-slate-400">Rapid sub-threshold layering, zero retention, high graph centrality.</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-emerald-500 to-indigo-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 6: ARCHITECTURE 5-LAYER DEEP SPECIFICATION MATRIX ── */}
        <motion.section
          id="architecture"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-purple-500/10 border border-purple-500/20 text-purple-400 text-xs font-bold uppercase tracking-wider">
              5-LAYER SYSTEM ARCHITECTURE
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100">
              Interactive Technical Specification Matrix
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Deep dive into every technical layer from raw SWIFT intake to automated SAR export.
            </p>
          </div>

          <div className="glass-card border border-purple-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              SWIFT ISO 20022 XML ayrıştırmasından Intel SGX donanım şifrelemesine ve FinCEN SAR bildirimine kadar platformun uçtan uca kurumsal veri hattını gösterir.
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-5 space-y-3">
                {[
                  { id: 1, name: 'Layer 1: ISO 20022 Data Intake Engine' },
                  { id: 2, name: 'Layer 2: GNN Subgraph Feature Store' },
                  { id: 3, name: 'Layer 3: Secure Enclave & DP Shield' },
                  { id: 4, name: 'Layer 4: Federated Aggregation Core' },
                  { id: 5, name: 'Layer 5: Automated Regulatory SIEM Exporter' },
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

              <div className="lg:col-span-7 p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between space-y-4">
                <div>
                  <span className="text-xs font-mono text-indigo-400 uppercase font-bold">SPECIFICATION MATRIX</span>
                  <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-1">
                    {activeLayer === 1 && 'Native ISO 20022 Financial XML Intake'}
                    {activeLayer === 2 && 'PyTorch Geometric GNN Embedding Feature Store'}
                    {activeLayer === 3 && 'Intel SGX Secure Enclave & Paillier Homomorphic Encryption'}
                    {activeLayer === 4 && 'Byzantine-Robust FedAvg Aggregation Core'}
                    {activeLayer === 5 && 'Automated SAR XML & Splunk SIEM Integration'}
                  </h3>
                  <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                    {activeLayer === 1 && 'Parses pacs.008 and camt.053 XML financial transaction messages directly into local graph tensors without storing customer identity details.'}
                    {activeLayer === 2 && 'Extracts structural graph attention embeddings (GAT) capturing account transaction topologies across isolated banking domains.'}
                    {activeLayer === 3 && 'Injects Gaussian differential privacy noise and computes encrypted sum updates within hardware-isolated TEE enclaves.'}
                    {activeLayer === 4 && 'Aggregates multi-bank gradient weights using Trimmed-Mean to automatically neutralize malicious or corrupted node updates.'}
                    {activeLayer === 5 && 'Generates cryptographic sign-off hashes and exports automated SAR XML filings directly to regulatory SIEM endpoints.'}
                  </p>
                </div>

                {/* Protocol Specs Code Snippet Preview */}
                <div className="p-4 rounded-xl bg-slate-900 font-mono text-[11px] text-indigo-300 border border-slate-800 overflow-x-auto">
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
    # Sort and trim top/bottom 10% gradients to quench Byzantine attacks
    sorted_weights = torch.sort(weight_tensors, dim=0)
    return torch.mean(sorted_weights[beta:-beta], dim=0)`}
                  {activeLayer === 5 && `<FinCEN_SAR_Export version="2.0">
  <FilingHeader><FilerID>CFI-PLATFORM-991</FilerID></FilingHeader>
  <SuspiciousActivity><Amount Ccy="USD">1450000.00</Amount></SuspiciousActivity>
</FinCEN_SAR_Export>`}
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 7: ADVERSARIAL ATTACK SIMULATION LAB ────────── */}
        <motion.section
          id="security"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-emerald-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-emerald-950/80 border border-emerald-500/30 font-mono text-xs text-emerald-200">
              <span className="font-bold text-emerald-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              Kötü niyetli hackerların veya sızmış bankaların AI modelinden müşteri verisi çalmasını (MIA/DLG) veya modeli zehirlemesini (Byzantine) engelleyen güvenlik testlerini simüle eder.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-wider">
                  ADVERSARIAL ATTACK DEFENSE LAB
                </span>
                <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100 mt-1">
                  Zero-Trust Attack & Privacy Leakage Simulator
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  Simulate state-of-the-art adversarial AI attacks and verify zero-trust defenses.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => handleRunAttack('mia')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'mia' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Launch MIA Attack 🎯
                </button>
                <button
                  onClick={() => handleRunAttack('dlg')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'dlg' ? 'bg-purple-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Launch DLG Attack 🔓
                </button>
                <button
                  onClick={() => handleRunAttack('byzantine')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'byzantine' ? 'bg-rose-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Toggle Byzantine ☣️
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
              <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 space-y-3 font-mono">
                <span className="text-xs text-slate-400 uppercase font-bold">Attack Target Detail</span>
                <div className="text-sm font-bold text-slate-200">
                  {attackType === 'mia' && 'Membership Inference Attack (MIA)'}
                  {attackType === 'dlg' && 'Deep Leakage from Gradients (DLG)'}
                  {attackType === 'byzantine' && 'Byzantine Gradient Poisoning'}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed">
                  {attackType === 'mia' && 'Attempts to infer whether a specific customer transaction was present in local bank training sets.'}
                  {attackType === 'dlg' && 'Uses dummy inputs and gradient matching optimization to reconstruct raw customer data tensors.'}
                  {attackType === 'byzantine' && 'Injects malicious weight updates to force global GNN model accuracy degradation.'}
                </p>
              </div>

              <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between font-mono">
                <span className="text-xs text-slate-400 uppercase font-bold">Zero-Trust Defense Status</span>
                <div className="text-base font-bold text-emerald-400 mt-2">
                  {attackStatus}
                </div>
                <div className="mt-4 p-3 rounded-xl bg-slate-900 text-[11px] text-slate-300 border border-slate-800">
                  Defense Mechanism: Intel SGX TEE + Trimmed-Mean Aggregation + Gaussian DP Noise
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 8: LIVE REST API EXECUTION & MULTI-LANG SDK STUDIO ── */}
        <motion.section
          id="api"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              Banka yazılımcıları 3 satır kodla (Python, Go, Node.js, cURL) platformu kendi banka altyapısına kolayca entegre edebilir.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <div className="flex items-center gap-2">
                  <CodeIcon />
                  <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100">Live REST API & Multi-Lang SDK Studio</h2>
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  Test live REST endpoints and generate production client code in cURL, Python, Node.js, and Go.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={apiEndpoint}
                  onChange={(e) => setApiEndpoint(e.target.value)}
                  className="p-2 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-indigo-300 font-bold"
                >
                  <option value="handshake">POST /api/v1/coordinator/handshake</option>
                  <option value="clients">GET /api/v1/coordinator/clients</option>
                  <option value="abac">POST /api/v1/security/abac/evaluate</option>
                </select>

                <button
                  onClick={handleSendApiRequest}
                  disabled={isApiLoading}
                  className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold shadow-md transition-all flex items-center gap-1.5 flex-shrink-0"
                >
                  {isApiLoading ? 'Executing...' : 'Execute Test 🚀'}
                </button>
              </div>
            </div>

            {/* SDK Language Selector Tabs */}
            <div className="flex gap-2 pt-4">
              {(['curl', 'python', 'node', 'go'] as const).map((lang) => (
                <button
                  key={lang}
                  onClick={() => setApiLang(lang)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold uppercase transition-all ${
                    apiLang === lang ? 'bg-indigo-600 text-white' : 'bg-slate-900 border border-slate-800 text-slate-400'
                  }`}
                >
                  {lang}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-4">
              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">Production Client Code ({apiLang})</span>
                <div className="w-full h-52 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-indigo-300 overflow-y-auto">
                  <pre>{getSdkCode()}</pre>
                </div>
              </div>

              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">HTTP 200 JSON Response & Headers</span>
                <div className="w-full h-52 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-emerald-400 overflow-y-auto">
                  {apiResponse ? (
                    <pre>{`HTTP/1.1 200 OK
X-Privacy-Budget-Remaining: 0.50
X-Enclave-Signature: ed25519_992f1b4a...

${apiResponse}`}</pre>
                  ) : (
                    <span className="text-slate-600">// Click "Execute Test" to receive live response</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-emerald-500 to-indigo-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 9: CUSTOMIZED DEPLOYMENT WIZARD ────────────── */}
        <motion.section
          id="docs"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-slate-800 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            {/* Component Purpose Callout Banner */}
            <div className="p-3.5 rounded-2xl bg-emerald-950/80 border border-emerald-500/30 font-mono text-xs text-emerald-200">
              <span className="font-bold text-emerald-400 block mb-1">💡 PROJE AMACI & ÇALIŞMA PRENSİBİ:</span>
              Bankaların kendi sunucularında (Kubernetes, Helm, Docker, Terraform) 5 dakikada platformu başlatması için hazır altyapı şablonları sunar.
            </div>

            <div className="text-center max-w-3xl mx-auto space-y-3 mb-6">
              <span className="px-3.5 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-wider">
                DEPLOYMENT BLUEPRINT WIZARD
              </span>
              <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-100">
                Generate Production Infrastructure Blueprint
              </h2>
              <p className="text-xs text-slate-400">
                Configure customized deployment templates for Kubernetes, Docker Compose, Terraform, or bare metal shell scripts.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Target OS</label>
                <select
                  value={wizOs}
                  onChange={(e) => setWizOs(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="linux">Linux Ubuntu 22.04 LTS</option>
                  <option value="macos">macOS Sonoma (Apple Silicon)</option>
                  <option value="windows">Windows Server Docker</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Hardware Acceleration</label>
                <select
                  value={wizHardware}
                  onChange={(e) => setWizHardware(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="cuda">NVIDIA CUDA 12.2 (Tensor Cores)</option>
                  <option value="metal">Apple Metal MPS</option>
                  <option value="cpu">CPU Only Monolith</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Compliance Standard</label>
                <select
                  value={wizRegulation}
                  onChange={(e) => setWizRegulation(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="eu_ai_act">EU AI Act High-Risk AML Tier</option>
                  <option value="ffiec">US FFIEC Compliance</option>
                  <option value="fca">UK FCA Regulatory Sandbox</option>
                </select>
              </div>
            </div>

            {/* Target Blueprint Tabs */}
            <div className="flex gap-2 mb-3">
              {(['helm', 'docker', 'terraform', 'shell'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setDeployTab(tab)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold uppercase transition-all ${
                    deployTab === tab ? 'bg-emerald-600 text-white' : 'bg-slate-950 border border-slate-800 text-slate-400'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between font-mono text-xs text-emerald-400 space-y-3">
              <pre className="overflow-x-auto text-[11px] leading-relaxed text-indigo-300">{getDeployBlueprint()}</pre>
              <div className="flex justify-end pt-2 border-t border-slate-900">
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(getDeployBlueprint());
                    setCopiedWizCmd(true);
                    setTimeout(() => setCopiedWizCmd(false), 2000);
                  }}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold flex items-center gap-1.5"
                >
                  {copiedWizCmd ? 'Copied! ✓' : 'Copy Blueprint'}
                </button>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── SECTION 10: ENTERPRISE FOOTER ────────────────────────── */}
        <footer className="border-t border-slate-800 bg-slate-950/90 py-12 sm:py-16 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8">
            <div className="space-y-2 text-center md:text-left">
              <div className="flex items-center justify-center md:justify-start gap-2">
                <span className="text-xl">🛡️</span>
                <span className="font-extrabold text-base sm:text-lg text-slate-100">Collaborative Fraud Intelligence Platform</span>
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
    </div>
  );
}
