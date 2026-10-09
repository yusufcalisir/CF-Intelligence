import { useState, useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import {
  useCreateSimulation,
  useBenchmarkDatasetsStatus,
  useDownloadBenchmarkDataset,
  useBenchmarkDownloadStatus,
} from '../../api/queries';
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
  const { data: benchmarkStatus } = useBenchmarkDatasetsStatus();
  const isSubmittingRef = useRef(false);

  const selectedDataset = config.dataset;
  const isBenchmark = selectedDataset && selectedDataset !== 'synthetic';
  const hasRealFiles = isBenchmark ? Boolean(benchmarkStatus?.[selectedDataset]?.has_real_files) : false;
  const effectiveMode = config.dataset_mode ?? (hasRealFiles ? 'real' : 'synthetic');
  const serverHasKaggleCreds = Boolean(
    benchmarkStatus?._meta?.kaggle_configured ?? (selectedDataset ? benchmarkStatus?.[selectedDataset]?.kaggle_configured : false)
  );

  // Downloader state for Hugging Face Spaces & container environments
  const [showDownloadPanel, setShowDownloadPanel] = useState(false);
  const [downloadSource, setDownloadSource] = useState<'auto' | 'mirror' | 'kaggle'>('auto');
  const [kaggleUsername, setKaggleUsername] = useState(() => {
    return typeof localStorage !== 'undefined' ? localStorage.getItem('cfi_kaggle_username') || '' : '';
  });
  const [kaggleKey, setKaggleKey] = useState(() => {
    return typeof localStorage !== 'undefined' ? localStorage.getItem('cfi_kaggle_key') || '' : '';
  });

  const downloadMutation = useDownloadBenchmarkDataset();
  const { data: downloadStatus } = useBenchmarkDownloadStatus(
    isBenchmark ? selectedDataset : undefined,
    Boolean(isBenchmark && !hasRealFiles)
  );

  const isDownloading = downloadStatus?.status === 'in_progress' || downloadMutation.isPending;

  useEffect(() => {
    if (downloadStatus?.status === 'completed' && isBenchmark) {
      setConfig((prev) => ({ ...prev, dataset_mode: 'real' }));
      setShowDownloadPanel(false);
    }
  }, [downloadStatus?.status, isBenchmark]);

  const handleStartDownload = () => {
    if (!selectedDataset || selectedDataset === 'synthetic') return;
    if (typeof localStorage !== 'undefined') {
      if (kaggleUsername) localStorage.setItem('cfi_kaggle_username', kaggleUsername);
      if (kaggleKey) localStorage.setItem('cfi_kaggle_key', kaggleKey);
    }
    downloadMutation.mutate({
      dataset: selectedDataset,
      kaggle_username: kaggleUsername || undefined,
      kaggle_key: kaggleKey || undefined,
      source: downloadSource,
    });
  };

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleResize = () => {
      setIsLargeMonitor(window.innerWidth >= 1600 && window.innerHeight >= 900);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const handleStart = () => {
    if (createMutation.isPending || isSubmittingRef.current) return;
    isSubmittingRef.current = true;
    const clientOpId = 'sim_op_' + (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).substring(2));
    createMutation.mutate({
      ...config,
      ...(isBenchmark ? { dataset_mode: effectiveMode } : {}),
      clientOperationId: clientOpId,
      idempotencyKey: clientOpId,
    }, {
      onSuccess: (data) => {
        onSimulationCreated(data.id);
      },
      onSettled: () => {
        isSubmittingRef.current = false;
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
            {/* Benchmark Dataset Selection */}
            <div>
              <h4 className="text-xs font-medium text-[var(--color-text-secondary)] mb-3 uppercase tracking-wider flex items-center justify-between">
                <span>Benchmark Dataset</span>
                <span className={`text-[10px] font-mono ${effectiveMode === 'real' ? 'text-[var(--color-accent-emerald)]' : 'text-[var(--color-accent-indigo)]'}`}>
                  {isBenchmark
                    ? effectiveMode === 'real'
                      ? '✓ Real Kaggle Dataset'
                      : '⚡ Synthetic Fixture'
                    : 'Synthetic Generator'}
                </span>
              </h4>
              <select
                value={config.dataset ?? 'synthetic'}
                onChange={(e) => {
                  const newDs = e.target.value as SimulationConfig['dataset'];
                  const realAvailable = newDs && newDs !== 'synthetic' ? Boolean(benchmarkStatus?.[newDs]?.has_real_files) : false;
                  setConfig((prev) => ({
                    ...prev,
                    dataset: newDs,
                    dataset_mode: newDs && newDs !== 'synthetic' ? (realAvailable ? 'real' : 'synthetic') : undefined,
                  }));
                }}
                className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-indigo)] transition-colors"
              >
                <option value="synthetic">Synthetic Multi-Bank Generator (10 features)</option>
                <option value="paysim">PaySim Mobile Money (6.36M txns, 13 features)</option>
                <option value="ieee_cis">IEEE-CIS Fraud Detection (590K txns, 378 features)</option>
                <option value="elliptic">Elliptic Bitcoin AML Graph (203K txns, 166 features)</option>
                <option value="creditcard">European Credit Card Fraud (284K txns, 29 features)</option>
              </select>
              {isBenchmark && (
                <div className="mt-2 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-[var(--color-text-secondary)]">Dataset Source Mode:</span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => updateConfig('dataset_mode', 'real')}
                        className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                          effectiveMode === 'real'
                            ? 'bg-[var(--color-accent-emerald)]/10 text-[var(--color-accent-emerald)] border-[var(--color-accent-emerald)]/40 font-medium'
                            : 'bg-transparent text-[var(--color-text-muted)] border-[var(--color-border)] hover:text-[var(--color-text-primary)]'
                        }`}
                      >
                        Real Kaggle Files {hasRealFiles ? '✓' : ''}
                      </button>
                      <button
                        type="button"
                        onClick={() => updateConfig('dataset_mode', 'synthetic')}
                        className={`px-2 py-0.5 text-xs rounded border transition-colors ${
                          effectiveMode === 'synthetic'
                            ? 'bg-[var(--color-accent-indigo)]/10 text-[var(--color-accent-indigo)] border-[var(--color-accent-indigo)]/40 font-medium'
                            : 'bg-transparent text-[var(--color-text-muted)] border-[var(--color-border)] hover:text-[var(--color-text-primary)]'
                        }`}
                      >
                        Synthetic Fixture
                      </button>
                    </div>
                  </div>
                  <p className={`text-[10px] mt-1 ${effectiveMode === 'real' ? (hasRealFiles ? 'text-[var(--color-accent-emerald)]' : 'text-amber-400') : 'text-[var(--color-accent-indigo)]'}`}>
                    {effectiveMode === 'real'
                      ? hasRealFiles
                        ? '✓ Real Kaggle benchmark dataset files verified on disk. Partitioned Non-IID across banks with dynamic PyTorch model sizing.'
                        : '⚠️ Real files not detected in storage. Training in real mode will fail closed unless physical CSV/Parquet files are mounted or downloaded.'
                      : '⚡ High-fidelity synthetic benchmark fixture preserving exact dataset schema and feature columns for fast offline testing.'}
                  </p>

                  {/* Benchmark Downloader Card for Hugging Face Spaces & Cloud/Container environments */}
                  {selectedDataset && !hasRealFiles && (
                    <div className="mt-2.5 p-2.5 rounded-lg border border-amber-500/30 bg-amber-500/5 space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5 text-xs text-amber-400 font-medium">
                          <span>⚠️ Real files missing on server</span>
                        </div>
                        {!isDownloading && (
                          <button
                            type="button"
                            onClick={() => setShowDownloadPanel((prev) => !prev)}
                            className="px-2.5 py-1 text-xs font-medium rounded bg-[var(--color-accent-indigo)] text-white hover:opacity-90 transition-opacity flex items-center gap-1 shadow-sm"
                          >
                            <span>📥</span>
                            <span>{showDownloadPanel ? 'Close Downloader' : 'Download to Server'}</span>
                          </button>
                        )}
                      </div>

                      {/* Live Progress Bar when downloading */}
                      {isDownloading && (
                        <div className="space-y-1.5 pt-1">
                          <div className="flex justify-between text-[11px] text-[var(--color-text-secondary)]">
                            <span className="font-medium text-[var(--color-accent-indigo)]">
                              {downloadStatus?.message || 'Downloading dataset files...'}
                            </span>
                            <span className="font-mono font-bold text-[var(--color-text-primary)]">
                              {downloadStatus?.percent ?? 0}%
                            </span>
                          </div>
                          <div className="w-full bg-[var(--color-bg-elevated)] h-2 rounded-full overflow-hidden border border-[var(--color-border)]">
                            <div
                              className="bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-emerald)] h-full transition-all duration-300 rounded-full"
                              style={{ width: `${Math.max(5, downloadStatus?.percent ?? 0)}%` }}
                            />
                          </div>
                          <p className="text-[10px] text-[var(--color-text-muted)] italic">
                            Files are saved directly to server storage (/app/storage/datasets). Once complete, real federated training is unlocked permanently.
                          </p>
                        </div>
                      )}

                      {/* Failure Alert */}
                      {downloadStatus?.status === 'failed' && (
                        <div className="p-2 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs space-y-1">
                          <p className="font-medium">Download Error:</p>
                          <p className="text-[11px] font-mono break-all">{downloadStatus.error || downloadStatus.message}</p>
                        </div>
                      )}

                      {/* Config Panel for Download */}
                      {showDownloadPanel && !isDownloading && (
                        <div className="pt-2 border-t border-[var(--color-border-subtle)] space-y-2.5">
                          <div className="text-[11px] text-[var(--color-text-secondary)]">
                            Source strategy for <span className="font-semibold text-[var(--color-text-primary)]">{selectedDataset.toUpperCase()}</span>:
                          </div>
                          <div className="flex flex-wrap gap-3 text-xs">
                            <label className="flex items-center gap-1.5 cursor-pointer">
                              <input
                                type="radio"
                                name="downloadSource"
                                value="auto"
                                checked={downloadSource === 'auto'}
                                onChange={() => setDownloadSource('auto')}
                                className="text-[var(--color-accent-indigo)]"
                              />
                              <span>Auto (Mirror / Kaggle)</span>
                            </label>
                            {(selectedDataset === 'creditcard' || selectedDataset === 'elliptic') && (
                              <label className="flex items-center gap-1.5 cursor-pointer">
                                <input
                                  type="radio"
                                  name="downloadSource"
                                  value="mirror"
                                  checked={downloadSource === 'mirror'}
                                  onChange={() => setDownloadSource('mirror')}
                                  className="text-[var(--color-accent-indigo)]"
                                />
                                <span>Public Mirror (1-Click, No API key)</span>
                              </label>
                            )}
                            <label className="flex items-center gap-1.5 cursor-pointer">
                              <input
                                type="radio"
                                name="downloadSource"
                                value="kaggle"
                                checked={downloadSource === 'kaggle'}
                                onChange={() => setDownloadSource('kaggle')}
                                className="text-[var(--color-accent-indigo)]"
                              />
                              <span>Kaggle API</span>
                            </label>
                          </div>

                          {serverHasKaggleCreds ? (
                            <div className="p-2 rounded bg-[var(--color-accent-emerald)]/10 border border-[var(--color-accent-emerald)]/30 flex items-center justify-between text-xs text-[var(--color-accent-emerald)]">
                              <span className="flex items-center gap-1.5 font-medium text-[11px]">
                                <span>🔒</span>
                                <span>Hugging Face Kaggle Secrets detected. Server ready for 1-click download!</span>
                              </span>
                            </div>
                          ) : (
                            (downloadSource === 'kaggle' || selectedDataset === 'paysim' || selectedDataset === 'ieee_cis') && (
                              <div className="space-y-1.5 p-2 rounded bg-[var(--color-bg-elevated)] border border-[var(--color-border)]">
                                <div className="flex justify-between items-center text-[10px] text-[var(--color-text-muted)]">
                                  <span>Optional if set in HF Secrets (KAGGLE_USERNAME / KAGGLE_KEY)</span>
                                  <a
                                    href="https://www.kaggle.com/settings"
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-[var(--color-accent-indigo)] hover:underline"
                                  >
                                    Get Token ↗
                                  </a>
                                </div>
                                <div className="grid grid-cols-2 gap-2">
                                  <input
                                    type="text"
                                    placeholder="Kaggle Username"
                                    value={kaggleUsername}
                                    onChange={(e) => setKaggleUsername(e.target.value)}
                                    className="px-2 py-1 text-xs bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded text-[var(--color-text-primary)]"
                                  />
                                  <input
                                    type="password"
                                    placeholder="Kaggle API Key"
                                    value={kaggleKey}
                                    onChange={(e) => setKaggleKey(e.target.value)}
                                    className="px-2 py-1 text-xs bg-[var(--color-bg-base)] border border-[var(--color-border)] rounded text-[var(--color-text-primary)]"
                                  />
                                </div>
                              </div>
                            )
                          )}

                          <div className="flex justify-end gap-2 pt-1">
                            <button
                              type="button"
                              onClick={() => setShowDownloadPanel(false)}
                              className="px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              onClick={handleStartDownload}
                              disabled={downloadMutation.isPending}
                              className="px-3 py-1 text-xs font-medium rounded bg-[var(--color-accent-emerald)] text-black hover:opacity-90 disabled:opacity-50 transition-opacity"
                            >
                              {downloadMutation.isPending ? 'Starting...' : 'Start Download'}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>


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
                  <option value="fed_prox" disabled={config.fl_engine_type === 'flower'}>FedProx (Proximal Regularization) ✨</option>
                </optgroup>
                <optgroup label="Byzantine-Robust">
                  <option value="krum" disabled={config.fl_engine_type === 'flower'}>Krum</option>
                  <option value="coordinate_wise_median" disabled={config.fl_engine_type === 'flower'}>Coordinate-wise Median</option>
                  <option value="trimmed_mean" disabled={config.fl_engine_type === 'flower'}>Trimmed Mean</option>
                  <option value="bulyan" disabled={config.fl_engine_type === 'flower'}>Bulyan (Multi-Byzantine Robust)</option>
                </optgroup>
              </select>
              <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                {config.fl_engine_type === 'flower' ? 'Flower uses its built-in FedAvg implementation' : 'FedProx, FedYogi & SCAFFOLD control client drift and adaptive convergence'}
              </p>
              {config.aggregation_method === 'fed_prox' && (
                <div className="mt-3 p-2.5 bg-[var(--color-bg-elevated)] border border-[var(--color-border)] rounded-md">
                  <label className="block text-xs text-[var(--color-text-secondary)] mb-1 font-medium">
                    FedProx Proximal Weight (&mu;): {config.fedprox_mu ?? 0.01}
                  </label>
                  <input
                    type="range"
                    min={0.001}
                    max={0.5}
                    step={0.005}
                    value={config.fedprox_mu ?? 0.01}
                    onChange={(e) => updateConfig('fedprox_mu', parseFloat(e.target.value))}
                    className="w-full accent-[var(--color-accent-indigo)]"
                  />
                  <p className="text-[10px] text-[var(--color-text-muted)] mt-1">
                    Adds &frac12;&mu;||w - w_t||&sup2; proximal penalty to local loss, curbing client drift across Non-IID bank data.
                  </p>
                </div>
              )}
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
        type="button"
        onClick={handleStart}
        disabled={createMutation.isPending || isSubmittingRef.current}
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
