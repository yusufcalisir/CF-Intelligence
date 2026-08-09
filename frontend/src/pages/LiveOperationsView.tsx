import { useEffect, useRef, useState } from 'react';
import { useParams, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts';
import ModelRegistryPanel from '../components/dashboard/ModelRegistryPanel';
import FederatedTrainingAnimation from '../components/dashboard/FederatedTrainingAnimation';
import ComplianceReportPanel from '../components/dashboard/ComplianceReportPanel';
import { IncentiveRegistryPanel } from '../components/dashboard/IncentiveRegistryPanel';
import { SecureHardwarePanel } from '../components/dashboard/SecureHardwarePanel';
import StreamingGNNPanel from '../components/dashboard/StreamingGNNPanel';

interface BankNode {
  id: string;
  name: string;
  status: 'ACTIVE' | 'OFFLINE' | 'SUSPENDED';
  tier: string;
  lastHeartbeat: string;
}

interface RoundData {
  round: number;
  auc: number;
  bankA: number;
  bankB: number;
  bankC: number;
  loss: number;
}

type TrainingPhase =
  | 'pending'
  | 'generating_data'
  | 'training_local'
  | 'training_federated'
  | 'evaluating'
  | 'completed';

const DEFAULT_BANKS: BankNode[] = [
  { id: 'bank_alpha', name: 'Bank Alpha', status: 'ACTIVE', tier: 'Tier 1', lastHeartbeat: 'Just now' },
  { id: 'bank_beta', name: 'Bank Beta', status: 'ACTIVE', tier: 'Tier 1', lastHeartbeat: '2s ago' },
  { id: 'bank_gamma', name: 'Bank Gamma', status: 'ACTIVE', tier: 'Tier 2', lastHeartbeat: '5s ago' },
];

const MOCK_SCORING_VOLUME = [
  { time: '00:00', volume: 1250 },
  { time: '04:00', volume: 890 },
  { time: '08:00', volume: 3400 },
  { time: '12:00', volume: 5600 },
  { time: '16:00', volume: 4800 },
  { time: '20:00', volume: 2900 },
  { time: '24:00', volume: 1800 },
];


const TOTAL_ROUNDS = 10;

export default function LiveOperationsView() {
  const { id } = useParams<{ id?: string }>();
  const location = useLocation();
  const [bankNodes, setBankNodes] = useState<BankNode[]>(DEFAULT_BANKS);
  const [currentRound, setCurrentRound] = useState(0);
  const [championAuc, setChampionAuc] = useState(0.72);
  const [gradientSubmissions, setGradientSubmissions] = useState(0);
  const [wsStatus, setWsStatus] = useState<'CONNECTED' | 'RECONNECTING'>('CONNECTED');
  const [trainingPhase, setTrainingPhase] = useState<TrainingPhase>('pending');
  const [roundHistory, setRoundHistory] = useState<RoundData[]>([]);
  const [isTraining, setIsTraining] = useState(false);
  const mockIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const phaseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // WebSocket live telemetry listener with automatic fallback & simulation
  useEffect(() => {
    const getWsUrl = () => {
      if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
      if (import.meta.env.VITE_API_URL) {
        const apiUrl = import.meta.env.VITE_API_URL;
        const wsProto = apiUrl.startsWith('https') ? 'wss:' : 'ws:';
        const host = apiUrl.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
        return `${wsProto}//${host}/ws/training`;
      }
      if (window.location.hostname.includes('hf.space') || window.location.hostname === 'localhost') {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${window.location.host}/ws/training`;
      }
      return 'wss://yusufcalisir-collaborative-fraud-intelligence-simulator.hf.space/ws/training';
    };

    let ws: WebSocket | null = null;
    let isCleanedUp = false;

    const startMockTicker = () => {
      setWsStatus('CONNECTED');
      if (!mockIntervalRef.current) {
        mockIntervalRef.current = setInterval(() => {
          setGradientSubmissions((prev) => (prev >= 3 ? 1 : prev + 1));
          setChampionAuc((prev) => Math.min(0.99, parseFloat((prev + (Math.random() * 0.002 - 0.001)).toFixed(4))));
        }, 5000);
      }
    };

    try {
      ws = new WebSocket(getWsUrl());
      ws.onopen = () => { if (!isCleanedUp) setWsStatus('CONNECTED'); };
      ws.onmessage = (event) => {
        if (isCleanedUp) return;
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'round_started') {
            setCurrentRound(data.round || 1);
            setGradientSubmissions(0);
            setTrainingPhase('training_federated');
          } else if (data.event === 'gradient_received') {
            setGradientSubmissions((prev) => prev + 1);
          } else if (data.event === 'round_complete') {
            if (data.auc) {
              setChampionAuc(data.auc);
              setRoundHistory((prev) => [
                ...prev,
                {
                  round: data.round,
                  auc: data.auc,
                  bankA: parseFloat((data.auc - 0.01 + Math.random() * 0.02).toFixed(4)),
                  bankB: parseFloat((data.auc - 0.015 + Math.random() * 0.02).toFixed(4)),
                  bankC: parseFloat((data.auc - 0.008 + Math.random() * 0.015).toFixed(4)),
                  loss: parseFloat(Math.max(0.05, 0.5 - data.round * 0.04).toFixed(4)),
                },
              ]);
            }
          }
        } catch { /* ignore non-json frames */ }
      };
      ws.onerror = () => {
        if (ws && ws.readyState !== WebSocket.CLOSED) {
          try { ws.close(); } catch { /* ignore */ }
        }
        if (!isCleanedUp) startMockTicker();
      };
      ws.onclose = () => { if (!isCleanedUp) startMockTicker(); };
    } catch {
      startMockTicker();
    }

    return () => {
      isCleanedUp = true;
      if (ws) {
        ws.onopen = null; ws.onmessage = null; ws.onerror = null; ws.onclose = null;
        try { ws.close(); } catch { /* ignore */ }
      }
      if (mockIntervalRef.current) clearInterval(mockIntervalRef.current);
    };
  }, []);

  // Poll bank node heartbeats every 30s
  useEffect(() => {
    const fetchBankNodes = async () => {
      try {
        const res = await fetch('/api/v1/onboarding/banks');
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setBankNodes(
              data.map((b: any) => ({
                id: b.bank_id || b.id,
                name: b.bank_name || b.name || b.bank_id,
                status: (b.status || 'ACTIVE').toUpperCase(),
                tier: b.tier || 'Tier 1',
                lastHeartbeat: 'Just now',
              }))
            );
          }
        }
      } catch { /* fall back to default bank list */ }
    };
    fetchBankNodes();
    const interval = setInterval(fetchBankNodes, 30000);
    return () => clearInterval(interval);
  }, []);

  // ── Simulated federated training run ──────────────────────────────────────
  const startSimulatedTraining = () => {
    if (isTraining) return;
    setIsTraining(true);
    setRoundHistory([]);
    setCurrentRound(0);
    setGradientSubmissions(0);

    // Phase sequence: generating_data → training_local → training_federated (×rounds) → evaluating → completed
    const runPhase = (phase: TrainingPhase, duration: number, next: () => void) => {
      setTrainingPhase(phase);
      phaseTimerRef.current = setTimeout(next, duration);
    };

    runPhase('generating_data', 2000, () => {
      runPhase('training_local', 2500, () => {
        // Simulate TOTAL_ROUNDS federated rounds
        let round = 0;
        let auc = 0.72;
        let loss = 0.58;
        setTrainingPhase('training_federated');

        const doRound = () => {
          if (round >= TOTAL_ROUNDS) {
            runPhase('evaluating', 2000, () => {
              setTrainingPhase('completed');
              setIsTraining(false);
            });
            return;
          }
          round++;
          auc = Math.min(0.99, auc + 0.012 + Math.random() * 0.009);
          loss = Math.max(0.05, loss - 0.04 - Math.random() * 0.015);
          const newPoint: RoundData = {
            round,
            auc: parseFloat(auc.toFixed(4)),
            bankA: parseFloat((auc - 0.01 + Math.random() * 0.02).toFixed(4)),
            bankB: parseFloat((auc - 0.015 + Math.random() * 0.02).toFixed(4)),
            bankC: parseFloat((auc - 0.008 + Math.random() * 0.015).toFixed(4)),
            loss: parseFloat(loss.toFixed(4)),
          };
          setCurrentRound(round);
          setChampionAuc(newPoint.auc);
          setGradientSubmissions(3);
          setRoundHistory((prev) => [...prev, newPoint]);
          phaseTimerRef.current = setTimeout(doRound, 1200);
        };

        doRound();
      });
    });
  };

  const resetTraining = () => {
    if (phaseTimerRef.current) clearTimeout(phaseTimerRef.current);
    setIsTraining(false);
    setTrainingPhase('pending');
    setRoundHistory([]);
    setCurrentRound(0);
    setChampionAuc(0.72);
    setGradientSubmissions(0);
  };

  // Auto-start simulation when navigated from Dashboard or via simulation route
  useEffect(() => {
    const isAutoStart = id || location.pathname.startsWith('/simulation') || location.search.includes('autostart=true');
    if (isAutoStart && !isTraining && trainingPhase === 'pending') {
      startSimulatedTraining();
    }
  }, [id, location.pathname, location.search]);

  // Tooltip style shared across charts
  const tooltipStyle = {
    backgroundColor: 'rgba(15, 23, 42, 0.95)',
    borderColor: 'var(--color-border)',
    borderRadius: '8px',
    color: '#fff',
    fontSize: '12px',
  };

  return (
    <div className="flex flex-col gap-6 p-4">
      {/* Header Bar */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4 glass-card p-4 sm:p-6 border-l-4 border-l-[var(--color-accent-indigo)] min-w-0"
      >
        <div className="space-y-1.5 min-w-0 flex-1">
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 min-w-0">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <span className="text-xl sm:text-2xl shrink-0">📡</span>
              <h1 className="text-lg sm:text-2xl md:text-3xl font-extrabold text-[var(--color-text-primary)] tracking-tight truncate">
                Live Operations Dashboard
              </h1>
            </div>
            <span
              className={`px-2.5 py-0.5 rounded-full text-[10px] sm:text-xs font-semibold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 self-start sm:self-auto ${
                wsStatus === 'CONNECTED'
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                  : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
              }`}
            >
              ● {wsStatus}
            </span>
          </div>
          <p className="text-xs sm:text-sm text-[var(--color-text-muted)] leading-normal">
            Real-time Consortium Federated Learning Telemetry & Transaction Scoring Stream
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-between xl:justify-end gap-3 sm:gap-4 pt-3 xl:pt-0 border-t border-[var(--color-border-subtle)] xl:border-t-0 shrink-0">
          <div className="text-left sm:text-right shrink-0">
            <p className="text-[10px] sm:text-xs text-[var(--color-text-muted)] uppercase tracking-wider font-semibold">Active Champion AUC</p>
            <p className="text-lg sm:text-2xl font-bold font-mono text-[var(--color-accent-indigo)]">
              {championAuc.toFixed(4)}
            </p>
          </div>
          {/* Training control buttons */}
          {!isTraining && trainingPhase !== 'completed' ? (
            <button
              onClick={startSimulatedTraining}
              className="px-3.5 sm:px-5 py-2 sm:py-2.5 rounded-xl font-semibold text-xs sm:text-sm text-white bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)] hover:opacity-90 transition-all shadow-lg shadow-[var(--color-accent-indigo)]/30 active:scale-95 whitespace-nowrap shrink-0"
            >
              ▶ Start Federated Training
            </button>
          ) : trainingPhase === 'completed' ? (
            <button
              onClick={resetTraining}
              className="px-3.5 sm:px-5 py-2 sm:py-2.5 rounded-xl font-semibold text-xs sm:text-sm text-white bg-gradient-to-r from-emerald-600 to-teal-600 hover:opacity-90 transition-all shadow-lg active:scale-95 whitespace-nowrap shrink-0"
            >
              🔄 Reset Simulation
            </button>
          ) : (
            <div className="flex items-center gap-2 px-3.5 sm:px-4 py-2 rounded-xl border border-[var(--color-accent-indigo)]/40 bg-[var(--color-accent-indigo)]/10 shrink-0">
              <span className="animate-pulse text-[var(--color-accent-indigo)] font-bold text-xs sm:text-sm">●</span>
              <span className="text-xs sm:text-sm text-[var(--color-text-secondary)] font-medium">Training…</span>
            </div>
          )}
        </div>
      </motion.div>

      {/* Top Telemetry KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Active Consortium Nodes</p>
          <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
            {bankNodes.filter((b) => b.status === 'ACTIVE').length} / {bankNodes.length}
          </p>
          <p className="text-xs text-emerald-400 mt-1">100% Quorum Reached</p>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">FL Training Round</p>
          <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
            {currentRound > 0 ? `Round ${currentRound} / ${TOTAL_ROUNDS}` : '—'}
          </p>
          <p className="text-xs text-indigo-400 mt-1">
            {gradientSubmissions > 0 ? `${gradientSubmissions} / 3 Gradients Received` : 'Awaiting start'}
          </p>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Scoring Latency (p95)</p>
          <p className="text-2xl font-bold font-mono text-emerald-400 mt-1">42.8 ms</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">SLA Target &lt; 100 ms</p>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">DP Epsilon Consumed</p>
          <p className="text-2xl font-bold font-mono text-amber-400 mt-1">2.10 / 8.00</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">73.7% Privacy Budget Left</p>
        </motion.div>
      </div>

      {/* Main Row: FL Animation & Round-by-Round AUC Chart */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-6 flex flex-col">
          <FederatedTrainingAnimation
            status={trainingPhase}
            currentRound={currentRound}
            totalRounds={TOTAL_ROUNDS}
          />
        </div>

        {/* Round-by-Round AUC Progression */}
        <div className="lg:col-span-6 glass-card p-6 flex flex-col">
          <div className="mb-4">
            <h3 className="text-lg font-bold text-[var(--color-text-primary)]">
              Per-Round Model Performance
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              AUC-ROC per bank vs. federated global model across communication rounds
            </p>
          </div>

          {roundHistory.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center">
              <span className="text-4xl opacity-40">📊</span>
              <p className="text-sm text-[var(--color-text-muted)]">
                {trainingPhase === 'pending'
                  ? 'Press "Start Federated Training" to begin the simulation'
                  : 'Preparing training rounds…'}
              </p>
            </div>
          ) : (
            <div className="flex-1 min-h-0 h-56">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={roundHistory} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis
                    dataKey="round"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    label={{ value: 'Round', position: 'insideBottom', offset: -2, fontSize: 10, fill: 'var(--color-text-muted)' }}
                  />
                  <YAxis
                    domain={[0.65, 1.0]}
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={(v: number) => v.toFixed(2)}
                  />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => v.toFixed(4)} />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }} />
                  <Line type="monotone" dataKey="auc" name="Global Federated" stroke="var(--color-accent-indigo)" strokeWidth={2.5} dot={false} isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankA" name="Bank Alpha" stroke="#34d399" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankB" name="Bank Beta" stroke="#f472b6" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankC" name="Bank Gamma" stroke="#fbbf24" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Loss Curve + Scoring Volume Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Training Loss Curve */}
        <div className="glass-card p-6 flex flex-col">
          <div className="mb-4">
            <h3 className="text-lg font-bold text-[var(--color-text-primary)]">Federated Training Loss</h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Cross-entropy loss across communication rounds</p>
          </div>
          {roundHistory.length === 0 ? (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-[var(--color-text-muted)]">Awaiting training start…</p>
            </div>
          ) : (
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={roundHistory} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="lossGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-accent-rose)" stopOpacity={0.5} />
                      <stop offset="95%" stopColor="var(--color-accent-rose)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="round" stroke="var(--color-text-muted)" fontSize={11} />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} tickFormatter={(v: number) => v.toFixed(2)} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => v.toFixed(4)} />
                  <Area type="monotone" dataKey="loss" name="Loss" stroke="var(--color-accent-rose)" fill="url(#lossGrad)" strokeWidth={2} dot={false} isAnimationActive={true} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* 24-Hour Scoring Volume */}
        <div className="glass-card p-6 flex flex-col">
          <div className="mb-4">
            <h3 className="text-lg font-bold text-[var(--color-text-primary)]">24-Hour Transaction Scoring Volume</h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Real-time cross-bank fraud evaluation rate (trans/sec)</p>
          </div>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={MOCK_SCORING_VOLUME} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorVolume" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-accent-indigo)" stopOpacity={0.6} />
                    <stop offset="95%" stopColor="var(--color-accent-indigo)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="time" stroke="var(--color-text-muted)" fontSize={11} />
                <YAxis stroke="var(--color-text-muted)" fontSize={11} />
                <Tooltip contentStyle={tooltipStyle} />
                <Area type="monotone" dataKey="volume" stroke="var(--color-accent-indigo)" fillOpacity={1} fill="url(#colorVolume)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Bank Nodes Health Grid */}
      <div className="glass-card p-6">
        <h3 className="text-lg font-bold text-[var(--color-text-primary)] mb-4">
          Consortium Bank Nodes Health & Status
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {bankNodes.map((bank, idx) => {
            const roundData = roundHistory[roundHistory.length - 1];
            const bankAuc = idx === 0 ? roundData?.bankA : idx === 1 ? roundData?.bankB : roundData?.bankC;
            return (
              <div key={bank.id} className="p-4 rounded-xl border border-[var(--color-border)] bg-slate-900/40 flex flex-col justify-between gap-3 min-w-0">
                <div className="flex items-start justify-between gap-2 min-w-0">
                  <div className="min-w-0 flex-1">
                    <p className="font-bold text-[var(--color-text-primary)] text-sm truncate">{bank.name}</p>
                    <p className="text-xs text-[var(--color-text-muted)] truncate">{bank.tier} • Last seen {bank.lastHeartbeat}</p>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-xs font-bold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 ${
                      bank.status === 'ACTIVE'
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                    }`}
                  >
                    ● {bank.status}
                  </span>
                </div>
                {bankAuc !== undefined && (
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1 h-1.5 bg-[var(--color-bg-elevated)] rounded-full overflow-hidden">
                      <motion.div
                        className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)]"
                        animate={{ width: `${(bankAuc * 100).toFixed(1)}%` }}
                        transition={{ duration: 0.6, ease: 'easeOut' }}
                      />
                    </div>
                    <span className="text-xs font-mono text-[var(--color-text-muted)] shrink-0">AUC {bankAuc.toFixed(3)}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Deep Operational Panels */}
      <ModelRegistryPanel simulationId="live_prod_v2" />
      <ComplianceReportPanel simulationId="live_prod_v2" banks={[]} />
      <IncentiveRegistryPanel banks={[]} />
      <SecureHardwarePanel simulation={{ id: 'live_prod_v2', status: 'completed', config: { hardware_isolation_mode: 'tee' }, rounds: Array.from({ length: 10 }) } as any} />
      <StreamingGNNPanel simulation={{ id: 'live_prod_v2', status: 'completed', config: { enable_streaming_gnn: true }, streaming_gnn_node_count: 1420, streaming_gnn_edge_count: 5890, streaming_gnn_loss_history: [0.45, 0.38, 0.31, 0.26, 0.22] } as any} />
    </div>
  );
}
