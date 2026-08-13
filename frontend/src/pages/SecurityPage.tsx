import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  useSecurityStatus,
  useEvaluateABAC,
  useAuditChain,
  useVerifyAuditChain,
} from '../api/queries';

export default function SecurityPage() {
  const [activeTab, setActiveTab] = useState<'mtls' | 'oidc' | 'abac' | 'vault' | 'audit' | 'secagg'>('mtls');
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

  // P2P SecAgg panel state (simulates live key broadcast status for current round)
  interface SecAggNode { id: string; label: string; x: number; y: number; broadcast: boolean; pkHex: string; hmacHex: string; }
  const [secaggRound] = useState(42);
  const secaggNodes: SecAggNode[] = [
    { id: 'bank_alpha', label: 'Alpha Intl.', x: 160, y: 60,  broadcast: true,  pkHex: 'a3f8c2..e14d', hmacHex: '9b72dd..3f01' },
    { id: 'bank_beta',  label: 'Beta Corp.',  x: 300, y: 160, broadcast: true,  pkHex: '5c19ab..8e72', hmacHex: 'cc40fa..d8b3' },
    { id: 'bank_gamma', label: 'Gamma Fin.',  x: 160, y: 260, broadcast: true,  pkHex: '1da472..5c9f', hmacHex: '2e8531..a167' },
    { id: 'bank_delta', label: 'Delta Bank',  x:  20, y: 160, broadcast: false, pkHex: '——',     hmacHex: '——' },
  ];
  const broadcastCount = secaggNodes.filter(n => n.broadcast).length;
  const quorumReady = broadcastCount >= 3;
  // Edges between all broadcast nodes (mesh)
  const meshEdges: { a: SecAggNode; b: SecAggNode }[] = [];
  const broadcast = secaggNodes.filter(n => n.broadcast);
  for (let i = 0; i < broadcast.length; i++)
    for (let j = i + 1; j < broadcast.length; j++) {
      const a = broadcast[i]; const b = broadcast[j];
      if (a && b) meshEdges.push({ a, b });
    }

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

      {/* 6-Tab Navigation */}
      <div className="flex overflow-x-auto no-scrollbar gap-2 sm:gap-3 pb-2 border-b border-[var(--color-border)]">
        {[
          { id: 'mtls',   icon: '🔑',  label: 'mTLS & Cert PKI' },
          { id: 'oidc',   icon: '🆔',  label: 'OIDC & IAM' },
          { id: 'abac',   icon: '🛡️',  label: 'Dynamic ABAC Rules' },
          { id: 'vault',  icon: '🔐',  label: 'HashiCorp Vault' },
          { id: 'audit',  icon: '⛓️',  label: 'Cryptographic Audit Chain' },
          { id: 'secagg', icon: '🔗',  label: 'P2P SecAgg (V2.0)' },
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
          {/* Tab 6: P2P SecAgg (V2.0) */}
          {activeTab === 'secagg' && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">

              {/* Left: Mesh Topology Visualiser */}
              <div className="glass-card p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">
                    🔗 X25519 Key Exchange Topology — Round #{secaggRound}
                  </h3>
                  <span
                    className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${
                      quorumReady
                        ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                        : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                    }`}
                  >
                    {quorumReady ? `✓ QUORUM MET (${broadcastCount}/4)` : `⏳ AWAITING QUORUM (${broadcastCount}/4)`}
                  </span>
                </div>

                {/* SVG Mesh Diagram */}
                <div className="bg-[var(--color-surface-alt)] rounded-xl p-2 flex items-center justify-center">
                  <svg viewBox="0 0 320 320" width="100%" style={{ maxWidth: 320, maxHeight: 320 }}>
                    {/* ECDH edges between broadcast nodes */}
                    {meshEdges.map((e, i) => (
                      <line
                        key={i}
                        x1={e.a.x + 40} y1={e.a.y + 40}
                        x2={e.b.x + 40} y2={e.b.y + 40}
                        stroke="#6366f1" strokeWidth="1.5" strokeDasharray="5 3"
                        opacity="0.55"
                      />
                    ))}
                    {secaggNodes.map((node) => (
                      <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
                        {/* Node circle */}
                        <circle
                          cx="40" cy="40" r="32"
                          fill={node.broadcast ? 'rgba(99,102,241,0.15)' : 'rgba(100,116,139,0.10)'}
                          stroke={node.broadcast ? '#6366f1' : '#475569'}
                          strokeWidth={node.broadcast ? '2' : '1.5'}
                        />
                        {/* Key icon */}
                        <text x="40" y="37" textAnchor="middle" fontSize="18">
                          {node.broadcast ? '🔑' : '⏳'}
                        </text>
                        {/* Node label */}
                        <text
                          x="40" y="56" textAnchor="middle" fontSize="8.5"
                          fill={node.broadcast ? '#a5b4fc' : '#64748b'}
                          fontWeight="600"
                        >
                          {node.label}
                        </text>
                        {/* Broadcast status badge */}
                        {node.broadcast && (
                          <>
                            <circle cx="64" cy="16" r="8" fill="#10b981" />
                            <text x="64" y="20" textAnchor="middle" fontSize="9" fill="white" fontWeight="bold">✓</text>
                          </>
                        )}
                      </g>
                    ))}
                  </svg>
                </div>

                {/* Protocol note */}
                <p className="text-[10px] text-[var(--color-text-muted)] leading-relaxed">
                  Dashed edges represent pairwise HKDF-SHA256 channels derived from X25519 ECDH.
                  The coordinator is a stateless relay — it stores only authenticated public key bundles
                  and never computes shared secrets or holds plaintext model weights.
                </p>
              </div>

              {/* Right: Node Details & Protocol Spec */}
              <div className="space-y-4">
                {/* Per-node status table */}
                <div className="glass-card p-5 space-y-3">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">Node Key Broadcast Status</h3>
                  <div className="space-y-2">
                    {secaggNodes.map((node) => (
                      <div
                        key={node.id}
                        className={`p-3 rounded-lg border text-xs flex items-start gap-3 ${
                          node.broadcast
                            ? 'bg-indigo-500/5 border-indigo-500/20'
                            : 'bg-slate-800/40 border-slate-700/30'
                        }`}
                      >
                        <span className="text-lg shrink-0 mt-0.5">{node.broadcast ? '🔑' : '⏳'}</span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-bold text-[var(--color-text-primary)] truncate">{node.label}</span>
                            <span
                              className={`shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded-full ${
                                node.broadcast
                                  ? 'bg-emerald-500/15 text-emerald-400'
                                  : 'bg-amber-500/15 text-amber-400'
                              }`}
                            >
                              {node.broadcast ? 'BROADCAST' : 'PENDING'}
                            </span>
                          </div>
                          <div className="font-mono text-[10px] text-[var(--color-text-muted)] mt-1 space-y-0.5">
                            <div>PK  <span className="text-indigo-400">{node.pkHex}</span></div>
                            <div>SIG <span className="text-purple-400">{node.hmacHex}</span></div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Protocol specification card */}
                <div className="glass-card p-5 space-y-3">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)]">Protocol Specification</h3>
                  <div className="space-y-2 text-xs">
                    {[
                      { label: 'Key Agreement',    value: 'Curve25519 ECDH (RFC 7748)' },
                      { label: 'KDF',              value: 'HKDF-SHA256 (RFC 5869)' },
                      { label: 'Mask PRG',         value: 'HMAC-SHA256 counter mode' },
                      { label: 'Modular Ring',     value: 'Z_{2^32} (32-bit unsigned)' },
                      { label: 'Authentication',   value: 'HMAC-SHA256 over (bank_id ∥ round_id ∥ pk)' },
                      { label: 'Zero-Sum Proof',   value: 'Σ y_u ≡ Σ w_u (mod 2^32)' },
                      { label: 'Server Knowledge', value: 'None — pure relay, ε=0 information' },
                      { label: 'Protocol Ver.',    value: '2.0.0' },
                    ].map((item) => (
                      <div key={item.label} className="flex justify-between gap-2 p-2 rounded bg-[var(--color-surface-alt)]">
                        <span className="text-[var(--color-text-muted)] shrink-0">{item.label}</span>
                        <span className="font-mono font-bold text-[var(--color-primary)] text-right">{item.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
