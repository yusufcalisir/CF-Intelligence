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
      className="glass-card p-6 h-full flex flex-col"
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
              className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
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
              className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
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
              className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
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
                className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
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

            {/* Failure Simulation */}
            <div className={config.fl_engine_type === 'flower' ? 'opacity-40 pointer-events-none' : ''}>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Failure Simulation {config.fl_engine_type === 'flower' && <span className="text-[var(--color-accent-amber)]">(Flower N/A)</span>}
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.fl_engine_type === 'flower' ? false : config.enable_dropout_simulation}
                    onChange={(e) => updateConfig('enable_dropout_simulation', e.target.checked)}
                    disabled={config.fl_engine_type === 'flower'}
                    className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                  />
                  <span className="text-xs text-[var(--color-text-secondary)]">Client Dropout</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.fl_engine_type === 'flower' ? false : config.enable_latency_simulation}
                    onChange={(e) => updateConfig('enable_latency_simulation', e.target.checked)}
                    disabled={config.fl_engine_type === 'flower'}
                    className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                  />
                  <span className="text-xs text-[var(--color-text-secondary)]">Network Latency</span>
                </label>
              </div>
              {config.enable_dropout_simulation && (
                <div className="mt-2">
                  <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                    Dropout Probability: {((config.dropout_probability ?? 0.2) * 100).toFixed(0)}%
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={80}
                    value={(config.dropout_probability ?? 0.2) * 100}
                    onChange={(e) => updateConfig('dropout_probability', parseInt(e.target.value) / 100)}
                    className="w-full accent-[var(--color-accent-indigo)]"
                  />
                </div>
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
                className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
              >
                <option value="none">None</option>
                <option value="differential_privacy">Differential Privacy</option>
                <option value="secure_aggregation">Secure Aggregation</option>
                <option value="both">Both</option>
              </select>
              {(config.privacy_mechanism === 'differential_privacy' || config.privacy_mechanism === 'both') && (
                <div className="mt-2">
                  <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                    ε (Epsilon): {config.dp_epsilon}
                  </label>
                  <input
                    type="range"
                    min={0.1}
                    max={10}
                    step={0.1}
                    value={config.dp_epsilon}
                    onChange={(e) => updateConfig('dp_epsilon', parseFloat(e.target.value))}
                    className="w-full accent-[var(--color-accent-indigo)]"
                  />
                  <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                    Lower ε = stronger privacy, more noise, lower utility
                  </p>
                </div>
              )}
              {(config.privacy_mechanism === 'differential_privacy' || config.privacy_mechanism === 'both') && (
                <div className="mt-3">
                  <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                    DP Implementation
                  </label>
                  <select
                    value={config.dp_mode}
                    onChange={(e) => updateConfig('dp_mode', e.target.value as SimulationConfig['dp_mode'])}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
                  >
                    <option value="post_hoc">Post-Hoc (Clip + Noise after training)</option>
                    <option value="opacus">Opacus (Per-Sample Gradient Privacy)</option>
                  </select>
                  <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                    Opacus uses Meta AI's library for industry-standard per-sample gradient clipping
                  </p>
                </div>
              )}
            </div>
            {/* Aggregation Strategy */}
            <div className={config.fl_engine_type === 'flower' ? 'opacity-60' : ''}>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Aggregation Strategy {config.fl_engine_type === 'flower' && <span className="text-[var(--color-accent-amber)]">(FedAvg only)</span>}
              </h4>
              <select
                value={config.fl_engine_type === 'flower' ? 'fed_avg_weighted' : config.aggregation_method}
                onChange={(e) => updateConfig('aggregation_method', e.target.value as SimulationConfig['aggregation_method'])}
                disabled={config.fl_engine_type === 'flower'}
                className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
              >
                <optgroup label="Classic">
                  <option value="fed_avg_weighted">FedAvg Weighted (Default)</option>
                  <option value="fed_avg">FedAvg (Unweighted)</option>
                </optgroup>
                <optgroup label="Adaptive Server Optimizers ✨">
                  <option value="fed_adam" disabled={config.fl_engine_type === 'flower'}>FedAdam (Server Adam)</option>
                  <option value="fed_adagrad" disabled={config.fl_engine_type === 'flower'}>FedAdagrad (Server AdaGrad)</option>
                  <option value="fed_yogi" disabled={config.fl_engine_type === 'flower'}>FedYogi (Slow variance decay) ✨</option>
                </optgroup>
                <optgroup label="Client-Drift Correction ✨">
                  <option value="scaffold" disabled={config.fl_engine_type === 'flower'}>SCAFFOLD (Control variates) ✨</option>
                </optgroup>
                <optgroup label="Byzantine-Robust">
                  <option value="krum" disabled={config.fl_engine_type === 'flower'}>Krum</option>
                  <option value="coordinate_wise_median" disabled={config.fl_engine_type === 'flower'}>Coordinate-wise Median</option>
                  <option value="trimmed_mean" disabled={config.fl_engine_type === 'flower'}>Trimmed Mean</option>
                  <option value="bulyan" disabled={config.fl_engine_type === 'flower'}>Bulyan (Multi-Byzantine Robust)</option>
                </optgroup>
              </select>
              <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                {config.fl_engine_type === 'flower' ? 'Flower uses its built-in FedAvg implementation' : 'FedYogi & SCAFFOLD are new in v20 — adaptive convergence & drift correction'}
              </p>
            </div>


            {/* Adversarial Simulation */}
            <div className={config.fl_engine_type === 'flower' ? 'opacity-40 pointer-events-none' : ''}>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Adversarial Simulation {config.fl_engine_type === 'flower' && <span className="text-[var(--color-accent-amber)]">(Flower N/A)</span>}
              </h4>
              <label className="flex items-center gap-2 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={config.fl_engine_type === 'flower' ? false : config.enable_poisoning_simulation}
                  onChange={(e) => updateConfig('enable_poisoning_simulation', e.target.checked)}
                  disabled={config.fl_engine_type === 'flower'}
                  className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-status-error)] focus:ring-[var(--color-status-error)]"
                />
                <span className="text-xs text-[var(--color-text-secondary)]">Enable Model Poisoning</span>
              </label>
              {config.enable_poisoning_simulation && (
                <div className="space-y-2 mt-2">
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">Malicious Bank</label>
                    <select
                      value={config.poisoning_bank_id}
                      onChange={(e) => updateConfig('poisoning_bank_id', e.target.value)}
                      className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-status-error)] transition-colors"
                    >
                      <option value="bank_a">Bank A — National Trust</option>
                      <option value="bank_b">Bank B — Metro Commercial</option>
                      <option value="bank_c">Bank C — Heritage Regional</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                      Poisoning Scale: {config.poisoning_scale}x
                    </label>
                    <input
                      type="range"
                      min={1}
                      max={20}
                      step={0.5}
                      value={config.poisoning_scale}
                      onChange={(e) => updateConfig('poisoning_scale', parseFloat(e.target.value))}
                      className="w-full accent-[var(--color-status-error)]"
                    />
                    <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                      Higher scale = more aggressive attack noise injected into model weights
                    </p>
                  </div>
                </div>
              )}
            </div>

            {/* Active Defense & Adversarial ML Training */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Active Defense & Adversarial Training
              </h4>
              <label className="flex items-center gap-2 cursor-pointer mb-2">
                <input
                  type="checkbox"
                  checked={config.enable_adversarial_training || false}
                  onChange={(e) => updateConfig('enable_adversarial_training', e.target.checked)}
                  className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                />
                <span className="text-xs text-[var(--color-text-secondary)]">Enable Adversarial Evasion Hardening</span>
              </label>
              {config.enable_adversarial_training && (
                <div className="space-y-3 mt-2 pl-2 border-l-2 border-cyan-500/40">
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">Attack Algorithm</label>
                    <select
                      value={config.adversarial_attack_type || 'fgsm'}
                      onChange={(e) => updateConfig('adversarial_attack_type', e.target.value)}
                      className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-cyan-500 transition-colors"
                    >
                      <option value="fgsm">FGSM (Fast Gradient Sign Method — 1 step)</option>
                      <option value="pgd">PGD (Projected Gradient Descent — 5 steps)</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                      Perturbation Noise (ε): {config.adversarial_epsilon ?? 0.05}
                    </label>
                    <input
                      type="range"
                      min={0.01}
                      max={0.25}
                      step={0.01}
                      value={config.adversarial_epsilon ?? 0.05}
                      onChange={(e) => updateConfig('adversarial_epsilon', parseFloat(e.target.value))}
                      className="w-full accent-cyan-500"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Regulatory Fairness & Bias Mitigation */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Regulatory AI Compliance & Fairness
              </h4>
              <div className="space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.enable_bias_mitigation || false}
                    onChange={(e) => updateConfig('enable_bias_mitigation', e.target.checked)}
                    className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                  />
                  <span className="text-xs text-[var(--color-text-secondary)]">Enable Bias Mitigation (Covariance Penalty)</span>
                </label>
                {config.enable_bias_mitigation && (
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                      Fairness Regularization Weight (&lambda;): {config.fairness_lambda ?? 0.5}
                    </label>
                    <input
                      type="range"
                      min={0.0}
                      max={2.0}
                      step={0.1}
                      value={config.fairness_lambda ?? 0.5}
                      onChange={(e) => updateConfig('fairness_lambda', parseFloat(e.target.value))}
                      className="w-full accent-[var(--color-accent-indigo)]"
                    />
                    <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                      Higher weight pushes model parameters to have zero covariance with sensitive attributes (nationality/region).
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Hardware & Cryptographic Isolation */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Hardware & Cryptographic Isolation
              </h4>
              <div>
                <label className="block text-xs text-[var(--color-text-muted)] mb-1">Isolation Mode</label>
                <select
                  value={config.hardware_isolation_mode || 'none'}
                  onChange={(e) => updateConfig('hardware_isolation_mode', e.target.value as any)}
                  className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
                >
                  <option value="none">None (Plaintext computation)</option>
                  <option value="tee">Trusted Execution Environment (TEE - Intel SGX / Nitro)</option>
                  <option value="fhe">Fully Homomorphic Encryption (FHE - CKKS)</option>
                </select>
                <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                  TEE runs secure summation inside isolated enclaves; FHE uses encrypted parameter addition.
                </p>
              </div>
            </div>

            {/* Real-Time Streaming GNN Settings */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Graph Neural Network Dynamics
              </h4>
              <div className="space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.enable_streaming_gnn || false}
                    onChange={(e) => updateConfig('enable_streaming_gnn', e.target.checked)}
                    className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                  />
                  <span className="text-xs text-[var(--color-text-secondary)]">Enable Streaming GNN (GraphSAGE/GAT)</span>
                </label>
                <p className="text-[10px] text-[var(--color-text-muted)]">
                  Enables online self-supervised training on transaction graph updates as payments stream in.
                </p>
              </div>
            </div>

            {/* Web3 & CBDC Smart Contract Incentive Settlement */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Web3 & CBDC Smart Contract Settlement
              </h4>
              <div className="space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.enable_web3_settlement || false}
                    onChange={(e) => updateConfig('enable_web3_settlement', e.target.checked)}
                    className="rounded border-[var(--color-border)] bg-[var(--color-bg-elevated)] text-[var(--color-accent-indigo)] focus:ring-[var(--color-accent-indigo)]"
                  />
                  <span className="text-xs text-[var(--color-text-secondary)]">Enable Automated On-Chain Settlement</span>
                </label>
                {config.enable_web3_settlement && (
                  <div>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">Settlement Asset / Token</label>
                    <select
                      value={config.settlement_currency || 'wCBDC'}
                      onChange={(e) => updateConfig('settlement_currency', e.target.value)}
                      className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
                    >
                      <option value="wCBDC">Wholesale CBDC (Central Bank Digital Currency)</option>
                      <option value="USDC">USDC (Fiat-Backed Stablecoin)</option>
                      <option value="e-TRY">Digital Lira (e-TRY CBDC Testnet)</option>
                    </select>
                    <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                      Disburses token payouts automatically to consortium bank wallets upon simulation completion based on LOO Shapley scores.
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Data Volume */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider">
                Data Volume
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {(['bank_a_transactions', 'bank_b_transactions', 'bank_c_transactions'] as const).map((key, i) => (
                  <div key={key}>
                    <label className="block text-xs text-[var(--color-text-muted)] mb-1">
                      Bank {String.fromCharCode(65 + i)}
                    </label>
                    <input
                      type="number"
                      value={config[key]}
                      onChange={(e) => updateConfig(key, parseInt(e.target.value) || 10000)}
                      min={1000}
                      max={200000}
                      step={1000}
                      className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] font-mono focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
                    />
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </div>

      {/* Start Button */}
      <button
        onClick={handleStart}
        disabled={createMutation.isPending}
        className="mt-auto w-full py-2.5 rounded-lg font-medium text-sm text-white transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed"
        style={{
          background: 'linear-gradient(135deg, var(--color-accent-indigo), var(--color-accent-teal))',
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
        <p className="mt-2 text-xs text-[var(--color-status-error)]">
          Failed to start simulation. Is the backend running?
        </p>
      )}
    </motion.div>
  );
}
