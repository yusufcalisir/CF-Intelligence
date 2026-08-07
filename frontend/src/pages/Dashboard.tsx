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
    <div className="flex flex-col gap-4">
      {/* Hero */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="shrink-0 py-3 md:py-5"
      >
        <h1 className="text-3xl md:text-4xl font-extrabold gradient-text tracking-tight mb-2">
          Collaborative Fraud Intelligence
        </h1>
        <p className="text-sm md:text-base text-[var(--color-text-secondary)] max-w-4xl leading-relaxed">
          Three independent banks collaboratively train a PyTorch fraud detection model using Federated Learning (FedAvg) and Differential Privacy (DP), without pooling raw transaction logs.
        </p>
      </motion.div>

      {/* Bank Cards */}
      <div className="shrink-0 mb-2">
        <h2 className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-3">
          Participating Institutions
        </h2>
        {banksLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="glass-card p-4 h-36 animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
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
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Simulation Controls */}
        <div className="lg:col-span-2 flex flex-col pr-1">
          <SimulationControls onSimulationCreated={handleSimulationCreated} />
        </div>

        {/* Recent Simulations */}
        <div className="glass-card p-4 flex flex-col max-h-[600px]">
          <h3 className="text-xs font-semibold text-[var(--color-text-primary)] mb-3 shrink-0">
            Recent Simulations
          </h3>
          {simsLoading ? (
            <div className="space-y-2 flex-1 overflow-hidden">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-12 bg-[var(--color-bg-elevated)] rounded-lg animate-pulse" />
              ))}
            </div>
          ) : simulations && simulations.length > 0 ? (
            <div className="space-y-2.5 flex-1 overflow-y-auto pr-1">
              {simulations.map((sim) => {
                const isCompleted = sim.status === 'completed';
                const isFailed = sim.status === 'failed';
                const isRunning = !isCompleted && !isFailed;
                const idSlice = sim.id.slice(0, 8).toUpperCase();
                const timeStr = new Date(sim.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

                return (
                  <button
                    key={sim.id}
                    onClick={() => navigate(`/simulation/${sim.id}`)}
                    className="w-full text-left p-3.5 rounded-xl bg-[var(--color-bg-elevated)] hover:bg-[var(--color-bg-card-hover)] border border-[var(--color-border-subtle)] hover:border-[var(--color-accent-indigo)]/50 transition-all duration-300 flex gap-3 items-center group relative overflow-hidden"
                  >
                    {/* Status Icon Indicator */}
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                      isCompleted ? 'bg-[var(--color-status-success)]/10 text-[var(--color-status-success)]' :
                      isFailed ? 'bg-[var(--color-status-error)]/10 text-[var(--color-status-error)]' :
                      'bg-[var(--color-accent-indigo)]/10 text-[var(--color-accent-indigo)]'
                    }`}>
                      {isCompleted && <span className="text-xs font-bold">✓</span>}
                      {isFailed && <span className="text-xs font-bold">✗</span>}
                      {isRunning && (
                        <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                      )}
                    </div>

                    {/* Simulation Details */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2 mb-1.5">
                        <span className="text-xs font-semibold text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-indigo-light)] transition-colors truncate">
                          Simulation #{idSlice}
                        </span>
                        <span className="text-[10px] text-[var(--color-text-muted)] font-mono shrink-0">
                          {timeStr}
                        </span>
                      </div>

                      <div className="flex flex-wrap items-center gap-1.5 text-[9px] text-[var(--color-text-secondary)]">
                        <span className="px-1.5 py-0.5 rounded bg-[var(--color-bg-primary)] border border-[var(--color-border-subtle)]">
                          Rounds: {sim.current_round}/{sim.total_rounds}
                        </span>
                        {sim.duration_seconds && (
                          <span className="px-1.5 py-0.5 rounded bg-[var(--color-bg-primary)] border border-[var(--color-border-subtle)]">
                            {formatDuration(sim.duration_seconds)}
                          </span>
                        )}
                        <StatusBadge status={sim.status} />
                      </div>

                      {/* Micro progress bar */}
                      {isRunning && (
                        <div className="w-full h-1 bg-[var(--color-bg-primary)] rounded-full mt-2.5 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)] transition-all duration-500"
                            style={{ width: `${sim.progress_pct}%` }}
                          />
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-4">
              <div className="relative w-16 h-16 mb-4 flex items-center justify-center">
                {/* Outer pulsing ring */}
                <div className="absolute inset-0 rounded-full border-2 border-[var(--color-accent-indigo)]/20 animate-ping" style={{ animationDuration: '3s' }} />
                {/* Inner pulsing ring */}
                <div className="absolute w-10 h-10 rounded-full border border-[var(--color-accent-teal)]/30 animate-pulse" />
                {/* Core node */}
                <div className="w-4 h-4 rounded-full bg-[var(--color-accent-indigo)] shadow-[0_0_12px_rgba(99,102,241,0.5)]" />
              </div>
              <p className="text-xs font-semibold text-[var(--color-text-secondary)]">No simulations yet</p>
              <p className="text-[10px] text-[var(--color-text-muted)] max-w-[200px] mt-1">
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
  const styles: Record<string, string> = {
    completed: 'bg-[var(--color-status-success)]/15 text-[var(--color-status-success)]',
    failed: 'bg-[var(--color-status-error)]/15 text-[var(--color-status-error)]',
    pending: 'bg-[var(--color-status-pending)]/15 text-[var(--color-status-pending)]',
    training_federated: 'bg-[var(--color-status-info)]/15 text-[var(--color-status-info)]',
    training_local: 'bg-[var(--color-status-info)]/15 text-[var(--color-status-info)]',
    generating_data: 'bg-[var(--color-status-warning)]/15 text-[var(--color-status-warning)]',
    evaluating: 'bg-[var(--color-accent-teal)]/15 text-[var(--color-accent-teal)]',
  };

  return (
    <span className={`text-[9px] px-2 py-0.5 rounded-full font-medium ${styles[status] ?? styles.pending}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
