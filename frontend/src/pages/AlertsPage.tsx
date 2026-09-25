import { useState, useEffect } from 'react';
import { useSearchParams, Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
  useAlerts,
  useAlert,
  useAlertExplainability,
  useAlertCounterfactuals,
  useAlertDecisionReplay,
  useAlertGNNExplanation,
  useCreateCase,
  useUpdateAlertStatus,
  useAddCaseNote,
  useAddEvidence,
} from '../api/queries';
import { BANK_NAMES, SEVERITY_COLORS } from '../api/types';
import type { Alert } from '../api/types';

export const ALERTS_SELECTED_ID_KEY = 'cfi_selected_alert_id';
export const ALERTS_BANK_FILTER_KEY = 'cfi_alerts_bank_filter';
export const ALERTS_SEVERITY_FILTER_KEY = 'cfi_alerts_severity_filter';

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'];

export default function AlertsPage() {
  const navigate = useNavigate();
  const createCase = useCreateCase();
  const updateAlertStatus = useUpdateAlertStatus();
  const addCaseNote = useAddCaseNote();
  const addEvidence = useAddEvidence();

  const [searchParams, setSearchParams] = useSearchParams();
  const [escalatingAlertId, setEscalatingAlertId] = useState<string | null>(null);
  const [escalationError, setEscalationError] = useState<string | null>(null);

  // 1. Restore selected alert ID from URL search params (?alert_id=...) or sessionStorage across tab switches
  const [selectedAlertId, setSelectedAlertId] = useState<string | null>(() => {
    try {
      const urlId = searchParams.get('alert_id');
      if (urlId) return urlId;
      if (typeof window !== 'undefined') {
        return sessionStorage.getItem(ALERTS_SELECTED_ID_KEY);
      }
    } catch { /* ignore */ }
    return null;
  });

  // 2. Restore filter preferences from URL or sessionStorage
  const [selectedBank, setSelectedBank] = useState<string>(() => {
    try {
      const urlBank = searchParams.get('bank_id');
      if (urlBank) return urlBank;
      if (typeof window !== 'undefined') {
        return sessionStorage.getItem(ALERTS_BANK_FILTER_KEY) || '';
      }
    } catch { /* ignore */ }
    return '';
  });

  const [selectedSeverity, setSelectedSeverity] = useState<string>(() => {
    try {
      const urlSev = searchParams.get('severity');
      if (urlSev) return urlSev;
      if (typeof window !== 'undefined') {
        return sessionStorage.getItem(ALERTS_SEVERITY_FILTER_KEY) || '';
      }
    } catch { /* ignore */ }
    return '';
  });

  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);

  // Direct fetch fallback in case the alert is outside the filtered list or on direct deep link
  const { data: directAlert } = useAlert(selectedAlertId || undefined);

  const { data: alerts, isLoading } = useAlerts({
    bank_id: selectedBank || undefined,
    severity: selectedSeverity || undefined,
  });

  // 3. Re-synchronize selectedAlert whenever alerts list or directAlert loads
  useEffect(() => {
    if (selectedAlertId) {
      const matching = alerts?.find((a) => a.id === selectedAlertId) || directAlert;
      if (matching) {
        setSelectedAlert(matching);
      }
    } else {
      setSelectedAlert(null);
    }
  }, [selectedAlertId, alerts, directAlert]);

  // 4. Synchronize selectedAlertId, bank, and severity to sessionStorage
  useEffect(() => {
    try {
      if (selectedAlertId) {
        sessionStorage.setItem(ALERTS_SELECTED_ID_KEY, selectedAlertId);
      } else {
        sessionStorage.removeItem(ALERTS_SELECTED_ID_KEY);
      }

      if (selectedBank) {
        sessionStorage.setItem(ALERTS_BANK_FILTER_KEY, selectedBank);
      } else {
        sessionStorage.removeItem(ALERTS_BANK_FILTER_KEY);
      }

      if (selectedSeverity) {
        sessionStorage.setItem(ALERTS_SEVERITY_FILTER_KEY, selectedSeverity);
      } else {
        sessionStorage.removeItem(ALERTS_SEVERITY_FILTER_KEY);
      }
    } catch { /* ignore */ }
  }, [selectedAlertId, selectedBank, selectedSeverity]);

  // 5. Handlers with synchronized URL search parameters
  const handleSelectAlert = (alert: Alert) => {
    if (selectedAlertId === alert.id) {
      handleClearSelectedAlert();
      return;
    }
    setSelectedAlert(alert);
    setSelectedAlertId(alert.id);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('alert_id', alert.id);
        return next;
      },
      { replace: true }
    );
  };

  const handleClearSelectedAlert = () => {
    setSelectedAlert(null);
    setSelectedAlertId(null);
    try {
      sessionStorage.removeItem(ALERTS_SELECTED_ID_KEY);
    } catch { /* ignore */ }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('alert_id');
        return next;
      },
      { replace: true }
    );
  };

  const handleBankChange = (bankId: string) => {
    setSelectedBank(bankId);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (bankId) next.set('bank_id', bankId);
        else next.delete('bank_id');
        return next;
      },
      { replace: true }
    );
  };

  const handleSeverityChange = (severity: string) => {
    setSelectedSeverity(severity);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (severity) next.set('severity', severity);
        else next.delete('severity');
        return next;
      },
      { replace: true }
    );
  };

  const handleEscalateAlertToCase = async (alertToEscalate: Alert) => {
    setEscalatingAlertId(alertToEscalate.id);
    setEscalationError(null);
    try {
      // 1. Calculate Priority based on risk score and severity
      let priority = 'p4_low';
      if (alertToEscalate.severity === 'critical' || alertToEscalate.risk_score >= 800) {
        priority = 'p1_critical';
      } else if (alertToEscalate.severity === 'high' || alertToEscalate.risk_score >= 650) {
        priority = 'p2_high';
      } else if (alertToEscalate.severity === 'medium' || alertToEscalate.risk_score >= 400) {
        priority = 'p3_medium';
      }

      // 2. Synthesize descriptive Case Title
      const bankLabel = BANK_NAMES[alertToEscalate.bank_id] || alertToEscalate.bank_id;
      const primaryReason = alertToEscalate.reason_codes?.[0] || 'High Risk';
      const txRef = alertToEscalate.transaction_id || alertToEscalate.id.slice(0, 8);
      const caseTitle = `[${bankLabel.toUpperCase()}] AML Case: ${primaryReason} - Tx ${txRef}`;

      // 3. Create Case via TanStack Query mutation
      const newCase = await createCase.mutateAsync({
        title: caseTitle,
        priority,
        alert_ids: [alertToEscalate.id],
        total_risk_score: alertToEscalate.risk_score,
      });

      // 4. Update Alert status to 'escalated'
      await updateAlertStatus.mutateAsync({
        alertId: alertToEscalate.id,
        payload: {
          status: 'escalated',
          resolution_notes: `Escalated directly to AML Investigation Case #${newCase.id.slice(0, 8)} (${newCase.title})`,
        },
      });

      // 5. Append initial forensic SHAP attribution and triage note
      const shapDrivers = (alertToEscalate.top_features || [])
        .slice(0, 5)
        .map((f: any) => `${f.feature || f.name}: +${Math.round((f.contribution ?? f.value ?? 0) * 100)}%`)
        .join(', ');
      const entityList = (alertToEscalate.involved_entity_ids || []).join(', ');
      const reasonList = (alertToEscalate.reason_codes || []).join(', ');

      const forensicContent = `⚡ Automated AML Triage Escalation from Alerts Workbench
- Origin Alert ID: ${alertToEscalate.id}
- Reporting Institution: ${bankLabel}
- Associated Transaction: ${alertToEscalate.transaction_id || 'N/A'}
- Composite Risk Score: ${alertToEscalate.risk_score.toFixed(1)} / 1000 (Inference Confidence: ${((alertToEscalate.confidence ?? 0) * 100).toFixed(1)}%)
- Severity Classification: ${alertToEscalate.severity.toUpperCase()}
- Fraud Reason Codes: ${reasonList || 'None specified'}
- Suspect Entity Identifiers: ${entityList || 'None mapped'}
- Primary SHAP Attribution Drivers: ${shapDrivers || 'Baseline feature distribution'}`;

      await addCaseNote.mutateAsync({
        caseId: newCase.id,
        author: 'AI Triage Engine',
        content: forensicContent,
      });

      // 6. Register Alert Telemetry Evidence in Case Evidence Registry (Chain of Custody)
      const evidencePayload = JSON.stringify({
        alert_id: alertToEscalate.id,
        bank_id: alertToEscalate.bank_id,
        transaction_id: alertToEscalate.transaction_id,
        risk_score: alertToEscalate.risk_score,
        severity: alertToEscalate.severity,
        reason_codes: alertToEscalate.reason_codes,
        involved_entity_ids: alertToEscalate.involved_entity_ids,
        top_features: alertToEscalate.top_features,
        risk_factors: alertToEscalate.risk_factors,
        escalated_at: new Date().toISOString(),
      }, null, 2);

      await addEvidence.mutateAsync({
        caseId: newCase.id,
        evidence_type: 'document',
        title: `Alert Telemetry & SHAP Dossier (${alertToEscalate.id.slice(0, 8)})`,
        file_path: `evidence/alerts/alert_${alertToEscalate.id.slice(0, 8)}_dossier.json`,
        content: evidencePayload,
        uploaded_by: 'AI Triage Engine',
      });

      // 7. Route directly to /cases/:newCaseId
      navigate(`/cases/${newCase.id}`);
    } catch (err: unknown) {
      console.error('Failed to escalate alert to case:', err);
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as Error)?.message
        || 'Failed to escalate alert to investigation case. Please try again.';
      setEscalationError(detail);
      setEscalatingAlertId(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <h1 className="text-2xl font-bold gradient-text mb-1">
          Alert Intelligence
        </h1>
        <p className="text-sm text-[var(--color-text-muted)] max-w-2xl">
          Privacy-preserving fraud alerts generated by the risk scoring engine.
          Each alert contains only risk indicators and hashed identifiers (never raw transaction data).
        </p>
      </motion.div>

      {/* Filters */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex gap-3 flex-wrap"
      >
        <select
          value={selectedBank}
          onChange={(e) => handleBankChange(e.target.value)}
          className="glass-card px-3 py-2 text-sm rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)]"
        >
          <option value="">All Banks</option>
          {Object.entries(BANK_NAMES).map(([id, name]) => (
            <option key={id} value={id}>{name}</option>
          ))}
        </select>

        <select
          value={selectedSeverity}
          onChange={(e) => handleSeverityChange(e.target.value)}
          className="glass-card px-3 py-2 text-sm rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)]"
        >
          <option value="">All Severities</option>
          {SEVERITY_ORDER.map((s) => (
            <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
          ))}
        </select>

        <div className="ml-auto text-sm text-[var(--color-text-muted)] self-center">
          {alerts?.length ?? 0} alerts
        </div>
      </motion.div>

      {/* Escalation Error Toast */}
      {escalationError && (
        <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-xs text-rose-300 flex items-center justify-between gap-2 animate-in fade-in">
          <span>❌ {escalationError}</span>
          <button
            onClick={() => setEscalationError(null)}
            className="text-rose-400 hover:text-white text-xs font-bold p-1 rounded"
            aria-label="Dismiss error"
          >
            ✕
          </button>
        </div>
      )}

      {/* Alert List + Detail */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Alert Feed */}
        <div className="lg:col-span-2 space-y-3">
          {isLoading ? (
            <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">
              Loading alerts...
            </div>
          ) : !alerts?.length ? (
            <div className="glass-card p-8 text-center text-[var(--color-text-muted)]">
              <p className="text-lg mb-2">No alerts yet</p>
              <p className="text-sm">Run a scenario from the Scenarios page to generate alerts.</p>
            </div>
          ) : (
            <AnimatePresence>
              {alerts.map((alert, i) => (
                <AlertCard
                  key={alert.id}
                  alert={alert}
                  index={i}
                  isSelected={selectedAlertId === alert.id || selectedAlert?.id === alert.id}
                  onClick={() => handleSelectAlert(alert)}
                  onEscalate={handleEscalateAlertToCase}
                  isEscalating={escalatingAlertId === alert.id}
                />
              ))}
            </AnimatePresence>
          )}
        </div>

        {/* Explainability Panel (Desktop) */}
        <div className="hidden lg:block lg:col-span-1">
          {selectedAlert ? (
            <ExplainabilityPanel
              alert={selectedAlert}
              onClose={handleClearSelectedAlert}
              onEscalate={handleEscalateAlertToCase}
              isEscalating={escalatingAlertId === selectedAlert.id}
            />
          ) : (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="glass-card p-6 text-center text-[var(--color-text-muted)] sticky top-6"
            >
              <div className="text-3xl mb-3">🔍</div>
              <p>Select an alert to view its explainability report</p>
            </motion.div>
          )}
        </div>
      </div>

      {/* Mobile Explainability Modal */}
      {selectedAlert && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm lg:hidden">
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            className="glass-card w-full max-w-lg max-h-[85vh] overflow-y-auto relative flex flex-col p-0"
          >
            <div className="p-4 border-b border-[var(--color-border)] flex items-center justify-between sticky top-0 bg-[var(--color-bg-card)] z-10">
              <h3 className="font-bold text-[var(--color-text-primary)]">Alert Details</h3>
              <button
                onClick={handleClearSelectedAlert}
                className="p-1 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-bg-card-hover)] focus:outline-none"
                aria-label="Close details"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-5 overflow-y-auto">
              <ExplainabilityPanel
                alert={selectedAlert}
                onClose={handleClearSelectedAlert}
                onEscalate={handleEscalateAlertToCase}
                isEscalating={escalatingAlertId === selectedAlert.id}
              />
            </div>
          </motion.div>
        </div>
      )}
    </div>
  );
}

function AlertCard({
  alert,
  index,
  isSelected,
  onClick,
  onEscalate,
  isEscalating,
}: {
  alert: Alert;
  index: number;
  isSelected: boolean;
  onClick: () => void;
  onEscalate?: (alert: Alert) => void;
  isEscalating?: boolean;
}) {
  const severityColor = SEVERITY_COLORS[alert.severity] || '#6b7280';

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ delay: index * 0.05 }}
      onClick={onClick}
      className={`glass-card p-4 cursor-pointer transition-all duration-200 hover:scale-[1.01] ${
        isSelected ? 'ring-2 ring-[var(--color-primary)]' : ''
      }`}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span
            className="px-2 py-0.5 rounded-full text-xs font-bold uppercase text-white"
            style={{ backgroundColor: severityColor }}
          >
            {alert.severity}
          </span>
          <span className="text-xs text-[var(--color-text-muted)]">
            {BANK_NAMES[alert.bank_id] || alert.bank_id}
          </span>
        </div>
        <span className="text-xs text-[var(--color-text-muted)]">
          {new Date(alert.created_at).toLocaleTimeString()}
        </span>
      </div>

      <div className="flex items-center gap-4 mb-2">
        <div>
          <div className="text-2xl font-bold" style={{ color: severityColor }}>
            {(alert.risk_score ?? 0).toFixed(0)}
          </div>
          <div className="text-[10px] uppercase text-[var(--color-text-muted)]">Risk Score</div>
        </div>
        <div className="flex-1">
          <div className="h-2 bg-[var(--color-surface-alt)] rounded-full overflow-hidden">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${(alert.risk_score ?? 0) / 10}%` }}
              transition={{ delay: index * 0.05 + 0.2, duration: 0.5 }}
              className="h-full rounded-full"
              style={{ backgroundColor: severityColor }}
            />
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-mono">{((alert.confidence ?? 0) * 100).toFixed(1)}%</div>
          <div className="text-[10px] uppercase text-[var(--color-text-muted)]">Confidence</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {(alert.reason_codes ?? []).map((code) => (
          <span
            key={code}
            className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]"
          >
            {code}
          </span>
        ))}
      </div>

      {/* Deep Link to Graph for Involved Entities */}
      {alert.involved_entity_ids && alert.involved_entity_ids.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap pt-2 mt-2 border-t border-[var(--color-border)]/40">
          <span className="text-[10px] uppercase font-mono text-[var(--color-text-muted)] flex items-center gap-1">
            <span>🕸️</span> Suspect Nodes:
          </span>
          {alert.involved_entity_ids.map((entityId) => (
            <div key={entityId} className="flex items-center gap-1">
              <Link
                to={`/graph?entity_id=${encodeURIComponent(entityId)}&depth=2`}
                onClick={(e) => e.stopPropagation()}
                className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 hover:text-white border border-indigo-500/30 transition-all flex items-center gap-1 group/ent"
                title={`Trace 2-hop ego network for ${entityId}`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 group-hover/ent:scale-125 transition-transform" />
                <span className="truncate max-w-[120px]">{entityId}</span>
                <span className="text-[9px] text-indigo-400 opacity-70 group-hover/ent:opacity-100">➔</span>
              </Link>
              <Link
                to={`/psi?entity_id=${encodeURIComponent(entityId)}&auto_match=true`}
                onClick={(e) => e.stopPropagation()}
                className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 hover:text-emerald-100 border border-emerald-500/30 transition-all flex items-center gap-0.5"
                title={`Check PSI Consortium Intersection for ${entityId}`}
              >
                <span>🔍 PSI</span>
              </Link>
            </div>
          ))}
        </div>
      )}

      {/* Escalation & Status Action Bar */}
      <div className="flex items-center justify-between gap-2 pt-2 mt-2 border-t border-[var(--color-border)]/40 flex-wrap">
        <div className="flex items-center gap-1.5 flex-wrap">
          {alert.status === 'escalated' ? (
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-500/15 text-purple-300 border border-purple-500/30 flex items-center gap-1">
              <span>⚡</span> Escalated to Case
            </span>
          ) : (
            <span className="text-[10px] font-mono text-[var(--color-text-muted)]">
              Status: <span className="capitalize text-slate-300 font-semibold">{alert.status}</span>
            </span>
          )}
          <Link
            to={`/workbench?alert_id=${encodeURIComponent(alert.id)}`}
            onClick={(e) => e.stopPropagation()}
            className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 hover:text-cyan-100 border border-cyan-500/30 transition-all flex items-center gap-1"
            title="Simulate feature remediation in Counterfactual Workbench"
          >
            <span>🎛️ Simulate</span>
            <span className="text-[9px]">➔</span>
          </Link>
        </div>
        {onEscalate && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onEscalate(alert);
            }}
            disabled={isEscalating}
            className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-sm transition-all flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
            title="Escalate alert into an official AML Investigation Case"
          >
            {isEscalating ? (
              <>
                <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>Escalating...</span>
              </>
            ) : (
              <>
                <span>⚡</span>
                <span>Escalate to Case</span>
              </>
            )}
          </button>
        )}
      </div>
    </motion.div>
  );
}

export function ExplainabilityPanel({
  alert,
  onClose,
  onEscalate,
  isEscalating,
  hideEscalateButton,
}: {
  alert: Alert;
  onClose?: () => void;
  onEscalate?: (alert: Alert) => void;
  isEscalating?: boolean;
  hideEscalateButton?: boolean;
}) {
  const [activeTab, setActiveTab] = useState<'attribution' | 'counterfactuals' | 'audit' | 'gnn'>('attribution');
  const { data: report, isLoading: isReportLoading } = useAlertExplainability(alert.id);
  const { data: cfReport, isLoading: isCfLoading } = useAlertCounterfactuals(alert.id);
  const { data: auditReport, isLoading: isAuditLoading } = useAlertDecisionReplay(alert.id);
  const { data: gnnReport, isLoading: isGnnLoading } = useAlertGNNExplanation(alert.id);

  return (
    <motion.div
      key={alert.id}
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="glass-card p-5 sticky top-6 space-y-4 border border-[var(--color-border)] shadow-2xl bg-slate-950/90 text-slate-100"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
        <h3 className="text-xs sm:text-sm font-bold uppercase text-slate-200 tracking-wider flex items-center gap-1.5 min-w-0">
          <span className="shrink-0">🧠</span>
          <span className="truncate">AI Explainability Portal</span>
        </h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 font-bold">
            GDPR Art. 22 Compliant
          </span>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 rounded-md text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
              title="Close panel"
              aria-label="Close explainability panel"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* AML Case Escalation Action Banner */}
      {!hideEscalateButton && (
        <div className="p-3.5 bg-gradient-to-r from-indigo-950/90 via-purple-950/80 to-slate-900/90 rounded-xl border border-indigo-500/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-lg">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-amber-400 font-bold text-xs uppercase tracking-wider flex items-center gap-1">
                <span>⚡</span> Triage Action
              </span>
              {alert.status === 'escalated' ? (
                <span className="px-2 py-0.5 rounded text-[9.5px] font-mono font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">
                  ESCALATED TO CASE
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded text-[9.5px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                  CRITICAL DISPOSITION
                </span>
              )}
            </div>
            <p className="text-[11px] text-slate-300 mt-1 leading-normal">
              {alert.status === 'escalated'
                ? 'This alert is linked to an AML investigation case with cryptographically chained evidence.'
                : 'Convert alert telemetry, SHAP attributions, and suspect graph entities into an AML case.'}
            </p>
          </div>
          <button
            id="escalate-alert-to-case-btn"
            type="button"
            onClick={() => onEscalate && onEscalate(alert)}
            disabled={isEscalating}
            className="px-3.5 py-2 rounded-xl text-xs font-bold text-white bg-gradient-to-r from-indigo-600 via-purple-600 to-indigo-600 hover:brightness-110 shadow-md shadow-indigo-600/30 border border-indigo-400/40 transition-all flex items-center justify-center gap-2 shrink-0 cursor-pointer disabled:opacity-50"
            title="Escalate alert to official AML Investigation Case"
          >
            {isEscalating ? (
              <>
                <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>Escalating Case...</span>
              </>
            ) : (
              <>
                <span>⚡</span>
                <span>Escalate to AML Investigation Case</span>
              </>
            )}
          </button>
        </div>
      )}

      {/* Suspect Graph Entities & Deep-Link Jump Bar */}
      {alert.involved_entity_ids && alert.involved_entity_ids.length > 0 && (
        <div className="p-3 bg-slate-900/90 rounded-xl border border-indigo-500/25 space-y-2">
          <div className="flex items-center justify-between text-[11px] font-bold text-slate-300 uppercase tracking-wider">
            <span className="flex items-center gap-1.5 text-indigo-300">
              <span>🕸️</span> Suspect Graph Entities
            </span>
            <span className="text-[10px] font-mono text-indigo-400">
              {alert.involved_entity_ids.length} linked
            </span>
          </div>
          <div className="space-y-1.5">
            {alert.involved_entity_ids.map((entityId) => (
              <div
                key={entityId}
                className="flex items-center justify-between p-2 rounded-lg bg-slate-950/70 border border-slate-800/80 gap-2"
              >
                <span className="font-mono text-xs text-indigo-200 truncate font-semibold" title={entityId}>
                  {entityId}
                </span>
                <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
                  <Link
                    to={`/graph?entity_id=${encodeURIComponent(entityId)}&depth=2`}
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-200 border border-indigo-500/40 transition-colors flex items-center gap-1"
                    title={`Explore 2-hop ego network for ${entityId}`}
                  >
                    <span>2-Hop</span>
                    <span className="text-[9px]">➔</span>
                  </Link>
                  <Link
                    to={`/graph?entity_id=${encodeURIComponent(entityId)}&depth=3`}
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-600/30 hover:bg-purple-600/50 text-purple-200 border border-purple-500/40 transition-colors flex items-center gap-1"
                    title={`Explore 3-hop ring network for ${entityId}`}
                  >
                    <span>3-Hop</span>
                    <span className="text-[9px]">➔</span>
                  </Link>
                  <Link
                    to={`/psi?entity_id=${encodeURIComponent(entityId)}&auto_match=true`}
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-600/30 hover:bg-emerald-600/50 text-emerald-200 border border-emerald-500/40 transition-colors flex items-center gap-1"
                    title={`Check PSI Consortium Intersection for ${entityId}`}
                  >
                    <span>🔍 Check PSI</span>
                    <span className="text-[9px]">➔</span>
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 p-1.5 bg-slate-900/90 border border-slate-800 rounded-xl text-center text-xs font-bold">
        {[
          { id: 'attribution', label: 'Attribution' },
          { id: 'counterfactuals', label: 'Remediation' },
          { id: 'audit', label: 'Audit Replay' },
          { id: 'gnn', label: 'GNN Explainer' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`py-1.5 px-2 rounded-lg transition-all text-[11px] truncate whitespace-nowrap ${
              activeTab === tab.id
                ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30 font-bold'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 font-medium'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab 1: Attribution */}
      {activeTab === 'attribution' && (
        <div className="space-y-4">
          {isReportLoading ? (
            <div className="text-center py-6 text-slate-400 text-xs font-mono">Analyzing feature contributions...</div>
          ) : !report ? (
            <div className="text-center py-6 text-slate-400 text-xs font-mono">No report available</div>
          ) : (
            <>
              {/* Risk Factors */}
              <div>
                <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider flex items-center gap-1.5">
                  <span>⚠️</span> Risk Factors
                </h4>
                <ul className="space-y-1.5">
                  {(report.risk_factors ?? []).map((factor, i) => (
                    <li key={i} className="text-xs flex items-start gap-2 bg-rose-500/10 border border-rose-500/20 p-2 rounded-lg text-rose-200 font-medium leading-normal">
                      <span className="text-rose-400 font-bold shrink-0 mt-0.5">•</span>
                      <span>{factor}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Top Features */}
              <div>
                <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider flex items-center gap-1.5">
                  <span>📊</span> Kernel SHAP Feature Attribution
                </h4>
                <div className="space-y-2.5">
                  {(report.top_features || []).slice(0, 5).map((f, i) => {
                    const anyF = f as any;
                    const rawVal =
                      typeof anyF.contribution === 'number'
                        ? anyF.contribution
                        : typeof anyF.value === 'number'
                        ? anyF.value
                        : 0;
                    const pct = rawVal * 100;
                    const barWidth = rawVal > 0.25 ? pct : pct * 5;

                    return (
                      <div key={i} className="text-xs space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-200 capitalize">
                            {f.feature.replace(/_/g, ' ')}
                          </span>
                          <span className="font-mono font-bold text-cyan-400">{pct.toFixed(0)}%</span>
                        </div>
                        <div className="h-2 bg-slate-900 border border-slate-800 rounded-full overflow-hidden">
                          <motion.div
                            initial={{ width: 0 }}
                            animate={{ width: `${Math.min(100, barWidth)}%` }}
                            transition={{ delay: i * 0.1 }}
                            className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-sky-400 to-cyan-400"
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Signal Breakdown */}
              {(report.risk_score_breakdown ?? []).length > 0 && (
                <div>
                  <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider flex items-center gap-1.5">
                    <span>⚡</span> 9-Signal Composite Pipeline
                  </h4>
                  <div className="space-y-2.5">
                    {(report.risk_score_breakdown ?? [])
                      .slice()
                      .sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0))
                      .slice(0, 5)
                      .map((sig, i) => (
                        <div key={i} className="text-xs space-y-1">
                          <div className="flex justify-between items-center">
                            <span className="font-semibold text-slate-200 capitalize">
                              {sig.signal_name.replace(/_/g, ' ').replace('rules', '')}
                            </span>
                            <span className="font-mono text-emerald-400 font-bold">
                              {((sig.contribution ?? 0) * 100).toFixed(0)}%
                            </span>
                          </div>
                          <div className="h-2 bg-slate-900 border border-slate-800 rounded-full overflow-hidden">
                            <div
                              className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-teal-400 to-emerald-400"
                              style={{ width: `${Math.max(3, (sig.contribution ?? 0) * 100)}%` }}
                            />
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Tab 2: Counterfactuals */}
      {activeTab === 'counterfactuals' && (
        <div className="space-y-4 text-xs">
          {isCfLoading ? (
            <div className="text-center py-6 text-slate-400 font-mono">Generating remediation paths...</div>
          ) : !cfReport ? (
            <div className="text-center py-6 text-slate-400 font-mono space-y-3">
              <div>No pre-computed counterfactual report available for this alert.</div>
              <Link
                to={`/workbench?alert_id=${encodeURIComponent(alert.id)}`}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-cyan-600 hover:bg-cyan-500 border border-cyan-400/40 transition-colors"
              >
                <span>🎛️ Launch Counterfactual Workbench</span>
                <span className="text-[10px]">➔</span>
              </Link>
            </div>
          ) : (
            <>
              {/* Direct Deep Link to Counterfactual Workbench */}
              <div className="p-3 bg-gradient-to-r from-cyan-950/80 via-slate-900/90 to-purple-950/80 rounded-xl border border-cyan-500/30 flex items-center justify-between gap-3 shadow-lg">
                <div>
                  <div className="text-[11px] font-bold text-cyan-300 uppercase tracking-wider flex items-center gap-1.5">
                    <span>🎛️</span> Interactive Parameter Workbench
                  </div>
                  <p className="text-[10px] text-slate-300 mt-0.5 leading-normal">
                    Tune transaction amount, velocity, and risk sliders dynamically to simulate optimal remediation paths.
                  </p>
                </div>
                <Link
                  to={`/workbench?alert_id=${encodeURIComponent(alert.id)}`}
                  className="px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-gradient-to-r from-cyan-600 to-blue-600 hover:brightness-110 shadow-md shadow-cyan-600/30 border border-cyan-400/40 transition-all flex items-center gap-1.5 shrink-0 whitespace-nowrap cursor-pointer"
                  title="Open Counterfactual Workbench for this alert"
                >
                  <span>Open Workbench</span>
                  <span className="text-[10px]">➔</span>
                </Link>
              </div>

              <div className="p-3 bg-slate-900/80 rounded-xl border border-slate-800">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-bold text-slate-200">Remediation Target</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      cfReport.is_cleared ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                    }`}
                  >
                    {cfReport.is_cleared ? 'CLEARED (<350 Risk)' : 'ACTION REQUIRED'}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-center my-2">
                  <div className="flex-1 bg-slate-950/60 p-2 rounded-lg border border-slate-800">
                    <div className="text-lg font-bold text-rose-400 font-mono">{cfReport.original_score}</div>
                    <div className="text-[9px] uppercase text-slate-400 font-semibold">Current Risk</div>
                  </div>
                  <div className="text-lg text-slate-400">➔</div>
                  <div className="flex-1 bg-slate-950/60 p-2 rounded-lg border border-slate-800">
                    <div className="text-lg font-bold text-emerald-400 font-mono">{cfReport.remediated_score}</div>
                    <div className="text-[9px] uppercase text-slate-400 font-semibold">Target Risk</div>
                  </div>
                </div>
              </div>

              <div>
                <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider">
                  Actionable Remediation Statements
                </h4>
                <div className="space-y-2">
                  {(cfReport.changes ?? []).map((change, i) => (
                    <div key={i} className="p-3 bg-slate-900/80 rounded-xl border border-slate-800 space-y-1">
                      <div className="flex items-center justify-between font-bold text-slate-200 capitalize">
                        <span>{change.feature.replace(/_/g, ' ')}</span>
                        <span className="text-[10px] font-mono text-cyan-400 bg-cyan-950/40 px-2 py-0.5 rounded border border-cyan-500/20">
                          {change.original_value} ➔ {change.remediated_value}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-300 leading-relaxed">
                        {change.delta_explanation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Tab 3: Deterministic Audit Replay */}
      {activeTab === 'audit' && (
        <div className="space-y-4 text-xs">
          {isAuditLoading ? (
            <div className="text-center py-6 text-slate-400 font-mono">Replaying inference audit...</div>
          ) : !auditReport ? (
            <div className="text-center py-6 text-slate-400 font-mono">Audit replay not available</div>
          ) : (
            <>
              <div className="p-3 bg-slate-900/80 rounded-xl border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-300">Model Version</span>
                  <span className="font-mono text-indigo-400 font-bold">{auditReport.model_version}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Model Baseline AUC</span>
                  <span className="font-mono font-bold text-slate-200">{((auditReport.model_auc ?? 0) * 100).toFixed(1)}%</span>
                </div>
                <div className="flex items-center justify-between pt-1.5 border-t border-slate-800">
                  <span className="text-slate-400">Inference Audit Match</span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                      auditReport.audit_matched ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                    }`}
                  >
                    {auditReport.audit_matched ? '✓ 100% REPRODUCED' : 'MISMATCH'}
                  </span>
                </div>
              </div>

              <div>
                <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider">
                  9-Signal Replay Execution Trace
                </h4>
                <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                  {(auditReport.policy_rules_evaluated ?? []).map((rule, i) => (
                    <div key={i} className="flex items-center justify-between p-2 bg-slate-900/80 rounded-lg border border-slate-800">
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className={`w-2 h-2 rounded-full shrink-0 ${
                            rule.triggered ? 'bg-amber-400 animate-pulse' : 'bg-slate-600'
                          }`}
                        />
                        <span className="font-mono font-bold text-[10px] text-indigo-400 shrink-0">{rule.rule_code}</span>
                        <span className="text-slate-300 capitalize truncate w-28 text-xs">
                          {rule.signal_name.replace(/_/g, ' ')}
                        </span>
                      </div>
                      <div className="font-mono text-[10px] text-right font-bold text-emerald-400 shrink-0">
                        +{((rule.contribution ?? 0) * 1000).toFixed(0)} pts
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Tab 4: GNN Explainer */}
      {activeTab === 'gnn' && (
        <div className="space-y-4 text-xs">
          {isGnnLoading ? (
            <div className="text-center py-6 text-slate-400 font-mono">Computing GNN attribution...</div>
          ) : !gnnReport ? (
            <div className="text-center py-6 text-slate-400 font-mono">GNN explanation not available</div>
          ) : (
            <>
              <div className="p-3 bg-slate-900/80 rounded-xl border border-slate-800 space-y-1.5">
                <div className="flex justify-between items-center font-bold">
                  <span className="text-slate-300">Target Node ID</span>
                  {gnnReport.node_id ? (
                    <Link
                      to={`/graph?entity_id=${encodeURIComponent(gnnReport.node_id)}&depth=2`}
                      className="font-mono text-cyan-400 hover:text-cyan-200 underline decoration-cyan-500/40 hover:decoration-cyan-300 transition-colors flex items-center gap-1"
                      title="Inspect 2-hop ego network in Graph Workbench"
                    >
                      <span>{gnnReport.node_id.slice(0, 16)}</span>
                      <span className="text-[10px]">🕸️</span>
                    </Link>
                  ) : (
                    <span className="font-mono text-slate-400">N/A</span>
                  )}
                </div>
                <div className="flex justify-between text-slate-400">
                  <span>2-Hop Subgraph</span>
                  <span className="font-semibold text-slate-200">{gnnReport.subgraph_nodes_count ?? 0} nodes • {gnnReport.subgraph_edges_count ?? 0} edges</span>
                </div>
                <p className="text-[11px] text-teal-300 font-medium pt-1.5 border-t border-slate-800">
                  {gnnReport.primary_driver_text}
                </p>
              </div>

              <div>
                <h4 className="text-[11px] font-bold text-slate-300 mb-2 uppercase tracking-wider flex items-center justify-between">
                  <span>Top Graph Edge Contributors</span>
                  <span className="text-[9px] font-mono text-slate-400">Click node to inspect ego graph</span>
                </h4>
                <div className="space-y-2">
                  {(gnnReport.top_contributing_edges ?? []).map((edge, i) => (
                    <div key={i} className="p-2.5 bg-slate-900/80 rounded-xl border border-slate-800 space-y-1.5">
                      <div className="flex items-center justify-between font-mono text-[11px] font-bold text-slate-200">
                        <span className="text-indigo-400 capitalize">{(edge.relationship_type ?? '').replace(/_/g, ' ')}</span>
                        <span className="text-emerald-400 font-bold">
                          {(edge.contribution_percentage ?? ((edge.weight ?? 0) * 100)).toFixed(0)}%
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-[10px] font-mono text-slate-400">
                        <Link
                          to={`/graph?entity_id=${encodeURIComponent(edge.source)}&depth=2`}
                          className="truncate max-w-[110px] text-cyan-400 hover:text-cyan-200 hover:underline"
                          title={`Trace graph for ${edge.source}`}
                        >
                          {edge.source}
                        </Link>
                        <span className="text-slate-500">➔</span>
                        <Link
                          to={`/graph?entity_id=${encodeURIComponent(edge.target)}&depth=2`}
                          className="truncate max-w-[110px] text-cyan-400 hover:text-cyan-200 hover:underline"
                          title={`Trace graph for ${edge.target}`}
                        >
                          {edge.target}
                        </Link>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </motion.div>
  );
}

