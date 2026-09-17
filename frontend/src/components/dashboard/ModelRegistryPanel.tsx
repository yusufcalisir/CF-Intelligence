import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  useModelVersions,
  useRollbackModel,
  useCanaryHistory,
  useSignOffModel,
  useShadowMetrics,
  useSubmitFeedback,
} from '../../api/queries';

interface ModelRegistryPanelProps {
  simulationId: string;
}

export default function ModelRegistryPanel({ simulationId }: ModelRegistryPanelProps) {
  const { data: versions, isLoading: loadingVersions, refetch: refetchVersions } = useModelVersions(simulationId);
  const { data: canaryHistory, isLoading: loadingCanary } = useCanaryHistory(simulationId);
  const rollbackMutation = useRollbackModel();
  const signoffMutation = useSignOffModel();
  const feedbackMutation = useSubmitFeedback();
  const { data: shadowMetrics, refetch: refetchShadow } = useShadowMetrics(simulationId);

  const [activeTab, setActiveTab] = useState<'registry' | 'canary' | 'shadow'>('registry');
  const [rollbackSuccess, setRollbackSuccess] = useState<string | null>(null);

  // Sign-off Form states
  const [selectedVersionForSignoff, setSelectedVersionForSignoff] = useState<number | null>(null);
  const [signoffRole, setSignoffRole] = useState<'compliance' | 'ml_engineer'>('ml_engineer');
  const [signoffUser, setSignoffUser] = useState('');
  const [signoffSignature, setSignoffSignature] = useState('');
  const [fairness, setFairness] = useState('0.95');
  const [bias, setBias] = useState('0.02');
  const [drift, setDrift] = useState('0.01');

  // Feedback states
  const [feedbackTxId, setFeedbackTxId] = useState('');
  const [feedbackLabel, setFeedbackLabel] = useState('0');

  const activeVersion = versions?.find((v) => v.is_active);

  const handleRollback = async (version: number) => {
    try {
      await rollbackMutation.mutateAsync({ simulationId, version });
      setRollbackSuccess(`Successfully rolled back active global model to Version ${version}!`);
      refetchVersions();
      refetchShadow();
      setTimeout(() => setRollbackSuccess(null), 5000);
    } catch (err: any) {
      console.error('Rollback failed:', err);
    }
  };

  const handleSignOff = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedVersionForSignoff || !signoffUser.trim() || !signoffSignature.trim()) return;
    try {
      await signoffMutation.mutateAsync({
        simulationId,
        version: selectedVersionForSignoff,
        role: signoffRole,
        user: signoffUser,
        signature: signoffSignature,
        fairness_score: parseFloat(fairness),
        bias_metric: parseFloat(bias),
        drift_divergence: parseFloat(drift),
      });
      setSelectedVersionForSignoff(null);
      setSignoffUser('');
      setSignoffSignature('');
      refetchVersions();
      refetchShadow();
    } catch (err) {
      console.error('Signoff failed:', err);
    }
  };

  const handleFeedbackSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!feedbackTxId.trim()) return;
    try {
      await feedbackMutation.mutateAsync({
        simulationId,
        transaction_id: feedbackTxId,
        actual_label: parseInt(feedbackLabel),
      });
      setFeedbackTxId('');
      refetchShadow();
    } catch (err) {
      console.error('Feedback failed:', err);
    }
  };

  const isLoading = loadingVersions || loadingCanary;

  if (isLoading && !versions) {
    return (
      <div className="glass-card p-5 animate-pulse">
        <div className="h-4 w-40 bg-[var(--color-bg-elevated)] rounded mb-4" />
        <div className="h-32 bg-[var(--color-bg-elevated)] rounded" />
      </div>
    );
  }

  return (
    <div className="glass-card p-5 flex flex-col gap-4">
      {/* Header and Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-[var(--color-border-subtle)] pb-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Model Registry & Canary Evaluation
          </h3>
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Track, gate, and rollback collaboratively trained models.
          </p>
        </div>

        <div className="flex bg-[var(--color-bg-elevated)] p-0.5 rounded-lg self-start sm:self-auto">
          <button
            onClick={() => setActiveTab('registry')}
            className={`px-3 py-1 rounded-md text-xs transition-all ${
              activeTab === 'registry'
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text-primary)] shadow-sm'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            Registry & Rollback
          </button>
          <button
            onClick={() => setActiveTab('canary')}
            className={`px-3 py-1 rounded-md text-xs transition-all ${
              activeTab === 'canary'
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text-primary)] shadow-sm'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            Canary Decision Logs
          </button>
          <button
            onClick={() => setActiveTab('shadow')}
            className={`px-3 py-1 rounded-md text-xs transition-all ${
              activeTab === 'shadow'
                ? 'bg-[var(--color-bg-card)] text-[var(--color-text-primary)] shadow-sm'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
            }`}
          >
            ⚖️ Shadow Deployment
          </button>
        </div>
      </div>

      {/* Success Notification */}
      <AnimatePresence>
        {rollbackSuccess && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="p-3 bg-[var(--color-status-success)]/15 border border-[var(--color-status-success)]/40 rounded-lg text-xs text-[var(--color-status-success)] font-medium flex items-center gap-2"
          >
            <span>✓</span> {rollbackSuccess}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Registry / Rollback view */}
      {activeTab === 'registry' && (
        <div className="space-y-4">
          {/* Active Model Summary Card */}
          {activeVersion ? (
            <div className="p-4 bg-gradient-to-tr from-[var(--color-accent-indigo)]/10 to-[var(--color-accent-teal)]/10 rounded-xl border border-[var(--color-accent-teal)]/30 flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold text-[var(--color-text-primary)]">
                    Active Production Model (v{activeVersion.version})
                  </span>
                  <span className="px-1.5 py-0.5 rounded text-[8px] bg-[var(--color-status-success)]/15 text-[var(--color-status-success)] font-bold uppercase tracking-wider animate-pulse">
                    Live
                  </span>
                </div>
                <p className="text-[10px] text-[var(--color-text-muted)]">
                  Created: {new Date(activeVersion.created_at).toLocaleString()}
                </p>
                <p className="text-[9px] font-mono text-[var(--color-text-muted)]">
                  Artifact: {activeVersion.filename}
                </p>
              </div>

              {/* Metrics Grid */}
              <div className="grid grid-cols-3 gap-3 md:gap-6 bg-[var(--color-bg-card)]/40 p-3 rounded-lg border border-[var(--color-border-subtle)] shrink-0">
                <div>
                  <div className="text-[8px] text-[var(--color-text-muted)] uppercase">AUC-ROC</div>
                  <div className="text-xs font-bold font-mono text-[var(--color-accent-teal)]">
                    {activeVersion.metrics.auc_roc.toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="text-[8px] text-[var(--color-text-muted)] uppercase">F1-Score</div>
                  <div className="text-xs font-bold font-mono text-[var(--color-accent-indigo-light)]">
                    {activeVersion.metrics.f1_score.toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="text-[8px] text-[var(--color-text-muted)] uppercase">Loss</div>
                  <div className="text-xs font-bold font-mono text-[var(--color-text-primary)]">
                    {activeVersion.metrics.loss.toFixed(4)}
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-6 border border-dashed border-[var(--color-border-subtle)] rounded-xl text-xs text-[var(--color-text-muted)]">
              No active version has been promoted to the registry yet. Run simulation communication rounds to start versioning.
            </div>
          )}

          {/* Versions Cards List */}
          {versions && versions.length > 0 && (
            <div className="space-y-3">
              {versions.map((ver) => (
                <div
                  key={ver.version}
                  className={`p-4 rounded-xl border border-[var(--color-border-subtle)] hover:bg-[var(--color-surface-alt)]/20 transition-colors flex flex-col md:flex-row justify-between md:items-center gap-4 ${
                    ver.is_active ? 'bg-[var(--color-accent-teal)]/5 border-[var(--color-accent-teal)]/30' : ''
                  }`}
                >
                  <div className="space-y-1.5 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-sm">Version {ver.version}</span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wider ${
                          ver.status === 'champion'
                            ? 'bg-[var(--color-status-success)]/15 text-[var(--color-status-success)]'
                            : ver.status === 'challenger'
                            ? 'bg-blue-500/15 text-blue-400'
                            : 'bg-yellow-500/15 text-yellow-500'
                        }`}
                      >
                        {ver.status || (ver.is_active ? 'champion' : 'inactive')}
                      </span>
                      {ver.is_active && (
                        <span className="px-1 py-0.2 bg-[var(--color-status-success)]/20 text-[var(--color-status-success)] text-[7px] font-extrabold uppercase rounded tracking-wide">
                          Live Active
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-[var(--color-text-muted)] space-y-0.5">
                      <div>Saved: {new Date(ver.created_at).toLocaleString()}</div>
                      <div className="font-mono text-[9px]">
                        Git Commit:{' '}
                        <span className="text-gray-400">
                          {ver.git_commit_hash?.slice(0, 8) || 'unknown'}
                        </span>
                      </div>
                      <div className="font-mono text-[9px]">
                        Dataset SHA:{' '}
                        <span className="text-gray-400">
                          {ver.dataset_hash?.slice(0, 8) || 'unknown'}
                        </span>
                      </div>
                      <div>
                        DP Profile:{' '}
                        <span className="text-[var(--color-accent-teal)] font-mono font-semibold">
                          {ver.dp_noise_profile?.mechanism} (ε=
                          {ver.dp_noise_profile?.epsilon || 0}, δ=
                          {ver.dp_noise_profile?.delta || 0})
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Metrics */}
                  <div className="flex gap-4 font-mono text-[10px] bg-[var(--color-bg-card)]/60 px-3 py-2 rounded-lg border border-[var(--color-border-subtle)]">
                    <div>
                      <div className="text-[8px] text-[var(--color-text-muted)] uppercase">PR-AUC</div>
                      <div className="font-bold text-[var(--color-accent-teal)]">
                        {ver.metrics.auc_roc.toFixed(4)}
                      </div>
                    </div>
                    <div className="w-[1px] bg-[var(--color-border-subtle)]" />
                    <div>
                      <div className="text-[8px] text-[var(--color-text-muted)] uppercase">F1</div>
                      <div className="font-bold text-[var(--color-accent-indigo-light)]">
                        {ver.metrics.f1_score.toFixed(4)}
                      </div>
                    </div>
                    <div className="w-[1px] bg-[var(--color-border-subtle)]" />
                    <div>
                      <div className="text-[8px] text-[var(--color-text-muted)] uppercase">Loss</div>
                      <div className="font-bold">{ver.metrics.loss.toFixed(4)}</div>
                    </div>
                  </div>

                  {/* Sign-offs statuses */}
                  <div className="space-y-1.5 min-w-[120px]">
                    <div className="text-[8px] text-[var(--color-text-muted)] uppercase font-semibold">
                      Sign-offs
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {['compliance', 'ml_engineer'].map((role) => {
                        const sig = ver.sign_offs?.find((s) => s.role === role);
                        return (
                          <span
                            key={role}
                            className={`px-1.5 py-0.5 rounded text-[8px] font-bold ${
                              sig
                                ? 'bg-[var(--color-status-success)]/10 text-[var(--color-status-success)] border border-[var(--color-status-success)]/20'
                                : 'bg-red-500/10 text-red-400 border border-red-500/20'
                            }`}
                          >
                            {role === 'compliance' ? 'Compliance: ' : 'ML Eng: '}{' '}
                            {sig ? `✓ (${sig.user})` : '✕'}
                          </span>
                        );
                      })}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex gap-2 shrink-0 self-end md:self-auto">
                    {ver.status === 'inactive' && (
                      <button
                        onClick={() => setSelectedVersionForSignoff(ver.version)}
                        className="px-2.5 py-1 bg-yellow-500/15 border border-yellow-500/30 hover:bg-yellow-500/25 text-yellow-500 rounded text-[10px] font-semibold active:scale-95 transition-all"
                      >
                        ✍ Sign Off
                      </button>
                    )}
                    <button
                      disabled={ver.is_active || rollbackMutation.isPending}
                      onClick={() => handleRollback(ver.version)}
                      className={`px-2.5 py-1 rounded text-[10px] font-semibold transition-all ${
                        ver.is_active
                          ? 'bg-transparent text-[var(--color-text-muted)] cursor-not-allowed border border-transparent'
                          : 'bg-[var(--color-bg-elevated)] hover:bg-[var(--color-border)] text-[var(--color-text-primary)] active:scale-95 border border-[var(--color-border-subtle)]'
                      }`}
                    >
                      Rollback
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Canary logs view */}
      {activeTab === 'canary' && (
        <div className="space-y-3">
          <div className="p-3 bg-[var(--color-bg-elevated)]/50 rounded-lg border border-[var(--color-border-subtle)] text-[10px] text-[var(--color-text-secondary)] leading-relaxed">
            💡 <strong>Canary Gate Rule:</strong> During the simulation loop, aggregated model updates must achieve a test AUC-ROC score greater than or equal to the currently promoted model (with a 0.5% grace tolerance) to be automatically deployed. If the quality check fails, the new model version is stored but is kept in an inactive (rejected) state.
          </div>

          <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
            {canaryHistory && canaryHistory.length > 0 ? (
              canaryHistory.map((item, idx) => (
                <motion.div
                  key={idx}
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="p-3 rounded-lg border border-[var(--color-border-subtle)] flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-[var(--color-text-primary)]">
                        Round {item.round} (Candidate v{item.version})
                      </span>
                      {item.is_promoted ? (
                        <span className="px-1.5 py-0.5 rounded text-[8px] bg-[var(--color-status-success)]/15 text-[var(--color-status-success)] font-semibold uppercase">
                          Promoted
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[8px] bg-[var(--color-status-error)]/15 text-[var(--color-status-error)] font-semibold uppercase">
                          Rejected
                        </span>
                      )}
                    </div>
                    <p className="text-[10px] text-[var(--color-text-muted)]">
                      {item.reason}
                    </p>
                  </div>

                  <div className="flex items-center gap-4 shrink-0 bg-[var(--color-bg-card)]/50 px-3 py-1.5 rounded border border-[var(--color-border-subtle)] font-mono text-[10px]">
                    <div>
                      <span className="text-[9px] text-[var(--color-text-muted)]">Candidate:</span>{' '}
                      <span className="font-bold text-[var(--color-text-primary)]">
                        {(item.candidate_auc ?? 0).toFixed(4)}
                      </span>
                    </div>
                    <div className="h-3 w-[1px] bg-[var(--color-border-subtle)]" />
                    <div>
                      <span className="text-[9px] text-[var(--color-text-muted)]">Previous:</span>{' '}
                      <span className="font-bold text-[var(--color-text-secondary)]">
                        {(item.promoted_auc ?? 0) > 0 ? (item.promoted_auc ?? 0).toFixed(4) : 'None'}
                      </span>
                    </div>
                  </div>
                </motion.div>
              ))
            ) : (
              <div className="text-center py-6 text-xs text-[var(--color-text-muted)]">
                Awaiting first communication round results to trigger canary checks...
              </div>
            )}
          </div>
        </div>
      )}
      {/* Shadow Deployment view */}
      {activeTab === 'shadow' && (
        <div className="space-y-4">
          <div className="p-3 bg-[var(--color-bg-elevated)]/50 rounded-lg border border-[var(--color-border-subtle)] text-[10px] text-[var(--color-text-secondary)] leading-relaxed">
            💡 <strong>SR 11-7 Compliance Shadow Routing:</strong> 100% of live traffic routes silently to the Challenger model to log latency and AUC characteristics. 10% of decision traffic is shifted to challenger output. If Challenger PR-AUC outperforms Champion, automated promotion is gated. If Champion AUC drops below 0.65, latency &gt; 200ms, or FPR &gt; 5%, auto-rollback is triggered.
          </div>

          {/* Champion vs Challenger Side-by-side Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Champion Box */}
            <div className="p-4 bg-[var(--color-bg-card)]/50 rounded-xl border border-[var(--color-border-subtle)] flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-[var(--color-text-primary)]">🏆 Current Champion</span>
                  <span className="px-2 py-0.5 rounded text-[8px] bg-[var(--color-status-success)]/15 text-[var(--color-status-success)] font-semibold uppercase">
                    Version {shadowMetrics?.champion_version || activeVersion?.version || 1}
                  </span>
                </div>
                <div className="space-y-1.5 mt-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">PR-AUC:</span>
                    <span className="font-mono font-bold text-[var(--color-accent-teal)]">
                      {shadowMetrics?.champion_pr_auc ? shadowMetrics.champion_pr_auc.toFixed(4) : '0.8500'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">AUC-ROC:</span>
                    <span className="font-mono font-bold">
                      {shadowMetrics?.champion_auc ? shadowMetrics.champion_auc.toFixed(4) : '0.8650'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">False Positive Rate (FPR):</span>
                    <span className={`font-mono font-bold ${shadowMetrics && shadowMetrics.champion_fpr > 0.05 ? 'text-red-400' : ''}`}>
                      {shadowMetrics?.champion_fpr ? `${(shadowMetrics.champion_fpr * 100).toFixed(1)}%` : '1.2%'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">Inference Latency:</span>
                    <span className={`font-mono font-bold ${shadowMetrics && shadowMetrics.champion_latency_ms > 200.0 ? 'text-red-400' : ''}`}>
                      {shadowMetrics?.champion_latency_ms ? `${shadowMetrics.champion_latency_ms.toFixed(1)}ms` : '12.4ms'}
                    </span>
                  </div>
                </div>
              </div>
              <div className="mt-4 pt-3 border-t border-[var(--color-border-subtle)] text-[10px] text-[var(--color-text-muted)] flex justify-between">
                <span>Serving share:</span>
                <span className="font-bold text-[var(--color-text-primary)]">
                  {shadowMetrics ? `${((1 - shadowMetrics.traffic_share) * 100).toFixed(0)}%` : '100%'}
                </span>
              </div>
            </div>

            {/* Challenger Box */}
            <div className="p-4 bg-[var(--color-bg-card)]/50 rounded-xl border border-[var(--color-border-subtle)] flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold text-[var(--color-text-primary)]">⚖️ Serving Challenger</span>
                  {shadowMetrics?.traffic_share && shadowMetrics.traffic_share > 0 ? (
                    <span className="px-2 py-0.5 rounded text-[8px] bg-blue-500/15 text-blue-400 font-semibold uppercase animate-pulse">
                      Active
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 rounded text-[8px] bg-[var(--color-bg-elevated)] text-[var(--color-text-muted)] font-semibold uppercase">
                      Inactive / Shadowing Only
                    </span>
                  )}
                </div>
                <div className="space-y-1.5 mt-2">
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">PR-AUC:</span>
                    <span className="font-mono font-bold text-[var(--color-accent-teal)]">
                      {shadowMetrics?.challenger_pr_auc && shadowMetrics.challenger_pr_auc > 0
                        ? shadowMetrics.challenger_pr_auc.toFixed(4)
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">AUC-ROC:</span>
                    <span className="font-mono font-bold">
                      {shadowMetrics?.challenger_auc && shadowMetrics.challenger_auc > 0
                        ? shadowMetrics.challenger_auc.toFixed(4)
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">False Positive Rate (FPR):</span>
                    <span className="font-mono font-bold">
                      {shadowMetrics?.challenger_fpr && shadowMetrics.challenger_fpr > 0
                        ? `${(shadowMetrics.challenger_fpr * 100).toFixed(1)}%`
                        : 'N/A'}
                    </span>
                  </div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-[var(--color-text-muted)]">Inference Latency:</span>
                    <span className="font-mono font-bold">
                      {shadowMetrics?.challenger_latency_ms && shadowMetrics.challenger_latency_ms > 0
                        ? `${shadowMetrics.challenger_latency_ms.toFixed(1)}ms`
                        : 'N/A'}
                    </span>
                  </div>
                </div>
              </div>
              <div className="mt-4 pt-3 border-t border-[var(--color-border-subtle)] text-[10px] text-[var(--color-text-muted)] flex justify-between">
                <span>Serving share:</span>
                <span className="font-bold text-blue-400">
                  {shadowMetrics ? `${(shadowMetrics.traffic_share * 100).toFixed(0)}%` : '0%'}
                </span>
              </div>
            </div>
          </div>

          {/* Interactive Mock Feedback Simulation Panel */}
          <div className="p-4 bg-[var(--color-bg-elevated)]/30 rounded-xl border border-[var(--color-border-subtle)] space-y-3">
            <h4 className="text-xs font-bold text-[var(--color-text-primary)]">
              🧪 Compliance Feedback & Shadow Testing
            </h4>
            <p className="text-[10px] text-[var(--color-text-muted)]">
              Simulate real-time ingestion of ground truth fraud outcomes to test the automated Champion promotion or performance rollbacks.
            </p>
            <form onSubmit={handleFeedbackSubmit} className="flex flex-col sm:flex-row gap-3 items-end">
              <div className="flex-1 space-y-1">
                <label className="text-[9px] text-[var(--color-text-muted)] uppercase font-semibold">
                  Transaction ID / Reference Hash
                </label>
                <input
                  type="text"
                  required
                  placeholder="e.g. txn_12345"
                  value={feedbackTxId}
                  onChange={(e) => setFeedbackTxId(e.target.value)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border-subtle)] rounded px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-[var(--color-accent-teal)] font-mono"
                />
              </div>
              <div className="space-y-1 shrink-0 w-32">
                <label className="text-[9px] text-[var(--color-text-muted)] uppercase font-semibold">
                  Actual Outcome
                </label>
                <select
                  value={feedbackLabel}
                  onChange={(e) => setFeedbackLabel(e.target.value)}
                  className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border-subtle)] rounded px-2 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none"
                >
                  <option value="0">0 - Legitimate</option>
                  <option value="1">1 - Confirmed Fraud</option>
                </select>
              </div>
              <button
                type="submit"
                disabled={feedbackMutation.isPending}
                className="px-4 py-1.5 bg-[var(--color-accent-teal)] hover:bg-[var(--color-accent-teal)]/90 text-white rounded text-xs font-semibold shrink-0 active:scale-95 transition-all"
              >
                {feedbackMutation.isPending ? 'Submitting...' : 'Submit outcome'}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Model Sign-Off Form Overlay Modal */}
      {selectedVersionForSignoff !== null && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="w-full max-w-md bg-[var(--color-bg-card)] border border-[var(--color-border-subtle)] rounded-xl p-5 shadow-xl space-y-4 text-xs"
          >
            <div>
              <h3 className="text-sm font-bold text-[var(--color-text-primary)]">
                ✍ Submit Cryptographic Sign-Off (Version {selectedVersionForSignoff})
              </h3>
              <p className="text-[10px] text-[var(--color-text-muted)]">
                Provide regulatory clearance for fairness, bias, and dataset drift divergence parameters before promotion.
              </p>
            </div>

            <form onSubmit={handleSignOff} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-[9px] text-[var(--color-text-muted)] uppercase font-semibold">Signer Role</label>
                  <select
                    value={signoffRole}
                    onChange={(e) => setSignoffRole(e.target.value as any)}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2.5 py-1.5 text-xs text-[var(--color-text-primary)]"
                  >
                    <option value="ml_engineer">ML Engineer</option>
                    <option value="compliance">Compliance Officer</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[9px] text-[var(--color-text-muted)] uppercase font-semibold">Signer Name</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Jane Doe"
                    value={signoffUser}
                    onChange={(e) => setSignoffUser(e.target.value)}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2.5 py-1.5 text-xs text-[var(--color-text-primary)]"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-[9px] text-[var(--color-text-muted)] uppercase font-semibold">Cryptographic Signature</label>
                <input
                  type="text"
                  required
                  placeholder="SHA-256 certificate signature"
                  value={signoffSignature}
                  onChange={(e) => setSignoffSignature(e.target.value)}
                  className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2.5 py-1.5 text-xs text-[var(--color-text-primary)] font-mono"
                />
              </div>

              <div className="grid grid-cols-3 gap-2 border-t border-[var(--color-border-subtle)] pt-3">
                <div className="space-y-1">
                  <label className="text-[8px] text-[var(--color-text-muted)] uppercase">Fairness Score</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    value={fairness}
                    onChange={(e) => setFairness(e.target.value)}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2 py-1 text-xs font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[8px] text-[var(--color-text-muted)] uppercase">Bias Metric</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    value={bias}
                    onChange={(e) => setBias(e.target.value)}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2 py-1 text-xs font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-[8px] text-[var(--color-text-muted)] uppercase">Drift Diverg.</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    max="1"
                    value={drift}
                    onChange={(e) => setDrift(e.target.value)}
                    className="w-full bg-[var(--color-bg-elevated)] border border-[var(--color-border-subtle)] rounded px-2 py-1 text-xs font-mono"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-3 border-t border-[var(--color-border-subtle)]">
                <button
                  type="button"
                  onClick={() => setSelectedVersionForSignoff(null)}
                  className="px-3 py-1.5 bg-[var(--color-bg-elevated)] hover:bg-[var(--color-border)] rounded text-xs text-[var(--color-text-primary)]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={signoffMutation.isPending}
                  className="px-4 py-1.5 bg-[var(--color-status-success)] hover:bg-[var(--color-status-success)]/95 text-white rounded text-xs font-semibold"
                >
                  {signoffMutation.isPending ? 'Submitting...' : 'Authorize Sign-Off'}
                </button>
              </div>
            </form>
          </motion.div>
        </div>
      )}
    </div>
  );
}
