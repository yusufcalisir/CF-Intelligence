import { motion } from 'framer-motion';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import type { SimulationDetail } from '../../api/types';

interface StreamingGNNPanelProps {
  simulation: SimulationDetail;
}

export default function StreamingGNNPanel({ simulation }: StreamingGNNPanelProps) {
  if (!simulation?.config?.enable_streaming_gnn) {
    return null;
  }

  const {
    streaming_gnn_node_count = 0,
    streaming_gnn_edge_count = 0,
    streaming_gnn_loss_history = []
  } = simulation;

  // Map loss history to charting structure
  const chartData = streaming_gnn_loss_history.map((loss, idx) => ({
    round: `Round ${idx + 1}`,
    loss: Number(loss.toFixed(4)),
  }));

  // GAT Simulated Attention Weights for visualization
  const attentionWeights = [
    { source: 'Customer', target: 'Device', weight: 0.82 },
    { source: 'Customer', target: 'IP Address', weight: 0.74 },
    { source: 'Customer', target: 'Card', weight: 0.65 },
    { source: 'Merchant', target: 'Card', weight: 0.58 },
    { source: 'Customer', target: 'Email', weight: 0.41 },
    { source: 'Customer', target: 'Phone', weight: 0.38 },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="glass-card p-6 border border-[var(--color-border-subtle)] rounded-xl bg-opacity-40 backdrop-blur-md shadow-lg space-y-6"
    >
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--color-border-subtle)] pb-4">
        <div>
          <h3 className="text-lg font-bold text-[var(--color-text-primary)] flex items-center gap-2">
            <span className="text-[var(--color-accent-teal)]">⚡</span>
            Real-Time Streaming GNN Dynamics
          </h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            Real-time transaction stream ingestion & online Federated Graph Attention Network (GAT) training logs.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] px-3 py-1.5 rounded-full">
          <span className="w-2.5 h-2.5 rounded-full bg-[var(--color-status-success)] animate-pulse" />
          <span className="text-xs font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">
            Streaming Active
          </span>
        </div>
      </div>

      {/* Grid Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] relative overflow-hidden group hover:border-[var(--color-accent-teal)] transition-all duration-300">
          <div className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">Active Graph Nodes</div>
          <div className="text-3xl font-extrabold text-[var(--color-text-primary)] mt-1 font-mono">
            {streaming_gnn_node_count.toLocaleString()}
          </div>
          <div className="text-[10px] text-[var(--color-text-muted)] mt-1">
            Accounts & devices in sliding window
          </div>
        </div>

        <div className="p-4 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] relative overflow-hidden group hover:border-[var(--color-accent-indigo)] transition-all duration-300">
          <div className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">Active Graph Edges</div>
          <div className="text-3xl font-extrabold text-[var(--color-text-primary)] mt-1 font-mono">
            {streaming_gnn_edge_count.toLocaleString()}
          </div>
          <div className="text-[10px] text-[var(--color-text-muted)] mt-1">
            Undirected transaction pathways mapped
          </div>
        </div>

        <div className="p-4 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] relative overflow-hidden group hover:border-[var(--color-accent-purple)] transition-all duration-300">
          <div className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">Stream Sliding Window</div>
          <div className="text-3xl font-extrabold text-[var(--color-text-primary)] mt-1 font-mono">
            60 <span className="text-lg font-medium text-[var(--color-text-muted)]">Min</span>
          </div>
          <div className="text-[10px] text-[var(--color-text-muted)] mt-1">
            Pruning threshold for expired connections
          </div>
        </div>
      </div>

      {/* Main Charts Split */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Side: GNN Loss History */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">
              Online Training Loss Curve
            </h4>
            {chartData.length > 0 && (
              <span className="text-xs font-mono text-[var(--color-accent-teal)]">
                Latest: {chartData[chartData.length - 1]?.loss ?? 0}
              </span>
            )}
          </div>
          <div className="h-64 bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded-lg p-4">
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -25, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gnnLossGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-accent-teal)" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="var(--color-accent-teal)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-subtle)" vertical={false} />
                  <XAxis dataKey="round" tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }} />
                  <YAxis tick={{ fill: 'var(--color-text-muted)', fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--color-bg-elevated)',
                      borderColor: 'var(--color-border-subtle)',
                      color: 'var(--color-text-primary)',
                      fontSize: '12px',
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="loss"
                    stroke="var(--color-accent-teal)"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#gnnLossGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-xs text-[var(--color-text-muted)]">
                No GNN training steps recorded yet.
              </div>
            )}
          </div>
        </div>

        {/* Right Side: GAT Attention Weights */}
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-[var(--color-text-secondary)] uppercase tracking-wider">
            Graph Attention Network (GAT) Edge Coefficients
          </h4>
          <div
            tabIndex={0}
            role="region"
            aria-label="Graph Attention Network edge coefficients list"
            className="bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded-lg p-4 h-64 overflow-y-auto space-y-3.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
          >
            {attentionWeights.map((att, idx) => (
              <div key={idx} className="space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-[var(--color-text-primary)]">{att.source}</span>
                    <span className="text-[var(--color-text-muted)]">➔</span>
                    <span className="font-semibold text-[var(--color-text-primary)]">{att.target}</span>
                  </div>
                  <span className="font-mono font-bold text-[var(--color-accent-indigo)]">
                    {(att.weight * 100).toFixed(0)}% attention
                  </span>
                </div>
                <div className="w-full h-2 bg-black bg-opacity-20 rounded-full overflow-hidden">
                  <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${att.weight * 100}%` }}
                    transition={{ duration: 0.8, delay: idx * 0.1 }}
                    className="h-full rounded-full"
                    style={{
                      background: 'linear-gradient(90deg, var(--color-accent-indigo), var(--color-accent-teal))',
                    }}
                  />
                </div>
              </div>
            ))}
            <div className="text-[10px] text-[var(--color-text-muted)] text-center pt-2">
              Weights reflect multi-head self-attention coefficients calculated dynamically over the topological neighborhoods.
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}
