import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  useRules,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
  useTestRule,
} from '../api/queries';
import { useModalA11y } from '../hooks/useModalA11y';

export default function PoliciesPage() {
  const { data: rules, refetch, isLoading } = useRules();
  const createRuleMutation = useCreateRule();
  const updateRuleMutation = useUpdateRule();
  const deleteRuleMutation = useDeleteRule();
  const testRuleMutation = useTestRule();

  const [isModalOpen, setIsModalOpen] = useState(false);

  const { containerRef } = useModalA11y<HTMLDivElement>({
    isOpen: isModalOpen,
    onClose: () => setIsModalOpen(false),
    closeOnEscape: true,
    trapFocus: true,
    restoreFocus: true,
  });
  const [newRuleName, setNewRuleName] = useState('');
  const [newRuleAction, setNewRuleAction] = useState('BLOCK_TRANSACTION');
  const [newRuleConditionText, setNewRuleConditionText] = useState(
    JSON.stringify(
      {
        and: [
          { field: 'composite_risk_score', operator: '>=', value: 830 },
          { field: 'country_code', operator: 'in', value: ['NG', 'RU', 'PH'] },
        ],
      },
      null,
      2
    )
  );

  // Tester state
  const [testConditionText, setTestConditionText] = useState(
    JSON.stringify(
      {
        and: [
          { field: 'composite_risk_score', operator: '>=', value: 830 },
          { field: 'country_code', operator: 'in', value: ['NG', 'RU', 'PH'] },
        ],
      },
      null,
      2
    )
  );
  const [testTransactionText, setTestTransactionText] = useState(
    JSON.stringify(
      {
        composite_risk_score: 850,
        country_code: 'NG',
        velocity: 6.2,
        transaction_amount: 1200.0,
      },
      null,
      2
    )
  );

  const [testResult, setTestResult] = useState<{ matches: boolean; message: string } | null>(null);
  const [errorMessage, setErrorMessage] = useState('');

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage('');
    try {
      const condition = JSON.parse(newRuleConditionText);
      await createRuleMutation.mutateAsync({
        rule_name: newRuleName,
        condition,
        action: newRuleAction,
        is_active: true,
      });
      refetch();
      setIsModalOpen(false);
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
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold gradient-text mb-1">Policy Rules & Decisions</h1>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl">
            Configure declarative anti-fraud logic. Rules run dynamically at gateway entrypoints to allow, block, or flag transactions based on composite risk thresholds.
          </p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="btn btn-primary flex items-center gap-2 shrink-0 whitespace-nowrap self-start sm:self-auto"
        >
          <span>➕</span> Add Policy Rule
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Rules List Panel */}
        <div className="lg:col-span-2 space-y-4">
          <div className="glass-card p-6">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <span>🛡️</span> Active Rules Registry
            </h2>

            {isLoading ? (
              <div className="text-center py-12 text-[var(--color-text-muted)]">Loading rules...</div>
            ) : rules && rules.length > 0 ? (
              <div className="space-y-4">
                {rules.map((rule) => (
                  <motion.div
                    key={rule.id}
                    layoutId={rule.id}
                    className="p-4 rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] space-y-3"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="font-bold text-[var(--color-text-primary)]">
                          {rule.rule_name}
                        </h3>
                        <span className="text-[10px] text-[var(--color-text-muted)] uppercase tracking-wider bg-[var(--color-bg-secondary)] px-2 py-0.5 rounded border border-[var(--color-border)]">
                          Action: {rule.action}
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <label className="relative inline-flex items-center cursor-pointer">
                          <input
                            type="checkbox"
                            className="sr-only peer"
                            checked={rule.is_active}
                            onChange={() => handleToggleActive(rule.id, rule.is_active)}
                          />
                          <div className="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                          <span className="ml-2 text-xs font-medium text-[var(--color-text-muted)]">
                            {rule.is_active ? 'Active' : 'Inactive'}
                          </span>
                        </label>
                        <button
                          onClick={() => handleDeleteRule(rule.id)}
                          className="p-1.5 rounded hover:bg-red-500/10 text-red-400 border border-transparent hover:border-red-500/30 transition-colors"
                          title="Delete rule"
                        >
                          🗑️
                        </button>
                      </div>
                    </div>

                    <div className="bg-[var(--color-bg-secondary)] p-3 rounded border border-[var(--color-border)] text-xs font-mono overflow-auto max-h-40">
                      <pre>{JSON.stringify(rule.condition, null, 2)}</pre>
                    </div>
                  </motion.div>
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-lg">
                No custom business rules defined yet. System defaults to threshold-based alert triggers (score &ge; 600).
              </div>
            )}
          </div>
        </div>

        {/* Live Rule Tester Panel */}
        <div className="space-y-6">
          <div className="glass-card p-6 space-y-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <span>⚡</span> Dynamic Rule Tester
            </h2>
            <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
              Verify condition checks against mock transaction variables in real time.
            </p>

            <div className="space-y-3">
              <div>
                <label htmlFor="test-condition-ast" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                  Condition JSON AST
                </label>
                <textarea
                  id="test-condition-ast"
                  aria-label="Condition JSON AST"
                  className="w-full h-40 p-2.5 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono focus:outline-none focus:border-indigo-500"
                  value={testConditionText}
                  onChange={(e) => setTestConditionText(e.target.value)}
                />
              </div>

              <div>
                <label htmlFor="test-transaction-payload" className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
                  Mock Transaction Payload
                </label>
                <textarea
                  id="test-transaction-payload"
                  aria-label="Mock Transaction Payload"
                  className="w-full h-40 p-2.5 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono focus:outline-none focus:border-indigo-500"
                  value={testTransactionText}
                  onChange={(e) => setTestTransactionText(e.target.value)}
                />
              </div>

              <button
                onClick={handleExecuteTest}
                disabled={testRuleMutation.isPending}
                className="w-full btn btn-primary flex items-center justify-center gap-2 py-2"
              >
                {testRuleMutation.isPending ? 'Evaluating...' : '⚡ Run Evaluation Test'}
              </button>

              {errorMessage && (
                <div className="p-3 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                  {errorMessage}
                </div>
              )}

              {testResult !== null && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className={`p-4 rounded-lg border flex items-start gap-3 ${
                    testResult.matches
                      ? 'bg-green-500/10 border-green-500/20 text-green-400'
                      : 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400'
                  }`}
                >
                  <span className="text-xl">
                    {testResult.matches ? '🚨' : '✅'}
                  </span>
                  <div>
                    <div className="font-semibold text-sm">
                      {testResult.matches ? 'Trigger Condition Met' : 'Passed Cleanly'}
                    </div>
                    <div className="text-xs mt-0.5 opacity-80">
                      {testResult.message}
                    </div>
                  </div>
                </motion.div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Create Modal */}
      <AnimatePresence>
        {isModalOpen && (
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <motion.div
              ref={containerRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby="create-rule-modal-title"
              tabIndex={-1}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="glass-card w-full max-w-lg p-6 space-y-4 focus:outline-none"
            >
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-3">
                <h3 id="create-rule-modal-title" className="text-lg font-bold text-[var(--color-text-primary)]">
                  Create Dynamic Policy Rule
                </h3>
                <button
                  onClick={() => setIsModalOpen(false)}
                  aria-label="Close modal"
                  className="text-[var(--color-text-muted)] hover:text-white text-lg p-1 focus:outline-none focus:ring-2 focus:ring-indigo-400 rounded"
                >
                  ✕
                </button>
              </div>

              <form onSubmit={handleCreateRule} className="space-y-4">
                <div>
                  <label htmlFor="new-rule-name-input" className="block text-xs font-semibold text-[var(--color-text-muted)] mb-1">
                    Rule Name
                  </label>
                  <input
                    id="new-rule-name-input"
                    type="text"
                    required
                    placeholder="e.g. suspicious_high_value_block"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-sm text-white focus:outline-none focus:border-indigo-500"
                    value={newRuleName}
                    onChange={(e) => setNewRuleName(e.target.value)}
                  />
                </div>

                <div>
                  <label htmlFor="new-rule-action-select" className="block text-xs font-semibold text-[var(--color-text-muted)] mb-1">
                    Triggered Action
                  </label>
                  <select
                    id="new-rule-action-select"
                    className="w-full px-3 py-2 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-sm text-white focus:outline-none focus:border-indigo-500"
                    value={newRuleAction}
                    onChange={(e) => setNewRuleAction(e.target.value)}
                  >
                    <option value="BLOCK_TRANSACTION">BLOCK_TRANSACTION</option>
                    <option value="FLAG_SUSPICIOUS">FLAG_SUSPICIOUS</option>
                    <option value="REVIEW_CASE">REVIEW_CASE</option>
                  </select>
                </div>

                <div>
                  <label htmlFor="new-rule-condition-textarea" className="block text-xs font-semibold text-[var(--color-text-muted)] mb-1">
                    Condition JSON AST
                  </label>
                  <textarea
                    id="new-rule-condition-textarea"
                    required
                    rows={8}
                    className="w-full p-3 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                    value={newRuleConditionText}
                    onChange={(e) => setNewRuleConditionText(e.target.value)}
                  />
                </div>

                {errorMessage && (
                  <div className="p-3 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                    {errorMessage}
                  </div>
                )}

                <div className="flex justify-end gap-3 pt-3 border-t border-[var(--color-border)]">
                  <button
                    type="button"
                    onClick={() => setIsModalOpen(false)}
                    className="btn hover:bg-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createRuleMutation.isPending}
                    className="btn btn-primary"
                  >
                    {createRuleMutation.isPending ? 'Creating...' : 'Register Rule'}
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
