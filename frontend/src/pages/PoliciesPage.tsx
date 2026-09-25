import React, { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ShieldCheck,
  Plus,
  Pencil,
  Trash2,
  Zap,
  Play,
  FileCode,
  Sparkles,
  Database,
  Shield,
} from 'lucide-react';
import {
  useRules,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
  useTestRule,
  useAlerts,
} from '../api/queries';
import { useModalA11y } from '../hooks/useModalA11y';
import { BusinessRule, Alert } from '../api/types';

export interface AMLRuleTemplate {
  id: string;
  name: string;
  badge: string;
  action: string;
  description: string;
  condition: Record<string, any>;
  sampleTransaction: Record<string, any>;
}

export const AML_RULE_TEMPLATES: AMLRuleTemplate[] = [
  {
    id: 'velocity_spike',
    name: 'velocity_spike_burst_block',
    badge: 'Hızlı Ardışık İşlem (Velocity Spike)',
    action: 'BLOCK_TRANSACTION',
    description: '1 saat içinde 5\'ten fazla transfer ve tutar >= 1.000 EUR/USD olan hesapları otomatik engeller.',
    condition: {
      and: [
        { field: 'composite_risk_score', operator: '>=', value: 750 },
        { field: 'velocity_1h', operator: '>', value: 5 },
        { field: 'amount', operator: '>=', value: 1000 },
      ],
    },
    sampleTransaction: {
      transaction_id: 'TXN-BURST-0814',
      velocity_1h: 7,
      amount: 1850.0,
      currency: 'EUR',
      composite_risk_score: 790,
      country_code: 'TR',
      is_new_device: true,
    },
  },
  {
    id: 'sanctions_evasion',
    name: 'sanctions_jurisdiction_evasion_flag',
    badge: 'Yüksek Risk / Yaptırım',
    action: 'FLAG_CRITICAL',
    description: 'Yüksek Riskli Ülke / Yaptırım Atlama: FATF kara/gri listeli ülkeler (KP, IR, SY, RU, MM) veya ülke risk skoru > 0.85 olan transferleri kritik olarak işaretler.',
    condition: {
      or: [
        { field: 'country_code', operator: 'in', value: ['KP', 'IR', 'SY', 'RU', 'MM'] },
        { field: 'country_risk_score', operator: '>', value: 0.85 },
      ],
    },
    sampleTransaction: {
      transaction_id: 'TXN-SANCTION-9921',
      country_code: 'RU',
      country_risk_score: 0.94,
      amount: 32000.0,
      currency: 'USD',
      composite_risk_score: 875,
      velocity_1h: 2,
    },
  },
  {
    id: 'high_value_outlier',
    name: 'high_value_anomaly_review',
    badge: 'Olağandışı Büyük Tutar',
    action: 'HOLD_FOR_REVIEW',
    description: 'Olağandışı Büyük Tutar: 50.000 EUR/USD üzeri tutarlar ve kompozit risk skoru >= 750 olan transferleri Four-Eyes AML incelemesine alır.',
    condition: {
      and: [
        { field: 'amount', operator: '>=', value: 50000 },
        { field: 'composite_risk_score', operator: '>=', value: 750 },
      ],
    },
    sampleTransaction: {
      transaction_id: 'TXN-HIGHVAL-4410',
      amount: 75000.0,
      currency: 'EUR',
      composite_risk_score: 820,
      velocity_1h: 1,
      country_code: 'DE',
      is_new_device: false,
    },
  },
  {
    id: 'smurfing_structuring',
    name: 'smurfing_structuring_threshold_sar',
    badge: 'Smurfing / Yapılandırma',
    action: 'ESCALATE_TO_SAR',
    description: 'Smurfing / Yapılandırma: 10.000 zorunlu bildirim sınırının hemen altındaki (9.000 - 9.999) ve ardışık tekrarlanan şüpheli işlemleri doğrudan MASAK/FinCEN SAR sürecine yönlendirir.',
    condition: {
      and: [
        { field: 'amount', operator: 'between', min_value: 9000, max_value: 9999 },
        { field: 'velocity_1h', operator: '>=', value: 2 },
      ],
    },
    sampleTransaction: {
      transaction_id: 'TXN-SMURF-7732',
      amount: 9750.0,
      currency: 'EUR',
      velocity_1h: 3,
      composite_risk_score: 895,
      country_code: 'TR',
      is_new_device: true,
    },
  },
];

export default function PoliciesPage() {
  const { data: rules, refetch, isLoading } = useRules();
  const { data: alertsData } = useAlerts();
  const createRuleMutation = useCreateRule();
  const updateRuleMutation = useUpdateRule();
  const deleteRuleMutation = useDeleteRule();
  const testRuleMutation = useTestRule();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<BusinessRule | null>(null);

  const { containerRef } = useModalA11y<HTMLDivElement>({
    isOpen: isModalOpen,
    onClose: () => {
      setIsModalOpen(false);
      setEditingRule(null);
    },
    closeOnEscape: true,
    trapFocus: true,
    restoreFocus: true,
  });

  const [newRuleName, setNewRuleName] = useState('');
  const [newRuleAction, setNewRuleAction] = useState('BLOCK_TRANSACTION');
  const [newRuleConditionText, setNewRuleConditionText] = useState(
    JSON.stringify(AML_RULE_TEMPLATES[0]?.condition ?? {}, null, 2)
  );

  // Tester state
  const [testConditionText, setTestConditionText] = useState(
    JSON.stringify(AML_RULE_TEMPLATES[0]?.condition ?? {}, null, 2)
  );
  const [testTransactionText, setTestTransactionText] = useState(
    JSON.stringify(AML_RULE_TEMPLATES[0]?.sampleTransaction ?? {}, null, 2)
  );

  const [loadedAlertInfo, setLoadedAlertInfo] = useState<{
    id: string;
    txId: string;
    bank: string;
    score: number;
    severity: string;
  } | null>(null);

  const [testResult, setTestResult] = useState<{
    matches: boolean;
    message: string;
    matched_fields?: string[];
  } | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  // Top suspicious alerts list sorted by risk score or recency
  const suspiciousAlerts = useMemo(() => {
    if (!alertsData || alertsData.length === 0) return [];
    return [...alertsData]
      .filter((a) => a.risk_score >= 600 || a.severity === 'critical' || a.severity === 'high')
      .sort((a, b) => b.risk_score - a.risk_score);
  }, [alertsData]);

  const handleOpenCreateModal = () => {
    setEditingRule(null);
    setNewRuleName('');
    setNewRuleAction('BLOCK_TRANSACTION');
    setNewRuleConditionText(
      JSON.stringify(AML_RULE_TEMPLATES[0]?.condition ?? {}, null, 2)
    );
    setErrorMessage('');
    setIsModalOpen(true);
  };

  const handleOpenEditModal = (rule: BusinessRule) => {
    setEditingRule(rule);
    setNewRuleName(rule.rule_name);
    setNewRuleAction(rule.action);
    setNewRuleConditionText(JSON.stringify(rule.condition, null, 2));
    setErrorMessage('');
    setIsModalOpen(true);
  };

  const handleApplyTemplateToModal = (tpl: AMLRuleTemplate) => {
    if (!editingRule) {
      setNewRuleName(tpl.name);
    }
    setNewRuleAction(tpl.action);
    setNewRuleConditionText(JSON.stringify(tpl.condition, null, 2));
  };

  const handleApplyTemplateToTester = (tpl: AMLRuleTemplate) => {
    setTestConditionText(JSON.stringify(tpl.condition, null, 2));
    setTestTransactionText(JSON.stringify(tpl.sampleTransaction, null, 2));
    setErrorMessage('');
    setTestResult(null);
    setLoadedAlertInfo(null);
  };

  const handleLoadRuleToTester = (rule: BusinessRule) => {
    setTestConditionText(JSON.stringify(rule.condition, null, 2));
    setErrorMessage('');
    setTestResult(null);
    const testerElem = document.getElementById('dynamic-rule-tester-panel');
    if (testerElem && typeof testerElem.scrollIntoView === 'function') {
      testerElem.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const handleLoadAlertPayload = (alertItem?: Alert) => {
    const targetAlert = alertItem || suspiciousAlerts[0] || alertsData?.[0];
    if (!targetAlert) {
      setErrorMessage('Sistemde test için yüklenebilecek şüpheli alarm bulunamadı. Lütfen veri akışını kontrol edin.');
      return;
    }

    const payload = {
      transaction_id: targetAlert.transaction_id || targetAlert.id,
      bank_id: targetAlert.bank_id,
      composite_risk_score: targetAlert.risk_score,
      risk_level: targetAlert.severity,
      confidence: targetAlert.confidence,
      amount: (targetAlert as any).transaction_amount ?? (targetAlert as any).amount ?? 9850.0,
      currency: (targetAlert as any).currency ?? 'EUR',
      country_code: (targetAlert as any).country_code ?? 'NG',
      country_risk_score: targetAlert.risk_score > 700 ? 0.88 : 0.45,
      velocity_1h: (targetAlert as any).velocity ?? 6.4,
      is_new_device: true,
      reason_codes: targetAlert.reason_codes || ['RAPID_TRANSFER_BURST'],
      risk_factors: targetAlert.risk_factors || ['GEO_MISMATCH'],
    };

    setTestTransactionText(JSON.stringify(payload, null, 2));
    setLoadedAlertInfo({
      id: targetAlert.id,
      txId: targetAlert.transaction_id || targetAlert.id,
      bank: targetAlert.bank_id,
      score: targetAlert.risk_score,
      severity: targetAlert.severity,
    });
    setErrorMessage('');
    setTestResult(null);
  };

  const handleSubmitRule = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage('');
    try {
      const condition = JSON.parse(newRuleConditionText);
      if (editingRule) {
        await updateRuleMutation.mutateAsync({
          id: editingRule.id,
          rule_name: newRuleName,
          condition,
          action: newRuleAction,
          is_active: editingRule.is_active,
        });
      } else {
        await createRuleMutation.mutateAsync({
          rule_name: newRuleName,
          condition,
          action: newRuleAction,
          is_active: true,
        });
      }
      refetch();
      setIsModalOpen(false);
      setEditingRule(null);
      setNewRuleName('');
    } catch (err: any) {
      setErrorMessage(err.message || 'Invalid JSON format in condition AST');
    }
  };

  const handleToggleActive = async (id: string, currentStatus: boolean) => {
    await updateRuleMutation.mutateAsync({
      id,
      is_active: !currentStatus,
    });
    refetch();
  };

  const handleDeleteRule = async (id: string) => {
    if (window.confirm('Are you sure you want to delete this business rule?')) {
      await deleteRuleMutation.mutateAsync(id);
      refetch();
    }
  };

  const handleExecuteTest = async () => {
    setErrorMessage('');
    setTestResult(null);
    try {
      const condition = JSON.parse(testConditionText);
      const transaction = JSON.parse(testTransactionText);
      const res = await testRuleMutation.mutateAsync({
        condition,
        transaction,
      });
      setTestResult(res);
    } catch (err: any) {
      setErrorMessage(err.message || 'Invalid JSON syntax');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-[var(--color-border)] pb-6">
        <div>
          <div className="flex items-center gap-2 text-indigo-400 font-semibold text-xs tracking-wider uppercase mb-1">
            <ShieldCheck className="w-4 h-4" />
            Declarative AML & Fraud Policy Engine
          </div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white flex items-center gap-3">
            Policy Rules & Decisions
          </h1>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl mt-1">
            Configure declarative anti-fraud logic. Rules run dynamically at gateway entrypoints to allow, block, or flag transactions based on composite risk thresholds.
          </p>
        </div>
        <button
          onClick={handleOpenCreateModal}
          aria-label="Add Policy Rule"
          className="btn btn-primary flex items-center gap-2 shrink-0 whitespace-nowrap self-start sm:self-auto cursor-pointer shadow-lg shadow-indigo-600/25 active:scale-95 transition-all"
        >
          <Plus className="w-4 h-4" />
          <span>Add Policy Rule</span>
        </button>
      </div>

      {/* Main Grid: Active Rules + Dynamic Tester */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Rules List Panel */}
        <div className="lg:col-span-2 space-y-4">
          <div className="glass-card p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold flex items-center gap-2 text-white">
                <Shield className="w-5 h-5 text-indigo-400" />
                <span>Active Rules Registry</span>
              </h2>
              <span className="text-xs font-mono px-2.5 py-1 rounded-lg bg-indigo-950/60 text-indigo-300 border border-indigo-500/30 font-semibold">
                {rules?.length ?? 0} Configured Rules
              </span>
            </div>

            {isLoading ? (
              <div className="text-center py-12 text-[var(--color-text-muted)]">Loading rules...</div>
            ) : rules && rules.length > 0 ? (
              <div className="space-y-4">
                {rules.map((rule) => (
                  <motion.div
                    key={rule.id}
                    layoutId={rule.id}
                    className="p-5 rounded-xl bg-[var(--color-bg-card)] border border-[var(--color-border)] hover:border-slate-700 transition-all space-y-3.5 shadow-sm"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="font-bold text-white text-base">
                            {rule.rule_name}
                          </h3>
                          <span
                            className={`text-[10px] font-mono px-2 py-0.5 rounded-full font-bold uppercase tracking-wider border ${
                              rule.action === 'BLOCK_TRANSACTION'
                                ? 'bg-rose-500/15 text-rose-300 border-rose-500/30'
                                : rule.action === 'ESCALATE_TO_SAR'
                                ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
                                : rule.action === 'HOLD_FOR_REVIEW'
                                ? 'bg-purple-500/15 text-purple-300 border-purple-500/30'
                                : 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
                            }`}
                          >
                            Action: {rule.action}
                          </span>
                        </div>
                        {rule.description && (
                          <p className="text-xs text-[var(--color-text-muted)]">{rule.description}</p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 self-start sm:self-auto shrink-0 flex-wrap">
                        {/* Active Toggle */}
                        <label className="relative inline-flex items-center cursor-pointer mr-1">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={rule.is_active}
                            onChange={() => handleToggleActive(rule.id, rule.is_active)}
                            aria-label={`Toggle active state for ${rule.rule_name}`}
                          />
                          <div className="w-9 h-5 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                          <span className="ml-2 text-xs font-medium text-[var(--color-text-muted)]">
                            {rule.is_active ? 'Active' : 'Inactive'}
                          </span>
                        </label>

                        {/* Test In Tester Button */}
                        <button
                          onClick={() => handleLoadRuleToTester(rule)}
                          aria-label={`Test Rule ${rule.rule_name} in engine`}
                          className="px-2.5 py-1.5 rounded-lg bg-indigo-950/40 hover:bg-indigo-900/60 text-indigo-300 border border-indigo-500/30 hover:border-indigo-400 text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer"
                          title="Load this rule into the dynamic tester panel"
                        >
                          <Zap className="w-3.5 h-3.5" />
                          <span>Test In Tester</span>
                        </button>

                        {/* Edit Rule Button */}
                        <button
                          onClick={() => handleOpenEditModal(rule)}
                          aria-label={`Edit Rule ${rule.rule_name}`}
                          className="px-2.5 py-1.5 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700 hover:border-slate-600 text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer"
                          title="Edit rule name, condition AST, or action"
                        >
                          <Pencil className="w-3.5 h-3.5 text-indigo-400" />
                          <span>Edit</span>
                        </button>

                        {/* Delete Rule Button */}
                        <button
                          onClick={() => handleDeleteRule(rule.id)}
                          aria-label={`Delete Rule ${rule.rule_name}`}
                          className="p-1.5 rounded-lg hover:bg-rose-500/15 text-rose-400 border border-transparent hover:border-rose-500/30 transition-colors cursor-pointer"
                          title="Delete rule"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    <div className="bg-[var(--color-bg-secondary)] p-3.5 rounded-lg border border-[var(--color-border)] text-xs font-mono overflow-auto max-h-40">
                      <pre className="text-indigo-200">{JSON.stringify(rule.condition, null, 2)}</pre>
                    </div>
                  </motion.div>
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-xl">
                No custom business rules defined yet. System defaults to threshold-based alert triggers (score &ge; 600).
              </div>
            )}
          </div>
        </div>

        {/* Live Rule Tester Panel */}
        <div id="dynamic-rule-tester-panel" className="space-y-6">
          <div className="glass-card p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-3">
              <h2 className="text-lg font-semibold flex items-center gap-2 text-white">
                <Zap className="w-5 h-5 text-amber-400" />
                <span>Dynamic Rule Tester</span>
              </h2>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30 font-bold uppercase">
                Real-Time Sandbox
              </span>
            </div>

            <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
              Verify condition ASTs against live suspicious alerts or calibrated AML anomaly vectors in real time.
            </p>

            <div className="space-y-4">
              {/* Condition Section with Templates */}
              <div>
                <div className="flex items-center justify-between mb-1.5 flex-wrap gap-1">
                  <label htmlFor="test-condition-ast" className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <FileCode className="w-3.5 h-3.5 text-indigo-400" />
                    <span>Condition JSON AST</span>
                  </label>
                  <div className="flex items-center gap-1 flex-wrap">
                    <span className="text-[10px] text-slate-500 uppercase tracking-wider mr-1">Şablon Yükle:</span>
                    {AML_RULE_TEMPLATES.map((tpl) => (
                      <button
                        key={tpl.id}
                        type="button"
                        aria-label={`Tester Template ${tpl.id}`}
                        onClick={() => handleApplyTemplateToTester(tpl)}
                        className="text-[10px] font-medium px-2 py-0.5 rounded bg-slate-800 hover:bg-indigo-900/50 text-slate-300 hover:text-indigo-200 border border-slate-700 hover:border-indigo-500/40 transition-colors cursor-pointer"
                        title={tpl.description}
                      >
                        {tpl.badge.split('(')[0]?.trim() ?? tpl.badge}
                      </button>
                    ))}
                  </div>
                </div>
                <textarea
                  id="test-condition-ast"
                  aria-label="Condition JSON AST"
                  className="w-full h-36 p-2.5 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono text-indigo-200 focus:outline-none focus:border-indigo-500 shadow-inner"
                  value={testConditionText}
                  onChange={(e) => setTestConditionText(e.target.value)}
                />
              </div>

              {/* Transaction Payload Section with Load Live Alert */}
              <div className="space-y-2">
                <div className="flex items-center justify-between flex-wrap gap-1.5">
                  <label htmlFor="test-transaction-payload" className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                    <Database className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Transaction Payload</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => handleLoadAlertPayload()}
                    aria-label="Son Şüpheli Alarmı Yükle"
                    className="text-xs font-semibold text-emerald-300 bg-emerald-950/60 hover:bg-emerald-900/80 border border-emerald-500/40 hover:border-emerald-400 px-2.5 py-1 rounded-lg transition-all flex items-center gap-1.5 shadow-sm cursor-pointer active:scale-95"
                    title="Sistemdeki en yüksek riskli gerçek alarmı test kutusuna yükler"
                  >
                    <Zap className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Son Şüpheli Alarmı Yükle</span>
                  </button>
                </div>

                {loadedAlertInfo && (
                  <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-300 text-xs">
                    <div className="flex items-center gap-2 truncate">
                      <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0" />
                      <span className="truncate">
                        Canlı Alarm Yüklendi: <strong className="font-mono">{loadedAlertInfo.txId}</strong> (Skor: {loadedAlertInfo.score}, Banka: {loadedAlertInfo.bank})
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setLoadedAlertInfo(null)}
                      className="text-slate-400 hover:text-white ml-2 text-xs cursor-pointer"
                    >
                      ✕
                    </button>
                  </div>
                )}

                {suspiciousAlerts.length > 1 && (
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-[11px] text-slate-400 shrink-0">Veya Listeden Seç:</span>
                    <select
                      aria-label="Şüpheli Alarm Seçimi"
                      className="w-full text-xs p-1.5 rounded bg-slate-900 border border-slate-700 text-slate-200 focus:outline-none focus:border-emerald-500"
                      onChange={(e) => {
                        const target = suspiciousAlerts.find((a) => a.id === e.target.value);
                        if (target) handleLoadAlertPayload(target);
                      }}
                      defaultValue=""
                    >
                      <option value="" disabled>
                        Şüpheli alarmlardan birini seçin... ({suspiciousAlerts.length} alarm)
                      </option>
                      {suspiciousAlerts.slice(0, 5).map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.transaction_id || a.id} — Skor: {a.risk_score} ({a.bank_id})
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                <textarea
                  id="test-transaction-payload"
                  aria-label="Mock Transaction Payload"
                  className="w-full h-36 p-2.5 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono text-emerald-200 focus:outline-none focus:border-indigo-500 shadow-inner"
                  value={testTransactionText}
                  onChange={(e) => setTestTransactionText(e.target.value)}
                />
              </div>

              <button
                onClick={handleExecuteTest}
                disabled={testRuleMutation.isPending}
                aria-label={testRuleMutation.isPending ? 'Evaluating...' : 'Run Evaluation Test'}
                className="w-full btn btn-primary flex items-center justify-center gap-2 py-2.5 font-bold cursor-pointer shadow-md shadow-indigo-600/30"
              >
                <Play className="w-4 h-4" />
                <span>{testRuleMutation.isPending ? 'Evaluating...' : 'Run Evaluation Test'}</span>
              </button>

              {errorMessage && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                  {errorMessage}
                </div>
              )}

              {testResult !== null && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`p-4 rounded-xl border flex items-start gap-3 ${
                    testResult.matches
                      ? 'bg-rose-950/30 border-rose-500/30 text-rose-300'
                      : 'bg-emerald-950/30 border-emerald-500/30 text-emerald-300'
                  }`}
                >
                  <div className="text-xl shrink-0 mt-0.5">
                    {testResult.matches ? '🚨' : '✅'}
                  </div>
                  <div className="space-y-1.5 flex-1 min-w-0">
                    <div className="font-bold text-sm">
                      {testResult.matches ? 'Trigger Condition Met (Kural Tetiklendi)' : 'Passed Cleanly (Temiz Geçti)'}
                    </div>
                    <div className="text-xs opacity-90 leading-relaxed font-mono">
                      {testResult.message}
                    </div>
                    {testResult.matched_fields && testResult.matched_fields.length > 0 && (
                      <div className="flex items-center gap-1.5 flex-wrap pt-1">
                        <span className="text-[10px] text-slate-400 uppercase font-sans">Eşleşen Alanlar:</span>
                        {testResult.matched_fields.map((f) => (
                          <span
                            key={f}
                            className="text-[10px] font-mono px-2 py-0.5 rounded bg-rose-500/20 text-rose-200 border border-rose-500/30 font-semibold"
                          >
                            {f}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </motion.div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Create / Edit Rule Modal */}
      <AnimatePresence>
        {isModalOpen && (
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <motion.div
              ref={containerRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby="rule-modal-title"
              tabIndex={-1}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="glass-card w-full max-w-xl p-6 space-y-4 focus:outline-none max-h-[90vh] overflow-y-auto"
            >
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-3">
                <div className="flex items-center gap-2">
                  <div className="p-2 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
                    {editingRule ? <Pencil className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                  </div>
                  <div>
                    <h3 id="rule-modal-title" className="text-lg font-bold text-white">
                      {editingRule ? `Edit Policy Rule: ${editingRule.rule_name}` : 'Create Dynamic Policy Rule'}
                    </h3>
                    <p className="text-xs text-slate-400">
                      {editingRule
                        ? 'Update declarative AST conditions and triggered policy actions.'
                        : 'Define declarative screening conditions evaluated on transactions in real time.'}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => {
                    setIsModalOpen(false);
                    setEditingRule(null);
                  }}
                  aria-label="Close modal"
                  className="text-slate-400 hover:text-white text-lg p-1 focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded cursor-pointer"
                >
                  ✕
                </button>
              </div>

              {/* Pre-built AML Rule Templates Section */}
              <div className="space-y-2 bg-slate-900/60 p-3.5 rounded-xl border border-slate-800">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-indigo-300 flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                    <span>Hazır AML Kural Şablonları (1-Tıkla Uygula)</span>
                  </span>
                  <span className="text-[10px] text-slate-500">AST Otomatik Doldurulur</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {AML_RULE_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl.id}
                      type="button"
                      aria-label={`Apply Template ${tpl.id}`}
                      onClick={() => handleApplyTemplateToModal(tpl)}
                      className="text-left p-2.5 rounded-lg border border-slate-800 hover:border-indigo-500/60 bg-slate-950/60 hover:bg-indigo-950/20 transition-all text-xs cursor-pointer group"
                    >
                      <div className="font-semibold text-white group-hover:text-indigo-300 flex items-center justify-between">
                        <span className="truncate">{tpl.badge}</span>
                        <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 uppercase">
                          {tpl.action.split('_')[0]}
                        </span>
                      </div>
                      <p className="text-[11px] text-slate-400 mt-1 line-clamp-1">{tpl.description}</p>
                    </button>
                  ))}
                </div>
              </div>

              <form onSubmit={handleSubmitRule} className="space-y-4">
                <div>
                  <label htmlFor="modal-rule-name-input" className="block text-xs font-semibold text-slate-300 mb-1">
                    Rule Name
                  </label>
                  <input
                    id="modal-rule-name-input"
                    aria-label="Modal Rule Name"
                    type="text"
                    required
                    placeholder="e.g. suspicious_high_value_block"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-sm text-white focus:outline-none focus:border-indigo-500"
                    value={newRuleName}
                    onChange={(e) => setNewRuleName(e.target.value)}
                  />
                </div>

                <div>
                  <label htmlFor="modal-rule-action-select" className="block text-xs font-semibold text-slate-300 mb-1">
                    Triggered Action
                  </label>
                  <select
                    id="modal-rule-action-select"
                    aria-label="Modal Triggered Action"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-sm text-white focus:outline-none focus:border-indigo-500"
                    value={newRuleAction}
                    onChange={(e) => setNewRuleAction(e.target.value)}
                  >
                    <option value="BLOCK_TRANSACTION">BLOCK_TRANSACTION (Doğrudan İşlemi Durdur)</option>
                    <option value="ESCALATE_TO_SAR">ESCALATE_TO_SAR (MASAK/FinCEN SAR Sevk)</option>
                    <option value="HOLD_FOR_REVIEW">HOLD_FOR_REVIEW (Four-Eyes İnceleme Bekletme)</option>
                    <option value="FLAG_CRITICAL">FLAG_CRITICAL (Kritik Öncelikli Alarm)</option>
                    <option value="FLAG_HIGH_RISK">FLAG_HIGH_RISK (Yüksek Riskli İşlem İşareti)</option>
                    <option value="REQUIRE_MFA">REQUIRE_MFA (Ekstra Güçlü Kimlik Doğrulama)</option>
                    <option value="ALLOW">ALLOW (Açık İzin / Güvenli İstisna)</option>
                  </select>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label htmlFor="modal-rule-condition-textarea" className="text-xs font-semibold text-slate-300">
                      Condition JSON AST
                    </label>
                    <span className="text-[10px] text-slate-500 font-mono">and/or/field/operator/value</span>
                  </div>
                  <textarea
                    id="modal-rule-condition-textarea"
                    aria-label="Modal Condition JSON AST"
                    required
                    rows={8}
                    className="w-full p-3 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono text-indigo-200 focus:outline-none focus:border-indigo-500"
                    value={newRuleConditionText}
                    onChange={(e) => setNewRuleConditionText(e.target.value)}
                  />
                </div>

                {errorMessage && (
                  <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                    {errorMessage}
                  </div>
                )}

                <div className="flex justify-end gap-3 pt-3 border-t border-[var(--color-border)]">
                  <button
                    type="button"
                    onClick={() => {
                      setIsModalOpen(false);
                      setEditingRule(null);
                    }}
                    className="btn hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createRuleMutation.isPending || updateRuleMutation.isPending}
                    className="btn btn-primary cursor-pointer"
                  >
                    {createRuleMutation.isPending || updateRuleMutation.isPending
                      ? 'Saving...'
                      : editingRule
                      ? 'Save Changes'
                      : 'Register Rule'}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
