import { useState, useId, useEffect } from 'react';
import {
  ShieldAlert,
  Zap,
  Radio,
  RefreshCw,
  Cpu,
  TrendingDown,
  Lock,
  AlertCircle,
  X,
} from 'lucide-react';
import { useInjectAttack } from '../../api/queries';
import type { AttackInjectionResponse } from '../../api/types';

export const CHAOS_ATTACK_SESSION_KEY = 'cfi_chaos_active_attack_v1';
export const CHAOS_DEFENSE_SESSION_KEY = 'cfi_chaos_selected_defense_v1';
export const CHAOS_INTENSITY_SESSION_KEY = 'cfi_chaos_intensity_v1';

interface ChaosAttackInjectorProps {
  onAttackTriggered?: (response: AttackInjectionResponse) => void;
  onQuarantineChange?: (bankId: string | null) => void;
}

export default function ChaosAttackInjectorPanel({
  onAttackTriggered,
  onQuarantineChange,
}: ChaosAttackInjectorProps) {
  const injectAttackMutation = useInjectAttack();

  // Restore active attack telemetry from sessionStorage across route changes
  const [activeAttack, setActiveAttack] = useState<AttackInjectionResponse | null>(() => {
    try {
      const stored = sessionStorage.getItem(CHAOS_ATTACK_SESSION_KEY);
      return stored ? (JSON.parse(stored) as AttackInjectionResponse) : null;
    } catch {
      return null;
    }
  });

  // Restore defense selection
  const [selectedDefense, setSelectedDefense] = useState<'krum' | 'trimmed_mean' | 'bulyan' | 'spectral'>(() => {
    try {
      const stored = sessionStorage.getItem(CHAOS_DEFENSE_SESSION_KEY);
      if (stored && ['krum', 'trimmed_mean', 'bulyan', 'spectral'].includes(stored)) {
        return stored as 'krum' | 'trimmed_mean' | 'bulyan' | 'spectral';
      }
    } catch { /* ignore */ }
    return 'krum';
  });

  // Restore intensity rate
  const [intensity, setIntensity] = useState<number>(() => {
    try {
      const stored = sessionStorage.getItem(CHAOS_INTENSITY_SESSION_KEY);
      return stored ? parseInt(stored, 10) : 500;
    } catch {
      return 500;
    }
  });

  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const intensityInputId = useId();

  // Persist active attack telemetry to sessionStorage
  useEffect(() => {
    try {
      if (activeAttack) {
        sessionStorage.setItem(CHAOS_ATTACK_SESSION_KEY, JSON.stringify(activeAttack));
      } else {
        sessionStorage.removeItem(CHAOS_ATTACK_SESSION_KEY);
      }
    } catch { /* ignore */ }
  }, [activeAttack]);

  // Persist selected defense strategy to sessionStorage
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAOS_DEFENSE_SESSION_KEY, selectedDefense);
    } catch { /* ignore */ }
  }, [selectedDefense]);

  // Persist intensity to sessionStorage
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAOS_INTENSITY_SESSION_KEY, intensity.toString());
    } catch { /* ignore */ }
  }, [intensity]);

  // Notify parent callbacks when telemetry is restored from sessionStorage on initial mount
  useEffect(() => {
    if (activeAttack) {
      onAttackTriggered?.(activeAttack);
      onQuarantineChange?.(activeAttack.adversary_quarantined ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLaunchSmurfing = async () => {
    setErrorMessage(null);
    try {
      const res = await injectAttackMutation.mutateAsync({
        attack_type: 'smurfing_layering',
        target_bank: 'bank_alpha',
        adversary_bank: 'bank_beta',
        intensity_rate: intensity,
        defense_strategy: 'psi_graph',
      });
      setActiveAttack(res);
      onAttackTriggered?.(res);
      onQuarantineChange?.(null);
    } catch (err: unknown) {
      const apiErr = err as { response?: { data?: { detail?: string } }; message?: string };
      const msg = apiErr?.response?.data?.detail || apiErr?.message || 'Attack injection failed. Please verify API connectivity.';
      setErrorMessage(`Smurfing attack failed: ${msg}`);
    }
  };

  const handleLaunchByzantine = async () => {
    setErrorMessage(null);
    try {
      const res = await injectAttackMutation.mutateAsync({
        attack_type: 'byzantine_poisoning',
        adversary_bank: 'bank_gamma',
        target_bank: 'bank_alpha',
        intensity_rate: intensity,
        defense_strategy: selectedDefense,
      });
      setActiveAttack(res);
      onAttackTriggered?.(res);
      onQuarantineChange?.('bank_gamma');
    } catch (err: unknown) {
      const apiErr = err as { response?: { data?: { detail?: string } }; message?: string };
      const msg = apiErr?.response?.data?.detail || apiErr?.message || 'Attack injection failed. Please verify API connectivity.';
      setErrorMessage(`Byzantine attack injection failed: ${msg}`);
    }
  };

  const handleReset = () => {
    setActiveAttack(null);
    setErrorMessage(null);
    try {
      sessionStorage.removeItem(CHAOS_ATTACK_SESSION_KEY);
    } catch { /* ignore */ }
    onQuarantineChange?.(null);
  };

  return (
    <div
      id="chaos-attack-injector-panel"
      className={`relative overflow-hidden rounded-2xl p-4 sm:p-5 transition-all duration-500 border ${
        activeAttack?.status === 'quarantined'
          ? 'bg-rose-950/20 border-rose-500/50 shadow-[0_0_40px_rgba(244,63,94,0.25)]'
          : activeAttack?.status === 'intercepted'
            ? 'bg-amber-950/20 border-amber-500/50 shadow-[0_0_40px_rgba(245,158,11,0.2)]'
            : 'bg-[var(--color-bg-card)] border-[var(--color-border)] shadow-md'
      }`}
    >
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3.5 border-b border-[var(--color-border-subtle)]">
        <div className="flex items-center gap-2.5">
          <div
            className={`p-2 rounded-xl shrink-0 ${
              activeAttack
                ? 'bg-rose-500/20 text-rose-400 animate-pulse'
                : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20'
            }`}
          >
            <ShieldAlert size={20} />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-base font-bold text-[var(--color-text-primary)]">
                Live Chaos & Attack Simulator
              </h3>
              <span
                id="threat-level-badge"
                className={`text-[10px] font-mono font-extrabold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                  activeAttack
                    ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40 animate-pulse'
                    : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                }`}
              >
                {activeAttack ? 'CRITICAL THREAT INJECTED' : 'CONSORTIUM NOMINAL'}
              </span>
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                SIMULATED DEMO
              </span>
            </div>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              Live chaos sandbox simulating real-world Byzantine gradient poisoning and 500 tx/s smurfing storms to test Krum, Bulyan & LSH-PSI defenses.
            </p>
          </div>
        </div>

        {/* Reset / Clean State Button */}
        {activeAttack && (
          <button
            id="neutralize-threat-btn"
            onClick={handleReset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl font-semibold text-xs text-white bg-emerald-600 hover:bg-emerald-500 active:scale-95 transition-all shadow-md shrink-0 self-start sm:self-auto cursor-pointer"
          >
            <RefreshCw size={13} />
            <span>Neutralize & Restore Quorum</span>
          </button>
        )}
      </div>

      {/* Real Error Notification Banner */}
      {errorMessage && (
        <div className="mt-3.5 p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <AlertCircle size={15} className="shrink-0 text-rose-400" />
            <span className="font-mono">{errorMessage}</span>
          </div>
          <button
            onClick={() => setErrorMessage(null)}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-white/10 transition-colors"
            title="Dismiss error"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Control & Trigger Actions Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5 mt-4">
        {/* Attack 1: Smurfing Storm */}
        <div className="p-3.5 rounded-xl bg-[var(--color-bg-primary)]/80 border border-[var(--color-border)] flex flex-col justify-between gap-3">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-amber-400 flex items-center gap-1.5">
                <Zap size={14} /> AML Layering Storm
              </span>
              <span className="text-[10px] font-mono text-slate-400 bg-white/5 px-2 py-0.5 rounded">
                Target: Bank Alpha
              </span>
            </div>
            <p className="text-xs text-[var(--color-text-secondary)] mt-1.5">
              Injects high-frequency ({intensity} tx/s) micro-transactions below the €10,000 reporting threshold to test autonomous LSH-PSI intersection.
            </p>

            {/* Defense Specification */}
            <div className="flex items-center gap-1.5 mt-2">
              <span className="text-[10px] text-[var(--color-text-muted)] font-semibold">Defense:</span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold">
                LSH-PSI + GRAPHSAGE
              </span>
            </div>
          </div>

          <button
            id="inject-smurfing-attack-btn"
            disabled={injectAttackMutation.isPending}
            onClick={handleLaunchSmurfing}
            className="w-full h-11 min-h-[44px] px-3 rounded-xl font-semibold text-xs text-white bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 active:scale-98 transition-all flex items-center justify-center gap-2 shadow-sm shrink-0 whitespace-nowrap cursor-pointer disabled:opacity-50"
          >
            {injectAttackMutation.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Injecting...</span>
              </>
            ) : (
              <>
                <Radio size={14} className="animate-pulse shrink-0" />
                <span>Inject 500 tx/s Smurfing Burst</span>
              </>
            )}
          </button>
        </div>

        {/* Attack 2: Byzantine Poisoning */}
        <div className="p-3.5 rounded-xl bg-[var(--color-bg-primary)]/80 border border-[var(--color-border)] flex flex-col justify-between gap-3">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-rose-400 flex items-center gap-1.5">
                <ShieldAlert size={14} /> Byzantine Gradient Poisoning
              </span>
              <span className="text-[10px] font-mono text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded">
                Adversary: Bank Gamma
              </span>
            </div>
            <p className="text-xs text-[var(--color-text-secondary)] mt-1.5">
              Bank Gamma uploads malicious sign-flipped gradient weights (Δw × -10.0). Krum evaluates Euclidean distances and drops the rogue update.
            </p>

            {/* Defense Selector */}
            <div className="flex items-center gap-1.5 mt-2">
              <span className="text-[10px] text-[var(--color-text-muted)] font-semibold">Defense:</span>
              {(['krum', 'trimmed_mean', 'bulyan', 'spectral'] as const).map((def) => (
                <button
                  key={def}
                  id={`defense-select-${def}`}
                  onClick={() => setSelectedDefense(def)}
                  className={`text-[10px] font-mono px-2 py-0.5 rounded transition-all cursor-pointer ${
                    selectedDefense === def
                      ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 font-bold'
                      : 'text-[var(--color-text-muted)] bg-white/5 hover:bg-white/10'
                  }`}
                >
                  {def.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <button
            id="inject-byzantine-attack-btn"
            disabled={injectAttackMutation.isPending}
            onClick={handleLaunchByzantine}
            className="w-full h-11 min-h-[44px] px-3 rounded-xl font-semibold text-xs text-white bg-gradient-to-r from-rose-600 to-red-700 hover:from-rose-500 hover:to-red-600 active:scale-98 transition-all flex items-center justify-center gap-2 shadow-sm shrink-0 whitespace-nowrap cursor-pointer disabled:opacity-50"
          >
            {injectAttackMutation.isPending ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin shrink-0" />
                <span>Injecting...</span>
              </>
            ) : (
              <>
                <ShieldAlert size={14} className="animate-pulse shrink-0" />
                <span>Inject Byzantine Poisoning</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Active Threat Live HUD */}
      {activeAttack && (
        <div
          id="active-threat-live-hud"
          className="mt-4 pt-3.5 border-t border-[var(--color-border-subtle)]"
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
            {/* Defense Shield Activated */}
            <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
                <Lock size={11} className="text-emerald-400" /> Defense Shield
              </span>
              <p className="text-xs font-bold text-emerald-400 mt-1 truncate">
                {activeAttack.defense_activated}
              </p>
              <span className="text-[10px] text-slate-400 font-mono">
                Latency: {activeAttack.mitigation_latency_ms.toFixed(1)}ms
              </span>
            </div>

            {/* Status / Quarantine */}
            <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
                <Cpu size={11} className="text-rose-400" /> Action Taken
              </span>
              <p
                id="active-quarantine-status"
                className="text-xs font-bold text-rose-400 mt-1 uppercase"
              >
                {activeAttack.status === 'quarantined'
                  ? `Quarantined: ${activeAttack.adversary_quarantined}`
                  : `Intercepted: ${activeAttack.packets_blocked} txs`}
              </p>
              <span className="text-[10px] text-slate-400 font-mono">
                {activeAttack.status === 'quarantined' ? 'Byzantine Dropped' : 'LSH Pool Isolated'}
              </span>
            </div>

            {/* Anomaly Metric */}
            <div className="p-2.5 rounded-xl bg-black/30 border border-white/5">
              <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
                <TrendingDown size={11} className="text-amber-400" /> Outlier Score
              </span>
              <p className="text-xs font-bold font-mono text-amber-400 mt-1">
                {activeAttack.euclidean_distance > 0
                  ? `Δ ${activeAttack.euclidean_distance.toFixed(1)} (Cutoff: ${activeAttack.distance_threshold.toFixed(1)})`
                  : `${activeAttack.packets_blocked} Blocked`}
              </p>
              <span className="text-[10px] text-slate-400 font-mono">
                {activeAttack.euclidean_distance > activeAttack.distance_threshold ? 'Threshold Exceeded' : 'Filtered'}
              </span>
            </div>

            {/* Model AUC Protection */}
            <div className="p-2.5 rounded-xl bg-black/30 border border-white/5 flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between gap-1">
                  <span className="text-[10px] text-[var(--color-text-muted)] uppercase font-semibold flex items-center gap-1">
                    <ShieldAlert size={11} className="text-indigo-400" /> Model Accuracy
                  </span>
                  <span className="text-[9px] font-mono uppercase px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-semibold">
                    Simulated
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-xs font-bold font-mono text-emerald-400">
                    {activeAttack.auc_protected.toFixed(4)}
                  </span>
                  <span className="text-[10px] font-mono text-rose-400 line-through">
                    {activeAttack.auc_compromised_baseline.toFixed(4)}
                  </span>
                </div>
                <span className="text-[10px] text-emerald-400 font-semibold block">
                  +{(activeAttack.auc_protected - activeAttack.auc_compromised_baseline).toFixed(2)} AUC Preserved
                </span>
              </div>
              <span className="text-[9px] text-[var(--color-text-muted)] mt-1.5 leading-tight block border-t border-white/5 pt-1">
                * Live continuous proxy from cosine alignment (not holdout validation AUC).
              </span>
            </div>
          </div>

          {/* Audit Log ticker */}
          <div className="mt-2.5 px-3 py-2 rounded-lg bg-black/40 border border-white/5 text-[11px] font-mono text-slate-300 flex items-center gap-2">
            <span className="text-rose-400 font-bold shrink-0">AUDIT:</span>
            <span className="truncate">{activeAttack.log_entry}</span>
          </div>

          {/* Simulation Disclaimer Banner */}
          <div className="mt-2 px-3 py-1.5 rounded-lg bg-indigo-950/20 border border-indigo-500/20 text-[10px] text-indigo-300/80 flex items-center gap-2">
            <span className="font-bold text-indigo-400 shrink-0 uppercase tracking-wider text-[9px] px-1.5 py-0.5 rounded bg-indigo-500/20 border border-indigo-500/30">
              Demo Notice
            </span>
            <span>
              AUC metrics in this chaos sandbox are continuous live demo indicators modeled from gradient cosine similarity and boundary strain, demonstrating real-time defense resilience rather than offline holdout dataset evaluations.
            </span>
          </div>
        </div>
      )}

      {/* Hidden intensity state controller for fine-tuning */}
      <div className="sr-only">
        <label htmlFor={intensityInputId}>Attack Burst Intensity</label>
        <input
          id={intensityInputId}
          type="range"
          min="100"
          max="2000"
          step="100"
          value={intensity}
          onChange={(e) => setIntensity(Number(e.target.value))}
        />
      </div>
    </div>
  );
}
