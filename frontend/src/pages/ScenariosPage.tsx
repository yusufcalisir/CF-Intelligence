import { useState, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import { useScenarios, useStartScenario, useScenarioStatus } from '../api/queries';
import ChaosAttackInjectorPanel from '../components/chaos/ChaosAttackInjectorPanel';
import { BANK_NAMES } from '../api/types';

export default function ScenariosPage() {
  const { data: scenarios, isLoading } = useScenarios();
  const startScenario = useStartScenario();
  const [activeScenarioId, setActiveScenarioId] = useState<string | undefined>();
  const [speed, setSpeed] = useState(1.0);
  const pageRef = useRef<HTMLDivElement>(null);

  const { data: status } = useScenarioStatus(activeScenarioId);

  const handleStart = useCallback(async (scenarioType: string) => {
    const result = await startScenario.mutateAsync({
      scenario_type: scenarioType,
      speed_multiplier: speed,
    });
    setActiveScenarioId(result.scenario_id);
    
    // Scroll to top of the scenarios page container to see the active scenario panel running
    setTimeout(() => {
      pageRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }, 100);
  }, [startScenario, speed]);

  const SCENARIO_ICONS: Record<string, string> = {
    fraud_ring: '🕸️',
    account_takeover: '🔐',
    money_laundering: '💰',
    card_testing: '💳',
  };

  const SCENARIO_GRADIENTS: Record<string, string> = {
    fraud_ring: 'linear-gradient(135deg, #6366f1 0%, #a855f7 100%)',
    account_takeover: 'linear-gradient(135deg, #ef4444 0%, #f97316 100%)',
    money_laundering: 'linear-gradient(135deg, #14b8a6 0%, #06b6d4 100%)',
    card_testing: 'linear-gradient(135deg, #ec4899 0%, #f43f5e 100%)',
  };

  return (
    <div ref={pageRef} className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <h1 className="text-2xl font-bold gradient-text mb-1">
          Fraud Scenarios
        </h1>
        <p className="text-sm text-[var(--color-text-muted)] max-w-2xl">
          Pre-built fraud scenarios demonstrating why collaborative intelligence improves detection.
          Each scenario shows what individual banks see vs. what collaboration reveals.
        </p>
      </motion.div>

      {/* Speed Control */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-4 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4"
      >
        <span className="text-sm text-[var(--color-text-muted)]">Replay Speed:</span>
        <input
          type="range"
          min="0.5"
          max="5"
          step="0.5"
          value={speed}
          onChange={(e) => setSpeed(parseFloat(e.target.value))}
          className="w-full sm:flex-1 max-w-xs accent-[var(--color-accent-indigo)]"
        />
        <span className="text-sm font-mono font-bold w-12 text-left sm:text-right">{speed}x</span>
      </motion.div>

      {/* Interactive Chaos & Live Attack Simulator */}
      <ChaosAttackInjectorPanel />

      {/* Active Scenario Status */}
      {status && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="glass-card p-5"
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-bold">
              {status.status === 'running' ? '▶️ Scenario Running' :
               status.status === 'completed' ? '✅ Scenario Complete' :
               '⏹️ Scenario Stopped'}
            </h3>
            <span className={`px-2 py-1 rounded text-xs font-bold ${
              status.status === 'running'
                ? 'bg-green-500/20 text-green-400'
                : 'bg-gray-500/20 text-gray-400'
            }`}>
              {status.status}
            </span>
          </div>

          <div className="mb-3">
            <div className="flex justify-between text-xs text-[var(--color-text-muted)] mb-1">
              <span>Event {status.delivered_events} of {status.total_events}</span>
              <span>{((status.delivered_events / status.total_events) * 100).toFixed(0)}%</span>
            </div>
            <div className="h-2 bg-[var(--color-bg-elevated)] rounded-full overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)]"
                initial={{ width: 0 }}
                animate={{ width: `${(status.delivered_events / status.total_events) * 100}%` }}
                transition={{ duration: 0.3 }}
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4 text-center text-xs">
            <div>
              <div className="font-bold text-sm">{status.delivered_events}</div>
              <div className="text-[var(--color-text-muted)]">Events Delivered</div>
            </div>
            <div>
              <div className="font-bold text-sm">{status.speed_multiplier}x</div>
              <div className="text-[var(--color-text-muted)]">Speed</div>
            </div>
            <div>
              <div className="font-bold text-sm">{status.total_events}</div>
              <div className="text-[var(--color-text-muted)]">Total Events</div>
            </div>
          </div>
        </motion.div>
      )}

      {/* Scenario Cards */}
      {isLoading ? (
        <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">Loading scenarios...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {scenarios?.map((scenario, i) => (
            <motion.div
              key={scenario.type}
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.1 }}
              className="glass-card overflow-hidden hover:scale-[1.02] transition-transform"
            >
              {/* Gradient Header */}
              <div
                className="h-2"
                style={{ background: SCENARIO_GRADIENTS[scenario.type] || SCENARIO_GRADIENTS.fraud_ring }}
              />

              <div className="p-5">
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-3xl">{SCENARIO_ICONS[scenario.type] || '🎯'}</span>
                  <div>
                    <h3 className="font-bold">{scenario.name}</h3>
                    <span className="text-xs text-[var(--color-text-muted)]">
                      {scenario.estimated_events} events • ~{scenario.estimated_duration_seconds}s
                    </span>
                  </div>
                </div>

                <p className="text-sm text-[var(--color-text-muted)] mb-4 leading-relaxed">
                  {scenario.description}
                </p>

                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                  <div className="flex flex-wrap gap-1">
                    {scenario.banks_involved.map((bankId) => (
                      <span
                        key={bankId}
                        className="px-2 py-0.5 text-[10px] rounded bg-[var(--color-bg-elevated)] text-[var(--color-text-muted)]"
                      >
                        {BANK_NAMES[bankId] || bankId}
                      </span>
                    ))}
                  </div>

                  <button
                    onClick={() => handleStart(scenario.type)}
                    disabled={startScenario.isPending || status?.status === 'running'}
                    className="h-11 min-h-[44px] px-5 text-sm font-semibold rounded-xl text-white hover:opacity-90 disabled:opacity-50 transition-all w-full sm:w-auto shrink-0 flex items-center justify-center gap-2 cursor-pointer shadow-md"
                    style={{ background: SCENARIO_GRADIENTS[scenario.type] || SCENARIO_GRADIENTS.fraud_ring }}
                  >
                    {startScenario.isPending ? (
                      <>
                        <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                        <span>Starting...</span>
                      </>
                    ) : (
                      <span>▶ Run</span>
                    )}
                  </button>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}
