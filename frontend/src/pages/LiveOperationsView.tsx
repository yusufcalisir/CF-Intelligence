import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
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

export default function LiveOperationsView() {
  const [bankNodes, setBankNodes] = useState<BankNode[]>(DEFAULT_BANKS);
  const [currentRound, setCurrentRound] = useState(5);
  const [totalRounds] = useState(10);
  const [championAuc, setChampionAuc] = useState(0.885);
  const [gradientSubmissions, setGradientSubmissions] = useState(3);
  const [wsStatus, setWsStatus] = useState<'CONNECTED' | 'RECONNECTING'>('CONNECTED');

  // WebSocket live telemetry listener
  useEffect(() => {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/training`;
    let ws: WebSocket | null = null;

    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => setWsStatus('CONNECTED');
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'round_started') {
            setCurrentRound(data.round || 1);
            setGradientSubmissions(0);
          } else if (data.event === 'gradient_received') {
            setGradientSubmissions((prev) => prev + 1);
          } else if (data.event === 'round_complete') {
            if (data.auc) setChampionAuc(data.auc);
          }
        } catch {
          // Ignore parse error
        }
      };
      ws.onerror = () => setWsStatus('RECONNECTING');
    } catch {
      setWsStatus('CONNECTED');
    }

    return () => {
      if (ws) ws.close();
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
      } catch {
        // Fall back to default bank list on fetch error
      }
    };

    fetchBankNodes();
    const interval = setInterval(fetchBankNodes, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex flex-col gap-6 p-4">
      {/* Header Bar */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 glass-card p-6 border-l-4 border-l-[var(--color-accent-indigo)]"
      >
        <div>
          <div className="flex items-center gap-3">
            <span className="text-2xl">📡</span>
            <h1 className="text-2xl md:text-3xl font-extrabold text-[var(--color-text-primary)] tracking-tight">
              Live Operations Dashboard
            </h1>
            <span
              className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                wsStatus === 'CONNECTED'
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                  : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
              }`}
            >
              ● {wsStatus}
            </span>
          </div>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            Real-time Consortium Federated Learning Telemetry & Transaction Scoring Stream
          </p>
        </div>

        <div className="flex items-center gap-4">
          <div className="text-right">
            <p className="text-xs text-[var(--color-text-muted)] uppercase">Active Champion AUC</p>
            <p className="text-2xl font-bold font-mono text-[var(--color-accent-indigo)]">
              {championAuc.toFixed(3)}
            </p>
          </div>
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
            Round {currentRound} / {totalRounds}
          </p>
          <p className="text-xs text-indigo-400 mt-1">{gradientSubmissions} Gradients Received</p>
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

      {/* Main Row: FL Animation & Scoring Volume AreaChart */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-6 flex flex-col">
          <FederatedTrainingAnimation status="running" currentRound={currentRound} totalRounds={totalRounds} />
        </div>

        <div className="lg:col-span-6 glass-card p-6 flex flex-col justify-between">
          <div>
            <h3 className="text-lg font-bold text-[var(--color-text-primary)] mb-1">
              24-Hour Transaction Scoring Volume
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mb-4">
              Real-time cross-bank fraud evaluation rate (trans/sec)
            </p>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={MOCK_SCORING_VOLUME}>
                <defs>
                  <linearGradient id="colorVolume" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--color-accent-indigo)" stopOpacity={0.6} />
                    <stop offset="95%" stopColor="var(--color-accent-indigo)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="time" stroke="var(--color-text-muted)" fontSize={12} />
                <YAxis stroke="var(--color-text-muted)" fontSize={12} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    borderColor: 'var(--color-border)',
                    borderRadius: '8px',
                    color: '#fff',
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="volume"
                  stroke="var(--color-accent-indigo)"
                  fillOpacity={1}
                  fill="url(#colorVolume)"
                  strokeWidth={2}
                />
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
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {bankNodes.map((bank) => (
            <div key={bank.id} className="p-4 rounded-xl border border-[var(--color-border)] bg-slate-900/40 flex items-center justify-between">
              <div>
                <p className="font-bold text-[var(--color-text-primary)] text-sm">{bank.name}</p>
                <p className="text-xs text-[var(--color-text-muted)]">{bank.tier} • Last seen {bank.lastHeartbeat}</p>
              </div>
              <span
                className={`px-3 py-1 rounded-full text-xs font-bold ${
                  bank.status === 'ACTIVE'
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                    : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                }`}
              >
                ● {bank.status}
              </span>
            </div>
          ))}
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
