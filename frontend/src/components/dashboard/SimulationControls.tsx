import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useCreateSimulation } from '../../api/queries';
import { DEFAULT_SIMULATION_CONFIG } from '../../utils/constants';
import type { SimulationConfig } from '../../api/types';

interface SimulationControlsProps {
  onSimulationCreated: (id: string) => void;
}

export default function SimulationControls({ onSimulationCreated }: SimulationControlsProps) {
  const [config, setConfig] = useState<Partial<SimulationConfig>>(DEFAULT_SIMULATION_CONFIG);
  const [isLargeMonitor, setIsLargeMonitor] = useState(() => {
    return typeof window !== 'undefined' && window.innerWidth >= 1600 && window.innerHeight >= 900;
  });
  const [isExpanded, setIsExpanded] = useState(false);
  const createMutation = useCreateSimulation();

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleResize = () => {
      setIsLargeMonitor(window.innerWidth >= 1600 && window.innerHeight >= 900);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const handleStart = () => {
    createMutation.mutate(config, {
      onSuccess: (data) => {
        onSimulationCreated(data.id);
      },
    });
  };

  const updateConfig = <K extends keyof SimulationConfig>(key: K, value: SimulationConfig[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="glass-card p-5 h-full flex flex-col"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          Simulation Configuration
        </h3>
        {!isLargeMonitor && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
          >
            {isExpanded ? 'Collapse ▴' : 'Expand ▾'}
          </button>
        )}
      </div>

      {/* Scrollable Settings Form */}
      <div className="flex-1 overflow-y-auto min-h-0 space-y-4 mb-4 pr-1">
        {/* Core Settings - always visible */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-2">
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Rounds</label>
            <input
              type="number"
              value={config.num_rounds}
              onChange={(e) => updateConfig('num_rounds', parseInt(e.target.value) || 10)}
              min={1}
              max={100}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Local Epochs</label>
            <input
              type="number"
              value={config.local_epochs}
              onChange={(e) => updateConfig('local_epochs', parseInt(e.target.value) || 3)}
              min={1}
              max={20}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">Learning Rate</label>
            <input
              type="number"
              value={config.learning_rate}
              onChange={(e) => updateConfig('learning_rate', parseFloat(e.target.value) || 0.001)}
              step={0.0001}
              min={0.0001}
              max={1}
              className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
            />
          </div>
        </div>

        {/* Advanced Settings */}
        {(isExpanded || isLargeMonitor) && (
          <motion.div
            initial={isLargeMonitor ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            transition={{ duration: 0.3 }}
            className="space-y-4 border-t border-[var(--color-border-subtle)] pt-4"
          >
            {/* FL Engine Selection */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                FL Engine
              </h4>
              <select
                value={config.fl_engine_type}
                onChange={(e) => updateConfig('fl_engine_type', e.target.value as SimulationConfig['fl_engine_type'])}
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
              >
                <option value="custom">Custom Engine (Built-in Simulator)</option>
                <option value="flower">Flower Framework (flwr.dev)</option>
              </select>
              {config.fl_engine_type === 'flower' && (
                <p className="text-[10px] text-[var(--color-accent-amber)] mt-1">
                  ⚡ Flower mode uses FedAvg only. Dropout, latency, poisoning, and Byzantine-robust aggregation are disabled.
                </p>
              )}
            </div>

            {/* Privacy */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Privacy Mechanism
              </h4>
              <select
                value={config.privacy_mechanism}
                onChange={(e) => updateConfig('privacy_mechanism', e.target.value as SimulationConfig['privacy_mechanism'])}
                className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
              >
                <option value="none">None</option>
                <option value="differential_privacy">Differential Privacy</option>
                <option value="secure_aggregation">Secure Aggregation</option>
                <option value="both">Both</option>
              </select>
            </div>
          </motion.div>
        )}
      </div>

      {/* Start Button */}
      <button
        onClick={handleStart}
        disabled={createMutation.isPending}
        className="mt-auto w-full py-2.5 rounded-lg font-semibold text-sm text-white transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/40"
        style={{
          background: 'linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)',
        }}
      >
        {createMutation.isPending ? (
          <span className="flex items-center justify-center gap-2">
            <span className="animate-spin">⟳</span> Starting...
          </span>
        ) : (
          'Start Federated Training'
        )}
      </button>

      {createMutation.isError && (
        <p className="mt-2 text-xs text-rose-400">
          Failed to start simulation. Is the backend running?
        </p>
      )}
    </motion.div>
  );
}
