/**
 * DatasetTrainingConfigPanel.tsx
 *
 * Inline collapsible panel rendered inside LiveOperationsView that lets the
 * user:
 *   1. Choose one of 4 benchmark datasets (PaySim, IEEE-CIS, Elliptic, Credit Card)
 *   2. Toggle between "Mock Simulation" and "Real Backend" training modes
 *
 * On "Launch Training" the parent receives (profile, mode) and starts the
 * appropriate training path.  This component carries NO training state — it is
 * purely presentational configuration UI.
 */

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ExternalLink, Zap, FlaskConical, ChevronRight, CheckCircle2 } from 'lucide-react';
import { DATASET_PROFILES, type DatasetProfile } from '../utils/datasetProfiles';

export type TrainingMode = 'mock' | 'real';

interface DatasetTrainingConfigPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onLaunch: (profile: DatasetProfile, mode: TrainingMode) => void;
  initialDataset?: DatasetProfile['id'];
  initialMode?: TrainingMode;
}

// ── small helper: format numbers with K/M suffix ──────────────────────────
function fmtCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toString();
}

function fmtFraud(r: number): string {
  if (r < 0.001) return `${(r * 100).toFixed(3)}%`;
  return `${(r * 100).toFixed(2)}%`;
}

const DATASET_ORDER: DatasetProfile['id'][] = ['paysim', 'ieee_cis', 'elliptic', 'creditcard'];

export default function DatasetTrainingConfigPanel({
  isOpen,
  onClose,
  onLaunch,
  initialDataset = 'paysim',
  initialMode = 'mock',
}: DatasetTrainingConfigPanelProps) {
  const [selectedId, setSelectedId] = useState<DatasetProfile['id']>(initialDataset);
  const [mode, setMode] = useState<TrainingMode>(initialMode);

  const selectedProfile = DATASET_PROFILES[selectedId];

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          key="config-panel"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.3, ease: 'easeInOut' }}
          className="overflow-hidden"
        >
          <div className="glass-card p-4 sm:p-6 border border-[var(--color-border)] rounded-2xl space-y-6">
            {/* ── Header ──────────────────────────────────────────────────── */}
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)]">
                  Configure Training Session
                </h3>
                <p className="text-xs text-[var(--color-text-muted)]">
                  Select a benchmark dataset and training mode before launching
                </p>
              </div>
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg hover:bg-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors shrink-0"
                aria-label="Close config panel"
              >
                ✕
              </button>
            </div>

            {/* ── Mode Toggle ─────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                Training Mode
              </p>
              <div className="grid grid-cols-2 gap-3">
                {/* Mock Simulation */}
                <button
                  onClick={() => setMode('mock')}
                  className={`relative flex flex-col items-start gap-1.5 p-3.5 rounded-xl border-2 transition-all text-left ${
                    mode === 'mock'
                      ? 'border-[var(--color-accent-indigo)] bg-[var(--color-accent-indigo)]/10'
                      : 'border-[var(--color-border)] hover:border-[var(--color-border-hover)] bg-transparent'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <FlaskConical
                      size={16}
                      className={mode === 'mock' ? 'text-[var(--color-accent-indigo)]' : 'text-[var(--color-text-muted)]'}
                    />
                    <span
                      className={`text-sm font-bold ${
                        mode === 'mock' ? 'text-[var(--color-accent-indigo)]' : 'text-[var(--color-text-secondary)]'
                      }`}
                    >
                      Mock Simulation
                    </span>
                    {mode === 'mock' && (
                      <CheckCircle2 size={14} className="text-[var(--color-accent-indigo)] ml-auto shrink-0" />
                    )}
                  </div>
                  <p className="text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                    Controlled, reproducible FL simulation with dataset-specific AUC & loss convergence profiles
                  </p>
                </button>

                {/* Real Backend */}
                <button
                  onClick={() => setMode('real')}
                  className={`relative flex flex-col items-start gap-1.5 p-3.5 rounded-xl border-2 transition-all text-left ${
                    mode === 'real'
                      ? 'border-amber-500 bg-amber-500/10'
                      : 'border-[var(--color-border)] hover:border-[var(--color-border-hover)] bg-transparent'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Zap
                      size={16}
                      className={mode === 'real' ? 'text-amber-400' : 'text-[var(--color-text-muted)]'}
                    />
                    <span
                      className={`text-sm font-bold ${
                        mode === 'real' ? 'text-amber-400' : 'text-[var(--color-text-secondary)]'
                      }`}
                    >
                      Real Backend
                    </span>
                    {mode === 'real' && (
                      <CheckCircle2 size={14} className="text-amber-400 ml-auto shrink-0" />
                    )}
                  </div>
                  <p className="text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                    Live WebSocket connection to FastAPI FL engine — real training rounds, live metrics
                  </p>
                </button>
              </div>
            </div>

            {/* ── Dataset Cards ────────────────────────────────────────────── */}
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                Benchmark Dataset
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {DATASET_ORDER.map((dsId) => {
                  const p = DATASET_PROFILES[dsId];
                  const isSelected = selectedId === dsId;
                  return (
                    <button
                      key={dsId}
                      onClick={() => setSelectedId(dsId)}
                      className={`flex flex-col gap-2 p-3.5 rounded-xl border-2 transition-all text-left ${
                        isSelected
                          ? 'bg-slate-800/60'
                          : 'border-[var(--color-border)] hover:border-[var(--color-border-hover)] bg-transparent'
                      }`}
                      style={isSelected ? { borderColor: p.color, backgroundColor: `${p.color}14` } : undefined}
                    >
                      {/* Dataset title row */}
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-lg shrink-0">{p.icon}</span>
                          <div className="min-w-0">
                            <p
                              className="text-sm font-bold truncate"
                              style={isSelected ? { color: p.color } : { color: 'var(--color-text-primary)' }}
                            >
                              {p.label}
                            </p>
                            <p className="text-[10px] text-[var(--color-text-muted)] truncate leading-snug">
                              {p.badge}
                            </p>
                          </div>
                        </div>
                        {isSelected && (
                          <CheckCircle2
                            size={16}
                            className="shrink-0 mt-0.5"
                            style={{ color: p.color }}
                          />
                        )}
                      </div>

                      {/* Stats row */}
                      <div className="grid grid-cols-3 gap-1">
                        <div className="bg-black/20 rounded-lg p-1.5 text-center">
                          <p className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wide leading-none mb-0.5">
                            Samples
                          </p>
                          <p className="text-xs font-bold font-mono text-[var(--color-text-secondary)]">
                            {fmtCount(p.totalSamples)}
                          </p>
                        </div>
                        <div className="bg-black/20 rounded-lg p-1.5 text-center">
                          <p className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wide leading-none mb-0.5">
                            Fraud%
                          </p>
                          <p className="text-xs font-bold font-mono text-rose-400">
                            {fmtFraud(p.fraudRatio)}
                          </p>
                        </div>
                        <div className="bg-black/20 rounded-lg p-1.5 text-center">
                          <p className="text-[9px] text-[var(--color-text-muted)] uppercase tracking-wide leading-none mb-0.5">
                            Features
                          </p>
                          <p className="text-xs font-bold font-mono text-[var(--color-text-secondary)]">
                            {p.numFeatures}
                          </p>
                        </div>
                      </div>

                      {/* Fraud pattern */}
                      <p className="text-[10px] text-[var(--color-text-muted)] leading-snug line-clamp-2">
                        {p.fraudPattern}
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* ── Selected dataset detail strip ────────────────────────────── */}
            <div
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl border"
              style={{
                borderColor: `${selectedProfile.color}40`,
                backgroundColor: `${selectedProfile.color}0A`,
              }}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <span className="text-2xl shrink-0">{selectedProfile.icon}</span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[var(--color-text-primary)] truncate">
                    {selectedProfile.label}
                  </p>
                  <p className="text-xs text-[var(--color-text-muted)] truncate">
                    {selectedProfile.subtitle}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {mode === 'mock' && (
                  <span className="text-[10px] text-[var(--color-text-muted)] whitespace-nowrap">
                    AUC {selectedProfile.initialAuc.toFixed(3)} → {selectedProfile.targetAuc.toFixed(3)}
                  </span>
                )}
                <a
                  href={selectedProfile.sourceLink}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors underline-offset-2 hover:underline"
                >
                  Source <ExternalLink size={10} />
                </a>
              </div>
            </div>

            {/* ── Launch button ────────────────────────────────────────────── */}
            <div className="flex items-center justify-between gap-3 pt-1">
              <button
                onClick={onClose}
                className="px-4 py-2 rounded-xl text-sm font-medium text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors border border-[var(--color-border)] hover:border-[var(--color-border-hover)]"
              >
                Cancel
              </button>
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.97 }}
                onClick={() => onLaunch(selectedProfile, mode)}
                className="flex items-center gap-2 px-6 py-2.5 rounded-xl font-bold text-sm text-white shadow-lg transition-all"
                style={{
                  background: `linear-gradient(135deg, ${selectedProfile.color}, #6366f1)`,
                  boxShadow: `0 4px 20px ${selectedProfile.color}40`,
                }}
              >
                {mode === 'mock' ? <FlaskConical size={15} /> : <Zap size={15} />}
                Launch{' '}
                {mode === 'mock' ? 'Simulation' : 'Real Training'}
                <ChevronRight size={15} />
              </motion.button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
