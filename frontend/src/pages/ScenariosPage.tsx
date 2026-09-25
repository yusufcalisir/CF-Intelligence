import { useState, useCallback, useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useScenarios, useStartScenario, useScenarioStatus, useStopScenario, useActiveScenarios } from '../api/queries';
import ChaosAttackInjectorPanel from '../components/chaos/ChaosAttackInjectorPanel';
import { BANK_NAMES } from '../api/types';

const SCENARIOS_SESSION_KEY = 'cfi_scenarios_active_id_v1';
const SCENARIOS_SPEED_KEY = 'cfi_scenarios_speed_v1';

export default function ScenariosPage() {
  const { data: scenarios, isLoading } = useScenarios();
  const { data: activeList } = useActiveScenarios();
  const startScenario = useStartScenario();
  const stopScenario = useStopScenario();

  const [activeScenarioId, setActiveScenarioId] = useState<string | undefined>(() => {
    try {
      return sessionStorage.getItem(SCENARIOS_SESSION_KEY) || undefined;
    } catch {
      return undefined;
    }
  });

  const [speed, setSpeed] = useState<number>(() => {
    try {
      const stored = sessionStorage.getItem(SCENARIOS_SPEED_KEY);
      return stored ? parseFloat(stored) : 1.0;
    } catch {
      return 1.0;
    }
  });

  const pageRef = useRef<HTMLDivElement>(null);

  const { data: status, isError } = useScenarioStatus(activeScenarioId);

  // Sync active scenario ID to sessionStorage
  useEffect(() => {
    try {
      if (activeScenarioId) {
        sessionStorage.setItem(SCENARIOS_SESSION_KEY, activeScenarioId);
      } else {
        sessionStorage.removeItem(SCENARIOS_SESSION_KEY);
      }
    } catch { /* ignore */ }
  }, [activeScenarioId]);

  // Sync speed multiplier to sessionStorage
  useEffect(() => {
    try {
      sessionStorage.setItem(SCENARIOS_SPEED_KEY, speed.toString());
    } catch { /* ignore */ }
  }, [speed]);

  // If the active scenario ID returns 404 (e.g. backend restarted), clean it up
  useEffect(() => {
    if (isError) {
      setActiveScenarioId(undefined);
      try {
        sessionStorage.removeItem(SCENARIOS_SESSION_KEY);
      } catch { /* ignore */ }
    }
  }, [isError]);

  // If no scenario is selected locally but the backend has an active stream running, auto-bind to it
  useEffect(() => {
    if (!activeScenarioId && activeList && activeList.length > 0) {
      const running = activeList.find((s) => s.status?.toLowerCase() === 'running') || activeList[0];
      if (running?.scenario_id) {
        setActiveScenarioId(running.scenario_id);
      }
    }
  }, [activeScenarioId, activeList]);

  const handleStart = useCallback(async (scenarioType: string) => {
    const result = await startScenario.mutateAsync({
      scenario_type: scenarioType,
      speed_multiplier: speed,
    });
    setActiveScenarioId(result.scenario_id);
    try {
      sessionStorage.setItem(SCENARIOS_SESSION_KEY, result.scenario_id);
    } catch { /* ignore */ }
    
    // Scroll to top of the scenarios page container to see the active scenario panel running
    setTimeout(() => {
      pageRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }, 100);
  }, [startScenario, speed]);

  const handleStop = useCallback(async () => {
    if (activeScenarioId) {
      await stopScenario.mutateAsync(activeScenarioId);
    }
  }, [activeScenarioId, stopScenario]);

  const handleDismiss = useCallback(() => {
    setActiveScenarioId(undefined);
    try {
      sessionStorage.removeItem(SCENARIOS_SESSION_KEY);
    } catch { /* ignore */ }
  }, []);

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

  const isRunning = status?.status?.toLowerCase() === 'running';
  const isCompleted = status?.status?.toLowerCase() === 'completed';
  const isStopped = status?.status?.toLowerCase() === 'stopped';
  const deliveredEvents = status?.delivered_events ?? (status as any)?.current_event ?? 0;
  const totalEvents = status?.total_events ?? 0;
  const progressPct = totalEvents > 0 ? Math.min(100, Math.round((deliveredEvents / totalEvents) * 100)) : 0;
  const logs: string[] = (status as any)?.logs && Array.isArray((status as any).logs) ? (status as any).logs : [];

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
          className="glass-card p-5 border border-indigo-500/30 shadow-lg"
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-bold flex items-center gap-2">
              <span>
                {isRunning ? '▶️ Scenario Running' :
                 isCompleted ? '✅ Scenario Complete' :
                 isStopped ? '⏹️ Scenario Stopped' :
                 `ℹ️ Scenario ${status?.status ?? ''}`}
              </span>
            </h3>
            <div className="flex items-center gap-2">
              <span className={`px-2.5 py-1 rounded-full text-xs font-bold ${
                isRunning
                  ? 'bg-green-500/20 text-green-400 border border-green-500/30 animate-pulse'
                  : 'bg-gray-500/20 text-gray-400 border border-gray-500/30'
              }`}>
                {status.status}
              </span>
              {isRunning ? (
                <button
                  onClick={handleStop}
                  disabled={stopScenario.isPending}
                  className="px-2.5 py-1 rounded text-xs font-semibold bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 border border-rose-500/40 transition-all cursor-pointer"
                >
                  {stopScenario.isPending ? 'Stopping...' : '⏹ Stop'}
                </button>
              ) : (
                <button
                  onClick={handleDismiss}
                  className="px-2.5 py-1 rounded text-xs font-semibold bg-white/10 text-slate-300 hover:bg-white/20 border border-white/15 transition-all cursor-pointer"
                >
                  ✕ Dismiss
                </button>
              )}
            </div>
          </div>

          <div className="mb-3">
            <div className="flex justify-between text-xs text-[var(--color-text-muted)] mb-1">
              <span>Event {deliveredEvents} of {totalEvents}</span>
              <span>{progressPct}%</span>
            </div>
            <div className="h-2 bg-[var(--color-bg-elevated)] rounded-full overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)]"
                initial={{ width: 0 }}
                animate={{ width: `${progressPct}%` }}
                transition={{ duration: 0.3 }}
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4 text-center text-xs">
            <div>
              <div className="font-bold text-sm">{deliveredEvents}</div>
              <div className="text-[var(--color-text-muted)]">Events Delivered</div>
            </div>
            <div>
              <div className="font-bold text-sm">{status.speed_multiplier ?? speed}x</div>
              <div className="text-[var(--color-text-muted)]">Speed</div>
            </div>
            <div>
              <div className="font-bold text-sm">{totalEvents}</div>
              <div className="text-[var(--color-text-muted)]">Total Events</div>
            </div>
          </div>

          {logs.length > 0 && (
            <div className="mt-4 pt-3 border-t border-[var(--color-border-subtle)] text-xs font-mono space-y-1">
              <div className="text-[var(--color-text-muted)] font-semibold mb-1">Live Logs:</div>
              {logs.map((log, idx) => (
                <div key={idx} className="text-slate-300 truncate">
                  &gt; {log}
                </div>
              ))}
            </div>
          )}
        </motion.div>
      )}

      {/* Scenario Cards */}
      {isLoading ? (
        <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">Loading scenarios...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {scenarios?.map((scenario, i) => {
            const scenarioType = scenario.type || (scenario as any).scenario_type || `scenario_${i}`;
            const scenarioName = scenario.name || scenarioType;
            const scenarioDesc = scenario.description || '';
            const scenarioEvents = scenario.estimated_events ?? (scenario as any).event_count ?? 0;
            const scenarioDuration = scenario.estimated_duration_seconds ?? 30;
            const banks = scenario.banks_involved || [];

            return (
              <motion.div
                key={scenarioType}
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
                className="glass-card overflow-hidden hover:scale-[1.02] transition-transform"
              >
                {/* Gradient Header */}
                <div
                  className="h-2"
                  style={{ background: SCENARIO_GRADIENTS[scenarioType] || SCENARIO_GRADIENTS.fraud_ring }}
                />

                <div className="p-5">
                  <div className="flex items-center gap-3 mb-3">
                    <span className="text-3xl">{SCENARIO_ICONS[scenarioType] || '🎯'}</span>
                    <div>
                      <h3 className="font-bold">{scenarioName}</h3>
                      <span className="text-xs text-[var(--color-text-muted)]">
                        {scenarioEvents} events • ~{scenarioDuration}s
                      </span>
                    </div>
                  </div>

                  <p className="text-sm text-[var(--color-text-muted)] mb-4 leading-relaxed">
                    {scenarioDesc}
                  </p>

                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                    <div className="flex flex-wrap gap-1">
                      {banks.map((bankId, bIdx) => (
                        <span
                          key={bankId || bIdx}
                          className="px-2 py-0.5 text-[10px] rounded bg-[var(--color-bg-elevated)] text-[var(--color-text-muted)]"
                        >
                          {BANK_NAMES[bankId] || bankId}
                        </span>
                      ))}
                    </div>

                    <button
                      onClick={() => handleStart(scenarioType)}
                      disabled={startScenario.isPending || isRunning}
                      className="h-11 min-h-[44px] px-5 text-sm font-semibold rounded-xl text-white hover:opacity-90 disabled:opacity-50 transition-all w-full sm:w-auto shrink-0 flex items-center justify-center gap-2 cursor-pointer shadow-md"
                      style={{ background: SCENARIO_GRADIENTS[scenarioType] || SCENARIO_GRADIENTS.fraud_ring }}
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
            );
          })}
        </div>
      )}
    </div>
  );
}
