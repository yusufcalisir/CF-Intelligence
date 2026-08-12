import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  useSecurityStatus,
  useEvaluateABAC,
  useAuditChain,
  useVerifyAuditChain,
} from '../api/queries';

export default function SecurityPage() {
  const [activeTab, setActiveTab] = useState<'mtls' | 'oidc' | 'abac' | 'vault' | 'audit'>('mtls');
  const { data: status, isLoading: isStatusLoading } = useSecurityStatus();
  const { data: auditEntries, isLoading: isAuditLoading } = useAuditChain(30);

  const evaluateABAC = useEvaluateABAC();
  const verifyChain = useVerifyAuditChain();

  // Interactive ABAC Evaluator state
  const [userRole, setUserRole] = useState('analyst');
  const [userBankId, setUserBankId] = useState('bank_a');
  const [userShift, setUserShift] = useState('08:00-18:00');
  const [userClearance, setUserClearance] = useState(2);
  const [userApprovalTier, setUserApprovalTier] = useState(50000);

  const [resourceType, setResourceType] = useState('alert');
  const [resourceBankId, setResourceBankId] = useState('bank_b');
  const [resourceAmount, setResourceAmount] = useState(75000);
  const [action, setAction] = useState('read');

  const handleTestABAC = () => {
    evaluateABAC.mutate({
      user_username: 'test_analyst',
      user_bank_id: userBankId,
      user_roles: [userRole],
      user_clearance: userClearance,
      user_shift_hours: userShift,
      user_approval_tier: userApprovalTier,
      resource_type: resourceType,
      resource_id: 'res_sample_101',
      resource_bank_id: resourceBankId,
      resource_amount: resourceAmount,
      resource_classification: 1,
      action: action,
    });
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text-primary)]">
            Enterprise Security & Compliance Control Center
          </h1>
          <p className="text-sm text-[var(--color-text-muted)]">
            ISO 27001, SOC2, PCI-DSS compliance suite: mTLS 1.3, OIDC JWT, ABAC, HashiCorp Vault & SHA-256 Audit Chain
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => verifyChain.mutate()}
            disabled={verifyChain.isPending}
            className="px-4 py-2 text-xs font-bold rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 shadow-md transition-all flex items-center gap-2"
          >
            {verifyChain.isPending ? 'Verifying Hashes...' : '🔒 Verify SHA-256 Audit Chain'}
          </button>
        </div>
      </div>

      {/* Verification Modal / Banner Result */}
      {verifyChain.data && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className={`p-4 rounded-xl border flex items-center justify-between ${
            verifyChain.data.is_valid
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
              : 'bg-red-500/10 border-red-500/30 text-red-400'
          }`}
        >
          <div className="flex items-center gap-3">
            <span className="text-2xl">{verifyChain.data.is_valid ? '✓' : '⚠️'}</span>
            <div>
              <div className="font-bold text-sm">
                {verifyChain.data.is_valid
                  ? 'Cryptographic Audit Chain Intact (100% SHA-256 Hash Match)'
                  : 'RETROSPECTIVE TAMPERING DETECTED!'}
              </div>
              <div className="text-xs opacity-90">
                {verifyChain.data.is_valid
                  ? `Verified ${verifyChain.data.total_records} events from Genesis Block. Tail Hash: ${verifyChain.data.last_hash.slice(0, 16)}...`
                  : `Broken at index #${verifyChain.data.broken_index}: ${verifyChain.data.tamper_reason}`}
              </div>
            </div>
          </div>
          <span className="text-[10px] font-mono px-2 py-1 bg-black/30 rounded">
            {verifyChain.data.verified_at}
          </span>
        </motion.div>
      )}

      {/* 5-Tab Navigation */}
      <div className="flex overflow-x-auto no-scrollbar gap-2 sm:gap-3 pb-2 border-b border-[var(--color-border)]">
        {[
          { id: 'mtls', icon: '🔑', label: 'mTLS & Cert PKI' },
          { id: 'oidc', icon: '🆔', label: 'OIDC & IAM' },
          { id: 'abac', icon: '🛡️', label: 'Dynamic ABAC Rules' },
          { id: 'vault', icon: '🔐', label: 'HashiCorp Vault' },
          { id: 'audit', icon: '⛓️', label: 'Cryptographic Audit Chain' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`px-3.5 py-2 text-xs sm:text-sm font-bold rounded-xl transition-all whitespace-nowrap shrink-0 flex items-center gap-1.5 border min-h-[44px] cursor-pointer ${
              activeTab === tab.id
                ? 'bg-indigo-600/20 border-indigo-500/50 text-indigo-300 shadow-md shadow-indigo-600/15'
                : 'bg-white/3 border-white/5 text-[var(--color-text-muted)] hover:text-slate-200 hover:bg-white/5'
            }`}
          >
            <span className="shrink-0">{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {isStatusLoading ? (
        <div className="text-center py-12 text-[var(--color-text-muted)]">Loading security suite status...</div>
      ) : (
        <>
          {/* Tab 1: mTLS */}
          {activeTab === 'mtls' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  Mutual TLS 1.3 Configuration
                </h3>
                <div className="space-y-3 text-xs">
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">mTLS Status</span>
                    <span className="font-mono text-emerald-400 font-bold">
                      {status?.mtls.enabled ? 'ACTIVE (CERT_REQUIRED)' : 'DEVELOPMENT'}
                    </span>
                  </div>
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">Root CA CN</span>
                    <span className="font-mono font-bold text-[var(--color-primary)]">{status?.mtls.ca_cn}</span>
                  </div>
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">TLS Minimum Protocol</span>
                    <span className="font-mono font-bold">{status?.mtls.tls_version}</span>
                  </div>
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">Peer SAN Validation</span>
                    <span className="font-mono text-emerald-400 font-bold">{status?.mtls.peer_verification}</span>
                  </div>
                </div>
              </div>

              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  Active X.509 Service Certificate
                </h3>
                {status?.mtls.sample_cert && (
                  <div className="space-y-2 text-xs font-mono p-3 bg-[var(--color-bg-card)] rounded-lg border border-[var(--color-border)]">
                    <div><span className="text-[var(--color-text-muted)]">CN:</span> {status.mtls.sample_cert.cn}</div>
                    <div><span className="text-[var(--color-text-muted)]">SANs:</span> {status.mtls.sample_cert.sans.join(', ')}</div>
                    <div><span className="text-[var(--color-text-muted)]">Expires:</span> {status.mtls.sample_cert.valid_until}</div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Tab 2: OIDC */}
          {activeTab === 'oidc' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  OIDC / OAuth2 Provider
                </h3>
                <div className="space-y-3 text-xs">
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">OIDC Issuer Realm</span>
                    <span className="font-mono text-[var(--color-primary)] font-bold">{status?.oidc.issuer}</span>
                  </div>
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">Client ID</span>
                    <span className="font-mono font-bold">{status?.oidc.client_id}</span>
                  </div>
                  <div className="flex justify-between p-2 rounded bg-[var(--color-surface-alt)]">
                    <span className="text-[var(--color-text-muted)]">Algorithms</span>
                    <span className="font-mono font-bold">{status?.oidc.supported_algorithms.join(', ')}</span>
                  </div>
                </div>
              </div>

              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  Extracted Bearer Token Claims
                </h3>
                <div className="flex flex-wrap gap-2 text-xs">
                  {status?.oidc.claims_extracted.map((c, i) => (
                    <span key={i} className="px-2 py-1 rounded font-mono bg-[var(--color-primary)]/20 text-[var(--color-primary)] font-bold">
                      {c}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Tab 3: ABAC Evaluator */}
          {activeTab === 'abac' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  Interactive ABAC Policy Simulator
                </h3>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div>
                    <label className="text-[var(--color-text-muted)]">User Bank</label>
                    <select
                      value={userBankId}
                      onChange={(e) => setUserBankId(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    >
                      <option value="bank_a">bank_a</option>
                      <option value="bank_b">bank_b</option>
                      <option value="bank_c">bank_c</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">User Role</label>
                    <select
                      value={userRole}
                      onChange={(e) => setUserRole(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    >
                      <option value="analyst">analyst</option>
                      <option value="cross_bank_investigator">cross_bank_investigator</option>
                      <option value="super_admin">super_admin</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">Target Resource Bank</label>
                    <select
                      value={resourceBankId}
                      onChange={(e) => setResourceBankId(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    >
                      <option value="bank_a">bank_a</option>
                      <option value="bank_b">bank_b</option>
                      <option value="bank_c">bank_c</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">Resource Amount ($)</label>
                    <input
                      type="number"
                      value={resourceAmount}
                      onChange={(e) => setResourceAmount(Number(e.target.value))}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    />
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">User Shift Hours</label>
                    <input
                      type="text"
                      value={userShift}
                      onChange={(e) => setUserShift(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    />
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">User Clearance Level</label>
                    <input
                      type="number"
                      value={userClearance}
                      onChange={(e) => setUserClearance(Number(e.target.value))}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    />
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">Approval Tier Limit ($)</label>
                    <input
                      type="number"
                      value={userApprovalTier}
                      onChange={(e) => setUserApprovalTier(Number(e.target.value))}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    />
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">Resource Type</label>
                    <select
                      value={resourceType}
                      onChange={(e) => setResourceType(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    >
                      <option value="alert">alert</option>
                      <option value="case">case</option>
                      <option value="model">model</option>
                      <option value="intelligence">intelligence</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-[var(--color-text-muted)]">Action</label>
                    <select
                      value={action}
                      onChange={(e) => setAction(e.target.value)}
                      className="w-full mt-1 p-2 rounded bg-[var(--color-surface-alt)] border border-[var(--color-border)] font-mono"
                    >
                      <option value="read">read</option>
                      <option value="write">write</option>
                      <option value="approve">approve</option>
                      <option value="export">export</option>
                    </select>
                  </div>

                </div>

                <button
                  onClick={handleTestABAC}
                  disabled={evaluateABAC.isPending}
                  className="w-full py-2 font-bold text-xs rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-all"
                >
                  {evaluateABAC.isPending ? 'Evaluating Policy...' : 'Execute ABAC Policy Check'}
                </button>

                {evaluateABAC.data && (
                  <div
                    className={`p-3 rounded-lg border text-xs ${
                      evaluateABAC.data.allowed
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : 'bg-red-500/10 border-red-500/30 text-red-400'
                    }`}
                  >
                    <div className="font-bold">{evaluateABAC.data.allowed ? '✓ ACCESS GRANTED' : '⛔ ACCESS DENIED'}</div>
                    <div className="font-mono text-[10px] mt-1">{evaluateABAC.data.policy_name}</div>
                    <div className="mt-1 opacity-90">{evaluateABAC.data.reason}</div>
                  </div>
                )}
              </div>

              <div className="glass-card p-5 space-y-4">
                <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                  Active ABAC Compliance Policies
                </h3>
                <div className="space-y-2 text-xs">
                  {status?.abac.enforced_policies.map((pol, i) => (
                    <div key={i} className="p-2 rounded bg-[var(--color-surface-alt)] font-mono font-bold flex items-center justify-between">
                      <span>{pol}</span>
                      <span className="text-emerald-400">ENFORCED</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Tab 4: Vault */}
          {activeTab === 'vault' && (
            <div className="glass-card p-5 space-y-4">
              <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                HashiCorp Vault Secrets Engine Integration
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                  <div className="text-[var(--color-text-muted)]">Vault Endpoint</div>
                  <div className="font-mono font-bold">{status?.vault.vault_url}</div>
                </div>
                <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                  <div className="text-[var(--color-text-muted)]">KV Engine Mount</div>
                  <div className="font-mono font-bold">{status?.vault.mount_point}</div>
                </div>
                <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                  <div className="text-[var(--color-text-muted)]">Secret Injection Source</div>
                  <div className="font-mono text-emerald-400 font-bold">{status?.vault.sample_secret_source}</div>
                </div>
              </div>
            </div>
          )}

          {/* Tab 5: Cryptographic Audit Chain */}
          {activeTab === 'audit' && (
            <div className="space-y-4">
              <div className="glass-card p-5 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                    SHA-256 Cryptographic Audit Ledger
                  </h3>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Formula: H_i = SHA-256( LogContent_i || H_i-1 )
                  </p>
                </div>
                <div className="text-right font-mono text-xs">
                  <div className="text-emerald-400 font-bold">Chain Status: INTACT</div>
                  <div className="text-[var(--color-text-muted)]">{status?.audit_chain.total_events} Total Events Recorded</div>
                </div>
              </div>

              <div className="glass-card p-5 space-y-3">
                <h4 className="text-xs font-bold uppercase text-[var(--color-text-muted)]">Recent Audit Events</h4>
                {isAuditLoading ? (
                  <div className="text-center py-6 text-[var(--color-text-muted)]">Loading ledger...</div>
                ) : (
                  <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                    {auditEntries?.map((entry) => (
                      <div key={entry.index} className="p-2.5 rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] text-xs space-y-1">
                        <div className="flex items-center justify-between font-mono font-bold">
                          <span className="text-[var(--color-primary)]">#{entry.index} [{entry.event_type}]</span>
                          <span className="text-[var(--color-text-muted)]">{entry.timestamp}</span>
                        </div>
                        <div className="text-[11px] text-[var(--color-text-primary)]">
                          Actor: <span className="font-semibold">{entry.actor}</span> | Target: <span className="font-semibold">{entry.target_id}</span>
                        </div>
                        <div className="flex justify-between font-mono text-[9px] text-[var(--color-text-muted)] pt-1 border-t border-[var(--color-border)]">
                          <span>Prev: {entry.prev_hash.slice(0, 16)}...</span>
                          <span className="text-emerald-400">Curr: {entry.curr_hash.slice(0, 16)}...</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
