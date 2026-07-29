import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';

// SVG Icons
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
  <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
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

// Bank Detail Interface for Drawer
interface BankInfoDetail {
  id: string;
  name: string;
  hardware: string;
  ram: string;
  pytorch: string;
  latency: string;
  xmlLogs: string[];
}

const BANK_DETAILS: Record<string, BankInfoDetail> = {
  bank_alpha: {
    id: 'bank_alpha',
    name: 'Santander UK Node',
    hardware: 'NVIDIA RTX 4090 (24GB VRAM)',
    ram: '64 GB Host RAM',
    pytorch: '2.2.0+cu121',
    latency: '1.2 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>SNT-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="GBP">45000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO20022 intake parsed: 1,420 transactions/sec. Local GNN embeddings generated.',
      'Differential Privacy noise injected: Gaussian(0, 0.05). Local weights encrypted.',
    ],
  },
  bank_beta: {
    id: 'bank_beta',
    name: 'BNP Paribas Node',
    hardware: 'NVIDIA A100 (40GB VRAM)',
    ram: '32 GB Host RAM',
    pytorch: '2.2.0+cu121',
    latency: '2.8 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>BNP-2026-8810</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="EUR">120000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO20022 intake parsed: 2,100 transactions/sec. Local GNN embeddings generated.',
      'Paillier homomorphic ciphertext generated: [[w_i]]. Ready for enclave upload.',
    ],
  },
  bank_gamma: {
    id: 'bank_gamma',
    name: 'Deutsche Bank Node',
    hardware: 'CPU Monolith Cluster',
    ram: '16 GB Host RAM',
    pytorch: '2.1.2+cpu',
    latency: '4.1 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>DBK-2026-7734</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="EUR">3800.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'Heterogeneous parameter negotiator applied: Batch size scaled down to 32.',
      'CPU threadpool gradient accumulation steps = 2. Straggler delay prevented.',
    ],
  },
};

// Live Palantir/CrowdStrike Telemetry Log Feed Items
const LIVE_LOG_FEED = [
  'Santander UK Node completed Local Epoch 3/3 (Loss: 0.0412)',
  'BNP Paribas Node generated Paillier Homomorphic Ciphertext [[W]]',
  'CFI Aggregator Core: Quorum Reached (3/3 Nodes) - Aggregating Round',
  'Cross-Bank Streaming GNN: Zero High-Risk Collusion Rings Detected',
  'Deutsche Bank Node synchronized Global Model Weight Vector (v2.4.0)',
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
  const [fraudWaveCount, setFraudWaveCount] = useState<number>(0);
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);

  // FL Cycle Phase State: 0 = Local Training, 1 = Upload, 2 = Aggregation, 3 = Download
  const [flPhase, setFlPhase] = useState<number>(0);

  // Telemetry HUD state
  const [flRound, setFlRound] = useState<number>(42);
  const [accuracy, setAccuracy] = useState<number>(98.42);
  const [logIndex, setLogIndex] = useState<number>(0);

  // Product Epsilon Calculator State
  const [epsilonCalc, setEpsilonCalc] = useState<number>(0.5);

  // Platform Graph Collusion State
  const [isGraphDetected, setIsGraphDetected] = useState<boolean>(false);
  const [isGraphIsolated, setIsGraphIsolated] = useState<boolean>(false);

  // Architecture Layer Stack State
  const [activeLayer, setActiveLayer] = useState<number>(1);

  // Security Attack Simulator State
  const [attackStatus, setAttackStatus] = useState<string | null>(null);

  // API Playground State
  const [apiEndpoint, setApiEndpoint] = useState<string>('handshake');
  const [apiReqBody, setApiReqBody] = useState<string>(
    JSON.stringify(
      { bank_id: 'bank_alpha', pytorch_version: '2.2.0+cu121', ram_gb: 64.0 },
      null,
      2
    )
  );
  const [apiResponse, setApiResponse] = useState<string | null>(null);
  const [isApiLoading, setIsApiLoading] = useState<boolean>(false);

  // Docs Deployment Wizard State
  const [wizOs, setWizOs] = useState<'linux' | 'macos' | 'windows'>('linux');
  const [wizHardware, setWizHardware] = useState<'cuda' | 'metal' | 'cpu'>('cuda');
  const [wizRegulation, setWizRegulation] = useState<'eu_ai_act' | 'ffiec' | 'fca'>('eu_ai_act');
  const [copiedWizCmd, setCopiedWizCmd] = useState<boolean>(false);

  // Increment FL round periodically
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

  // 60FPS Hero Canvas Topology Render Engine
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;
    let width = (canvas.width = canvas.parentElement?.clientWidth || 800);
    let height = (canvas.height = width < 640 ? 360 : 420);

    const handleResize = () => {
      if (!canvas) return;
      width = canvas.width = canvas.parentElement?.clientWidth || 800;
      height = canvas.height = width < 640 ? 360 : 420;
    };
    window.addEventListener('resize', handleResize);

    const isMobile = width < 640;
    const nodes = {
      alpha: { x: isMobile ? width * 0.25 : width * 0.2, y: isMobile ? 80 : 100, color: '#6366f1' },
      beta: { x: isMobile ? width * 0.75 : width * 0.8, y: isMobile ? 80 : 100, color: '#a855f7' },
      gamma: { x: width * 0.5, y: isMobile ? 300 : 340, color: '#10b981' },
      core: { x: width * 0.5, y: isMobile ? 180 : 200, color: '#ec4899' },
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
      radius: number;
    }> = [];

    const createParticle = (from: 'alpha' | 'beta' | 'gamma', isUpload: boolean) => {
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
        speed: 0.012 + Math.random() * 0.01,
        progress: 0,
        color: isUpload ? bank.color : '#38bdf8',
        radius: isUpload ? 4 : 5,
      });
    };

    let tick = 0;
    const render = () => {
      ctx.clearRect(0, 0, width, height);

      // Connection lines
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);

      ['alpha', 'beta', 'gamma'].forEach((key) => {
        const n = nodes[key as keyof typeof nodes];
        ctx.strokeStyle = n.color + '60';
        ctx.beginPath();
        ctx.moveTo(n.x, n.y);
        ctx.lineTo(nodes.core.x, nodes.core.y);
        ctx.stroke();
      });

      ctx.setLineDash([]);

      // Spawn particles depending on FL Phase
      if (isPlaying && tick % 12 === 0) {
        if (flPhase === 1) {
          // Encrypted Upload Phase
          createParticle('alpha', true);
          createParticle('beta', true);
          createParticle('gamma', true);
        } else if (flPhase === 3) {
          // Global Model Download Phase
          createParticle('alpha', false);
          createParticle('beta', false);
          createParticle('gamma', false);
        }
      }

      // Aggregator Shockwave in Phase 2
      if (flPhase === 2) {
        const shockRadius = (tick % 30) * 2.5;
        ctx.strokeStyle = '#ec489980';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(nodes.core.x, nodes.core.y, shockRadius, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Draw active particles
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        if (!p) continue;
        p.progress += p.speed;

        p.x = (1 - p.progress) * p.startX + p.progress * p.targetX;
        p.y = (1 - p.progress) * p.startY + p.progress * p.targetY;

        ctx.fillStyle = p.color;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

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

  // Security Attack Simulation Trigger
  const handleRunAttack = (type: 'mia' | 'dlg' | 'byzantine') => {
    setAttackStatus('RUNNING SIMULATION...');
    setTimeout(() => {
      if (type === 'mia') {
        setAttackStatus('MIA RESISTANCE VERIFIED: 95.8% Privacy Guarantee (Safe Tier)');
      } else if (type === 'dlg') {
        setAttackStatus('DLG GRADIENT RECONSTRUCTION BLOCKED: 100% Masked (Error > 99.9%)');
      } else {
        setAttackStatus('BYZANTINE NODE ISOLATED: Trimmed-Mean Aggregation Quenched Malicious Gradients');
      }
    }, 1200);
  };

  // API Playground Exec
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
              handshake_token: 'hs_tok_89a2f10b42',
              assigned_quorum: 'cluster_eu_west_1',
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
              { bank_id: 'bank_alpha', status: 'ONLINE', ram_gb: 64.0, hardware: 'cuda' },
              { bank_id: 'bank_beta', status: 'ONLINE', ram_gb: 32.0, hardware: 'cuda' },
              { bank_id: 'bank_gamma', status: 'ONLINE', ram_gb: 16.0, hardware: 'cpu' },
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

  // Deployment Script Generator
  const getDeploymentCmd = () => {
    const osCmd = wizOs === 'linux' ? 'curl -sSL' : wizOs === 'macos' ? 'brew install' : 'docker run -d';
    const accelFlag = wizHardware === 'cuda' ? '--cuda' : wizHardware === 'metal' ? '--metal' : '--cpu';
    const regFlag = wizRegulation === 'eu_ai_act' ? '--compliance eu-ai-act' : '--compliance ffiec';
    return `${osCmd} https://get.cfi-platform.org/install.sh | bash -s -- ${accelFlag} ${regFlag}`;
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

      <div className="relative z-10">
        {/* ── SECTION 1: GLASSMORPHIC TOP NAVBAR ─────────────────── */}
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-slate-950/80 border-b border-slate-800/80">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 h-20 flex items-center justify-between">
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-emerald-500 p-0.5 shadow-lg shadow-indigo-500/20">
                <div className="h-full w-full bg-slate-950 rounded-[10px] flex items-center justify-center font-black text-white text-lg">
                  🛡️
                </div>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-extrabold text-base sm:text-lg tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
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
            <nav className="hidden md:flex items-center gap-6 lg:gap-8 text-xs font-semibold text-slate-300">
              <a href="#product" className="hover:text-indigo-400 transition-colors">Product</a>
              <a href="#platform" className="hover:text-indigo-400 transition-colors">Platform</a>
              <a href="#architecture" className="hover:text-indigo-400 transition-colors">Architecture</a>
              <a href="#security" className="hover:text-indigo-400 transition-colors">Security</a>
              <a href="#api" className="hover:text-indigo-400 transition-colors">API Playground</a>
              <a href="#docs" className="hover:text-indigo-400 transition-colors">Deploy Wizard</a>
            </nav>

            {/* Actions & Mobile Menu Button */}
            <div className="flex items-center gap-3">
              <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-semibold">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Quorum Active (3/3 Synced)
              </div>

              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:inline-flex group relative items-center justify-center p-0.5 overflow-hidden rounded-xl font-bold text-xs shadow-lg shadow-indigo-500/25 transition-all duration-300 hover:scale-105"
              >
                <span className="absolute inset-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 animate-gradient" />
                <span className="relative px-5 py-2.5 rounded-[10px] bg-slate-950 text-white flex items-center gap-2 group-hover:bg-slate-900 transition-all">
                  <span>Launch Demo</span>
                  <ArrowRightIcon />
                </span>
              </button>

              {/* Mobile Hamburger Toggle Button */}
              <button
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="md:hidden p-2.5 rounded-xl bg-slate-900 border border-slate-800 text-slate-200 focus:outline-none"
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
              className="md:hidden border-b border-slate-800 bg-slate-950/95 backdrop-blur-2xl px-6 py-6 space-y-4"
            >
              <nav className="flex flex-col space-y-3 font-semibold text-sm text-slate-200">
                <a
                  href="#product"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
                  Product Capabilities
                </a>
                <a
                  href="#platform"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
                  Platform Simulator
                </a>
                <a
                  href="#architecture"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
                  System Architecture
                </a>
                <a
                  href="#security"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
                  Security Playground
                </a>
                <a
                  href="#api"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
                  API Execution Studio
                </a>
                <a
                  href="#docs"
                  onClick={() => setIsMobileMenuOpen(false)}
                  className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300"
                >
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

        {/* ── PALANTIR/CROWDSTRIKE STYLE LIVE LOG TICKER BAR ───── */}
        <div className="bg-slate-900/90 border-b border-slate-800/80 px-4 sm:px-6 py-2 overflow-hidden flex items-center gap-3 text-xs font-mono">
          <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px] font-bold uppercase tracking-wider flex-shrink-0">
            LIVE TELEMETRY LOG
          </span>
          <div className="truncate text-slate-300 flex-1">
            <span className="text-emerald-400 font-bold">[{new Date().toLocaleTimeString()}]</span>{' '}
            {LIVE_LOG_FEED[logIndex]}
          </div>
        </div>

        {/* ── SECTION 2: HERO SECTION WITH CONTINUOUS FL LIFECYCLE ── */}
        <section className="relative pt-10 sm:pt-16 pb-16 sm:pb-24 px-4 sm:px-6 max-w-7xl mx-auto overflow-hidden">
          {/* Glowing Background Radial Orbs */}
          <div className="absolute top-10 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-gradient-to-tr from-indigo-500/10 via-purple-500/10 to-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

          <div className="text-center max-w-4xl mx-auto space-y-4 sm:space-y-6">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="inline-flex items-center gap-2 px-3 sm:px-4 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-[11px] sm:text-xs font-semibold"
            >
              <span>✨ Privacy-Preserving Collaborative Machine Learning</span>
              <span className="text-indigo-500 hidden sm:inline">•</span>
              <span className="text-emerald-400 font-bold hidden sm:inline">GDPR & EU AI Act Compliant</span>
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1 }}
              className="text-3xl sm:text-5xl lg:text-6xl font-black tracking-tight leading-tight text-slate-100"
            >
              Detect Cross-Bank Fraud Rings Without Sharing Raw Customer Data
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
              className="text-xs sm:text-base lg:text-lg text-slate-400 max-w-3xl mx-auto leading-relaxed"
            >
              Leverage Heterogeneous Federated Learning, Secure Enclaves, and Streaming Graph Neural Networks to stop multi-institutional money laundering and fraud syndicates in real time.
            </motion.p>
          </div>

          {/* Live Telemetry HUD Bar */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.3 }}
            className="mt-8 sm:mt-10 max-w-4xl mx-auto grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 p-4 rounded-2xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl"
          >
            <div className="text-center">
              <span className="text-[10px] sm:text-[11px] font-bold text-slate-400 uppercase tracking-wider">Active FL Round</span>
              <div className="text-lg sm:text-xl font-black text-indigo-400 font-mono mt-0.5">#{flRound}</div>
            </div>
            <div className="text-center">
              <span className="text-[10px] sm:text-[11px] font-bold text-slate-400 uppercase tracking-wider">Global GNN Accuracy</span>
              <div className="text-lg sm:text-xl font-black text-emerald-400 font-mono mt-0.5">{accuracy}%</div>
            </div>
            <div className="text-center">
              <span className="text-[10px] sm:text-[11px] font-bold text-slate-400 uppercase tracking-wider">Privacy Budget (ε)</span>
              <div className="text-lg sm:text-xl font-black text-purple-400 font-mono mt-0.5">0.50 (Strict)</div>
            </div>
            <div className="text-center">
              <span className="text-[10px] sm:text-[11px] font-bold text-slate-400 uppercase tracking-wider">Stream Bandwidth</span>
              <div className="text-lg sm:text-xl font-black text-blue-400 font-mono mt-0.5">1.4 GB/s</div>
            </div>
          </motion.div>

          {/* ── 4-PHASE STORYTELLING TOPOLOGY CANVAS ─────────────── */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.6, delay: 0.4 }}
            className="mt-8 sm:mt-10 rounded-3xl border border-indigo-500/20 bg-gradient-to-b from-slate-900/90 via-slate-950/95 to-slate-950 p-4 sm:p-8 lg:p-10 shadow-2xl relative overflow-hidden"
          >
            {/* Phase Indicator Header Bar */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-slate-800/80">
              <div className="flex items-center gap-3">
                <span className="h-3 w-3 rounded-full bg-emerald-400 animate-ping" />
                <div>
                  <span className="text-[10px] sm:text-[11px] font-mono text-indigo-400 font-bold uppercase tracking-wider">
                    FEDERATION CYCLE IN PROGRESS
                  </span>
                  <h3 className="text-xs sm:text-base font-bold text-slate-100">{getPhaseTitle()}</h3>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setFraudWaveCount((prev) => prev + 1)}
                  className="px-3 py-1.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs font-bold transition-all flex items-center gap-1.5 shadow-sm"
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
                  className={`px-3 py-1.5 rounded-xl text-xs font-bold transition-all flex items-center gap-1.5 border ${
                    isDpShieldActive
                      ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                      : 'bg-slate-800 text-slate-400 border-slate-700'
                  }`}
                >
                  <LockIcon />
                  <span>DP Shield: {isDpShieldActive ? 'ON' : 'OFF'}</span>
                </button>

                <button
                  onClick={() => setIsPlaying(!isPlaying)}
                  className="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold border border-slate-700 transition-all"
                >
                  {isPlaying ? 'Pause ⏸️' : 'Play ▶️'}
                </button>
              </div>
            </div>

            {/* HTML5 Canvas Topology Element */}
            <div className="relative w-full h-[360px] sm:h-[420px] mt-6 flex items-center justify-center">
              <canvas ref={canvasRef} className="absolute inset-0 w-full h-full pointer-events-none" />

              {/* Bank Alpha Node (Top Left) */}
              <motion.div
                whileHover={{ scale: 1.05 }}
                onClick={() => BANK_DETAILS.bank_alpha && setActiveBankDrawer(BANK_DETAILS.bank_alpha)}
                className={`absolute top-2 left-2 sm:top-4 sm:left-16 p-3 sm:p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-300 w-44 sm:w-56 ${
                  flPhase === 0
                    ? 'bg-indigo-500/30 border-indigo-400 shadow-xl shadow-indigo-500/30 ring-2 ring-indigo-500/50'
                    : 'bg-indigo-500/15 border-indigo-500/40 hover:border-indigo-400'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] sm:text-xs font-mono font-bold text-indigo-400">BANK ALPHA</span>
                  <span className="h-2 w-2 rounded-full bg-emerald-400" />
                </div>
                <div className="text-xs sm:text-sm font-bold text-slate-100 truncate">Santander UK</div>
                <div className="text-[10px] sm:text-[11px] text-slate-400 mt-1 flex items-center gap-1">
                  <ZapIcon />
                  <span className="truncate">{flPhase === 0 ? '🧠 Training...' : 'RTX 4090'}</span>
                </div>
              </motion.div>

              {/* Bank Beta Node (Top Right) */}
              <motion.div
                whileHover={{ scale: 1.05 }}
                onClick={() => BANK_DETAILS.bank_beta && setActiveBankDrawer(BANK_DETAILS.bank_beta)}
                className={`absolute top-2 right-2 sm:top-4 sm:right-16 p-3 sm:p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-300 w-44 sm:w-56 ${
                  flPhase === 0
                    ? 'bg-purple-500/30 border-purple-400 shadow-xl shadow-purple-500/30 ring-2 ring-purple-500/50'
                    : 'bg-purple-500/15 border-purple-500/40 hover:border-purple-400'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] sm:text-xs font-mono font-bold text-purple-400">BANK BETA</span>
                  <span className="h-2 w-2 rounded-full bg-emerald-400" />
                </div>
                <div className="text-xs sm:text-sm font-bold text-slate-100 truncate">BNP Paribas</div>
                <div className="text-[10px] sm:text-[11px] text-slate-400 mt-1 flex items-center gap-1">
                  <ZapIcon />
                  <span className="truncate">{flPhase === 0 ? '🧠 Training...' : 'NVIDIA A100'}</span>
                </div>
              </motion.div>

              {/* Central Aggregator Core (Center) */}
              <div
                className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 p-4 sm:p-6 rounded-3xl bg-slate-950 border-2 transition-all duration-300 text-center w-52 sm:w-64 z-10 ${
                  flPhase === 2
                    ? 'border-emerald-400 shadow-2xl shadow-emerald-500/40 ring-4 ring-emerald-500/20'
                    : 'border-indigo-500 shadow-2xl shadow-indigo-500/30'
                }`}
              >
                <div className="relative inline-block">
                  <div className="h-10 w-10 sm:h-12 sm:w-12 rounded-2xl bg-indigo-500/20 border border-indigo-500/40 text-indigo-400 flex items-center justify-center mx-auto text-xl sm:text-2xl mb-1.5">
                    🛰️
                  </div>
                  {isDpShieldActive && (
                    <div className="absolute -top-1 -right-1 h-4 w-4 sm:h-5 sm:w-5 rounded-full bg-emerald-500 text-slate-950 flex items-center justify-center text-[9px] sm:text-[10px] font-black">
                      🔒
                    </div>
                  )}
                </div>
                <h4 className="text-xs sm:text-sm font-extrabold text-slate-100">CFI Aggregator Core</h4>
                <p className="text-[10px] sm:text-[11px] text-slate-400 mt-0.5">Secure Enclave (Intel SGX)</p>
                <div className="mt-2.5 py-1 px-2 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 font-mono text-[9px] sm:text-[10px] font-bold truncate">
                  {flPhase === 2 ? '⚡ Aggregating...' : 'FedAvg + Differential Privacy'}
                </div>
              </div>

              {/* Bank Gamma Node (Bottom Center) */}
              <motion.div
                whileHover={{ scale: 1.05 }}
                onClick={() => BANK_DETAILS.bank_gamma && setActiveBankDrawer(BANK_DETAILS.bank_gamma)}
                className={`absolute bottom-2 sm:bottom-4 left-1/2 -translate-x-1/2 p-3 sm:p-4 rounded-2xl border backdrop-blur-xl cursor-pointer transition-all duration-300 w-44 sm:w-56 ${
                  flPhase === 0
                    ? 'bg-emerald-500/30 border-emerald-400 shadow-xl shadow-emerald-500/30 ring-2 ring-emerald-500/50'
                    : 'bg-emerald-500/15 border-emerald-500/40 hover:border-emerald-400'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] sm:text-xs font-mono font-bold text-emerald-400">BANK GAMMA</span>
                  <span className="h-2 w-2 rounded-full bg-emerald-400" />
                </div>
                <div className="text-xs sm:text-sm font-bold text-slate-100 truncate">Deutsche Bank</div>
                <div className="text-[10px] sm:text-[11px] text-slate-400 mt-1 flex items-center gap-1">
                  <CpuIcon />
                  <span className="truncate">{flPhase === 0 ? '🧠 Training...' : 'CPU Cluster'}</span>
                </div>
              </motion.div>
            </div>
          </motion.div>
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
                    <span className="text-xs font-mono font-bold text-indigo-400 uppercase">INSPECTING NODE</span>
                    <h3 className="text-lg sm:text-xl font-bold text-slate-100">{activeBankDrawer.name}</h3>
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
                    <span className="text-slate-500">Hardware:</span>
                    <div className="text-indigo-300 font-bold mt-0.5 truncate">{activeBankDrawer.hardware}</div>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
                    <span className="text-slate-500">RAM Allocation:</span>
                    <div className="text-emerald-300 font-bold mt-0.5">{activeBankDrawer.ram}</div>
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

        {/* ── SECTION 3: PRODUCT & EPSILON CALCULATOR ──────────────── */}
        <section id="product" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="text-center max-w-3xl mx-auto space-y-4">
            <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold">
              DIFFERENTIAL PRIVACY CALCULATOR
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100">
              Privacy Budget ($\epsilon$) vs Accuracy Trade-off Calculator
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Adjust the privacy budget ($\epsilon$) slider to see real-time mathematical accuracy and reconstruction risk guarantees.
            </p>
          </div>

          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
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
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                    <span className="text-slate-400">Reconstruction Guarantee:</span>
                    <span className="text-emerald-400 font-bold">
                      {epsilonCalc <= 0.5 ? 'Mathematically Impossible' : epsilonCalc <= 1.5 ? 'Very High' : 'Moderate'}
                    </span>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                    <span className="text-slate-400">Gaussian Noise Std Dev ($\sigma$):</span>
                    <span className="text-purple-400 font-bold">{(0.5 / epsilonCalc).toFixed(3)}</span>
                  </div>
                </div>
              </div>

              <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="p-6 rounded-2xl bg-slate-950 border border-emerald-500/30 flex flex-col justify-between">
                  <span className="text-xs text-slate-400 uppercase font-bold">Estimated Model Accuracy</span>
                  <div className="text-3xl sm:text-4xl font-black text-emerald-400 font-mono mt-4">
                    {(88 + (epsilonCalc / 5.0) * 11.2).toFixed(2)}%
                  </div>
                </div>

                <div className="p-6 rounded-2xl bg-slate-950 border border-purple-500/30 flex flex-col justify-between">
                  <span className="text-xs text-slate-400 uppercase font-bold">Training Loss Convergence</span>
                  <div className="text-3xl sm:text-4xl font-black text-purple-400 font-mono mt-4">
                    {(0.18 - (epsilonCalc / 5.0) * 0.14).toFixed(4)}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 4: PLATFORM & FRAUD RING GRAPH COLLUSION SIMULATOR ── */}
        <section id="platform" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="glass-card border border-purple-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-purple-400 uppercase tracking-wider">
                  STREAMING GNN COLLUSION DETECTOR
                </span>
                <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100 mt-1">
                  Cross-Bank Money Mule Ring Graph Simulator
                </h2>
              </div>

              <div className="flex items-center gap-2">
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
              </div>
            </div>

            {/* Interactive SVG Node-Link Graph */}
            <div className="relative w-full h-[280px] sm:h-[320px] mt-8 bg-slate-950 rounded-2xl border border-slate-800 flex items-center justify-center overflow-hidden">
              <svg className="w-full h-full" viewBox="0 0 600 280">
                {/* Links */}
                {!isGraphIsolated && (
                  <>
                    <line x1="120" y1="140" x2="280" y2="80" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                    <line x1="280" y1="80" x2="440" y2="140" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                    <line x1="440" y1="140" x2="280" y2="200" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                  </>
                )}

                {/* Node 1: Santander UK */}
                <circle cx="120" cy="140" r="24" fill="#1e1b4b" stroke="#6366f1" strokeWidth="2" />
                <text x="120" y="144" fill="#ffffff" fontSize="10" fontWeight="bold" textAnchor="middle">ACCT-A</text>

                {/* Node 2: BNP Paribas */}
                <circle cx="280" cy="80" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#a855f7'} strokeWidth="2" />
                <text x="280" y="84" fill="#ffffff" fontSize="10" fontWeight="bold" textAnchor="middle">MULE-1</text>

                {/* Node 3: Deutsche Bank */}
                <circle cx="440" cy="140" r="24" fill="#064e3b" stroke="#10b981" strokeWidth="2" />
                <text x="440" y="144" fill="#ffffff" fontSize="10" fontWeight="bold" textAnchor="middle">ACCT-C</text>

                {/* Node 4: Offramp */}
                <circle cx="280" cy="200" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#ec4899'} strokeWidth="2" />
                <text x="280" y="204" fill="#ffffff" fontSize="10" fontWeight="bold" textAnchor="middle">OFFRAMP</text>
              </svg>

              {isGraphDetected && (
                <div className="absolute top-4 right-4 px-3 py-1.5 rounded-xl bg-rose-500/20 border border-rose-500/40 text-rose-300 font-mono text-[10px] sm:text-xs font-bold">
                  ⚠️ SUSPICIOUS RING DETECTED (Risk Score: 0.94)
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ── SECTION 5: ARCHITECTURE 5-LAYER STACK & PATH HIGHLIGHTING ── */}
        <section id="architecture" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="text-center max-w-3xl mx-auto space-y-4">
            <span className="px-3.5 py-1.5 rounded-full bg-purple-500/10 border border-purple-500/20 text-purple-400 text-xs font-bold">
              5-LAYER SYSTEM ARCHITECTURE
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100">
              Interactive Technical Layer Stack
            </h2>
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

            <div className="lg:col-span-7 p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between">
              <div>
                <span className="text-xs font-mono text-indigo-400 uppercase font-bold">SPECIFICATION DETAIL</span>
                <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-2">
                  {activeLayer === 1 && 'Native ISO 20022 Financial XML Intake'}
                  {activeLayer === 2 && 'PyTorch Geometric GNN Embedding Feature Store'}
                  {activeLayer === 3 && 'Intel SGX Secure Enclave & Paillier Homomorphic Encryption'}
                  {activeLayer === 4 && 'Byzantine-Robust FedAvg Aggregation Core'}
                  {activeLayer === 5 && 'Automated SAR XML & Splunk SIEM Integration'}
                </h3>
                <p className="text-xs text-slate-400 mt-3 leading-relaxed">
                  {activeLayer === 1 && 'Parses pacs.008 and camt.053 XML financial transaction messages directly into local graph tensors without storing customer identity details.'}
                  {activeLayer === 2 && 'Extracts structural graph attention embeddings (GAT) capturing account transaction topologies across isolated banking domains.'}
                  {activeLayer === 3 && 'Injects Gaussian differential privacy noise and computes encrypted sum updates within hardware-isolated TEE enclaves.'}
                  {activeLayer === 4 && 'Aggregates multi-bank gradient weights using Trimmed-Mean to automatically neutralize malicious or corrupted node updates.'}
                  {activeLayer === 5 && 'Generates cryptographic sign-off hashes and exports automated SAR XML filings directly to regulatory SIEM endpoints.'}
                </p>
              </div>
              <div className="mt-6 p-3 rounded-xl bg-slate-900 font-mono text-[11px] text-emerald-400 border border-slate-800">
                Status: Operational (Verified Compliance Audit)
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 6: SECURITY ATTACK SIMULATOR ────────────────── */}
        <section id="security" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="glass-card border border-emerald-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-wider">
                  SECURITY ATTACK SIMULATOR
                </span>
                <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100 mt-1">
                  Adversarial Attack Simulation Playground
                </h2>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => handleRunAttack('mia')}
                  className="px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold shadow-md"
                >
                  Launch MIA 🎯
                </button>
                <button
                  onClick={() => handleRunAttack('dlg')}
                  className="px-3.5 py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-xs font-bold shadow-md"
                >
                  Launch DLG 🔓
                </button>
                <button
                  onClick={() => handleRunAttack('byzantine')}
                  className="px-3.5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold shadow-md"
                >
                  Toggle Byzantine ☣️
                </button>
              </div>
            </div>

            <div className="mt-6 p-6 rounded-2xl bg-slate-950 border border-slate-800 text-center font-mono">
              <span className="text-xs text-slate-400 uppercase font-bold">Simulation Result Output</span>
              <div className="text-xs sm:text-base font-bold text-emerald-400 mt-2">
                {attackStatus || 'Click any attack button above to test zero-trust defense mechanisms.'}
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 7: LIVE REST API PLAYGROUND ─────────────────── */}
        <section id="api" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <div className="flex items-center gap-2">
                  <CodeIcon />
                  <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100">Live REST API Execution Studio</h2>
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  Edit request payloads and execute test API calls live against simulated endpoints.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={apiEndpoint}
                  onChange={(e) => setApiEndpoint(e.target.value)}
                  className="p-2 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-indigo-300 font-bold max-w-[200px] sm:max-w-none"
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
                  {isApiLoading ? 'Executing...' : 'Send Test 🚀'}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">JSON Request Body</span>
                <textarea
                  value={apiReqBody}
                  onChange={(e) => setApiReqBody(e.target.value)}
                  className="w-full h-48 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-indigo-300 focus:border-indigo-500"
                />
              </div>

              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">HTTP 200 JSON Response</span>
                <div className="w-full h-48 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-emerald-400 overflow-y-auto">
                  {apiResponse ? <pre>{apiResponse}</pre> : <span className="text-slate-600">// Click "Send Test" to execute</span>}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── SECTION 8: CUSTOMIZED DEPLOYMENT WIZARD ────────────── */}
        <section id="docs" className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8 sm:space-y-12">
          <div className="glass-card border border-slate-800 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl">
            <div className="text-center max-w-3xl mx-auto space-y-4 mb-8">
              <span className="px-3.5 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold">
                DEPLOYMENT BLUEPRINT WIZARD
              </span>
              <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-100">
                Generate Customized Node Deployment Script
              </h2>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
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

            <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 flex items-center justify-between font-mono text-xs text-emerald-400">
              <span className="truncate pr-4">{getDeploymentCmd()}</span>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(getDeploymentCmd());
                  setCopiedWizCmd(true);
                  setTimeout(() => setCopiedWizCmd(false), 2000);
                }}
                className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold flex-shrink-0"
              >
                {copiedWizCmd ? 'Copied! ✓' : 'Copy Script'}
              </button>
            </div>
          </div>
        </section>

        {/* ── SECTION 9: ENTERPRISE FOOTER ────────────────────────── */}
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
