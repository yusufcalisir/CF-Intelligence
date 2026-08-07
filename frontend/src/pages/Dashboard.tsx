import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { useBanks, useSimulations } from '../api/queries';
import BankCard from '../components/dashboard/BankCard';
import DataDriftPanel from '../components/dashboard/DataDriftPanel';
import SimulationControls from '../components/dashboard/SimulationControls';
import { formatDuration } from '../utils/formatters';

export default function Dashboard() {
  const navigate = useNavigate();
  const { data: banks, isLoading: banksLoading } = useBanks();
  const { data: simulations, isLoading: simsLoading } = useSimulations();
  const [, setLastSimId] = useState<string | null>(null);





  const handleSimulationCreated = (id: string) => {
    setLastSimId(id);
    // Navigate to the simulation view after a brief delay
    setTimeout(() => navigate(`/simulation/${id}`), 500);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* Hero */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        style={{ flexShrink: 0, padding: '0.75rem 0' }}
      >
        <h1
          style={{
            margin: '0 0 0.5rem 0',
            fontSize: 'clamp(1.75rem, 4vw, 2.25rem)',
            fontWeight: 800,
            background: 'linear-gradient(135deg, #818cf8 0%, #2dd4bf 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
            letterSpacing: '-0.02em',
            lineHeight: 1.1,
          }}
        >
          Collaborative Fraud Intelligence
        </h1>
        <p style={{ margin: 0, fontSize: '0.875rem', color: '#94a3b8', maxWidth: '56rem', lineHeight: 1.6 }}>
          Three independent banks collaboratively train a PyTorch fraud detection model using Federated Learning (FedAvg) and Differential Privacy (DP), without pooling raw transaction logs.
        </p>
      </motion.div>

      {/* Bank Cards */}
      <div style={{ flexShrink: 0, marginBottom: '0.5rem' }}>
        <h2 style={{ margin: '0 0 0.75rem 0', fontSize: '0.7rem', fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Participating Institutions
        </h2>
        {banksLoading ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
            {[0, 1, 2].map((i) => (
              <div key={i} style={{ height: 144, borderRadius: '0.75rem', background: 'rgba(15,22,41,0.5)', animation: 'pulse 2s infinite' }} />
            ))}
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0.75rem' }}>
            {banks?.map((bank, idx) => (
              <BankCard key={bank.id} bank={bank} index={idx} />
            ))}
          </div>
        )}
      </div>

      {/* Data Drift Visualization */}
      <div className="shrink-0 mb-2">
        <DataDriftPanel />
      </div>

      {/* Controls + Recent Simulations */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '1rem', alignItems: 'start' }} className="!grid-cols-1 lg:!grid-cols-3">
        <div style={{ gridColumn: 'span 2' }}>
          <SimulationControls onSimulationCreated={handleSimulationCreated} />
        </div>

        {/* Recent Simulations */}
        <div className="glass-card" style={{ padding: '1rem', display: 'flex', flexDirection: 'column', maxHeight: 600 }}>
          <h3 style={{ margin: '0 0 0.75rem 0', fontSize: '0.75rem', fontWeight: 600, color: '#e2e8f0', flexShrink: 0 }}>
            Recent Simulations
          </h3>
          {simsLoading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', flex: 1, overflow: 'hidden' }}>
              {[0, 1, 2].map((i) => (
                <div key={i} style={{ height: 48, background: 'rgba(26,32,64,0.5)', borderRadius: '0.5rem' }} />
              ))}
            </div>
          ) : simulations && simulations.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem', flex: 1, overflowY: 'auto', paddingRight: '0.25rem' }}>
              {simulations.map((sim) => {
                const isCompleted = sim.status === 'completed';
                const isFailed = sim.status === 'failed';
                const isRunning = !isCompleted && !isFailed;
                const idSlice = sim.id.slice(0, 8).toUpperCase();
                const timeStr = new Date(sim.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

                const iconBg = isCompleted ? 'rgba(16,185,129,0.12)' : isFailed ? 'rgba(239,68,68,0.12)' : 'rgba(99,102,241,0.12)';
                const iconColor = isCompleted ? '#10b981' : isFailed ? '#ef4444' : '#818cf8';

                return (
                  <button
                    key={sim.id}
                    onClick={() => navigate(`/simulation/${sim.id}`)}
                    style={{
                      width: '100%', textAlign: 'left', padding: '0.875rem', borderRadius: '0.75rem',
                      background: 'rgba(26,32,64,0.5)', border: '1px solid rgba(30,42,74,0.8)',
                      cursor: 'pointer', display: 'flex', gap: '0.75rem', alignItems: 'center',
                      transition: 'border-color 0.2s, background 0.2s',
                    }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLElement).style.background = 'rgba(99,102,241,0.08)';
                      (e.currentTarget as HTMLElement).style.borderColor = 'rgba(99,102,241,0.3)';
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLElement).style.background = 'rgba(26,32,64,0.5)';
                      (e.currentTarget as HTMLElement).style.borderColor = 'rgba(30,42,74,0.8)';
                    }}
                  >
                    <div style={{ width: 32, height: 32, borderRadius: '0.5rem', display: 'flex', alignItems: 'center', justifyContent: 'center', background: iconBg, color: iconColor, flexShrink: 0 }}>
                      {isCompleted && <span style={{ fontSize: '0.75rem', fontWeight: 700 }}>✓</span>}
                      {isFailed && <span style={{ fontSize: '0.75rem', fontWeight: 700 }}>✗</span>}
                      {isRunning && <div style={{ width: 14, height: 14, border: `2px solid ${iconColor}`, borderTopColor: 'transparent', borderRadius: '50%' }} className="animate-spin" />}
                    </div>

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.375rem' }}>
                        <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#e2e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          Simulation #{idSlice}
                        </span>
                        <span style={{ fontSize: '10px', color: '#64748b', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>{timeStr}</span>
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.375rem', fontSize: '9px', color: '#94a3b8' }}>
                        <span style={{ padding: '2px 6px', borderRadius: '0.25rem', background: 'rgba(8,12,24,0.6)', border: '1px solid rgba(30,42,74,0.6)' }}>
                          Rounds: {sim.current_round}/{sim.total_rounds}
                        </span>
                        {sim.duration_seconds && (
                          <span style={{ padding: '2px 6px', borderRadius: '0.25rem', background: 'rgba(8,12,24,0.6)', border: '1px solid rgba(30,42,74,0.6)' }}>
                            {formatDuration(sim.duration_seconds)}
                          </span>
                        )}
                        <StatusBadge status={sim.status} />
                      </div>
                      {isRunning && (
                        <div style={{ width: '100%', height: 3, background: 'rgba(8,12,24,0.8)', borderRadius: 9999, marginTop: 10, overflow: 'hidden' }}>
                          <div style={{ height: '100%', borderRadius: 9999, background: 'linear-gradient(90deg,#6366f1,#14b8a6)', width: `${sim.progress_pct}%`, transition: 'width 0.5s' }} />
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '1rem' }}>
              <div style={{ position: 'relative', width: 64, height: 64, marginBottom: '1rem', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: '2px solid rgba(99,102,241,0.2)' }} className="animate-ping" />
                <div style={{ position: 'absolute', width: 40, height: 40, borderRadius: '50%', border: '1px solid rgba(20,184,166,0.3)' }} className="animate-pulse" />
                <div style={{ width: 16, height: 16, borderRadius: '50%', background: '#6366f1', boxShadow: '0 0 12px rgba(99,102,241,0.5)' }} />
              </div>
              <p style={{ margin: 0, fontSize: '0.75rem', fontWeight: 600, color: '#94a3b8' }}>No simulations yet</p>
              <p style={{ margin: '0.25rem 0 0', fontSize: '10px', color: '#64748b', maxWidth: 200 }}>
                Configure and start your first federated training run above.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  type BadgeStyle = { background: string; color: string };
  const fallback: BadgeStyle = { background: 'rgba(139,92,246,0.15)', color: '#8b5cf6' };
  const styleMap: Record<string, BadgeStyle> = {
    completed:          { background: 'rgba(16,185,129,0.15)',  color: '#10b981' },
    failed:             { background: 'rgba(239,68,68,0.15)',   color: '#ef4444' },
    pending:            fallback,
    training_federated: { background: 'rgba(59,130,246,0.15)',  color: '#3b82f6' },
    training_local:     { background: 'rgba(59,130,246,0.15)',  color: '#3b82f6' },
    generating_data:    { background: 'rgba(245,158,11,0.15)',  color: '#f59e0b' },
    evaluating:         { background: 'rgba(20,184,166,0.15)',  color: '#14b8a6' },
  };
  const s = styleMap[status] ?? fallback;

  return (
    <span style={{ fontSize: '9px', padding: '2px 8px', borderRadius: 9999, fontWeight: 500, background: s.background, color: s.color }}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
