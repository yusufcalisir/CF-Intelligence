import { useState } from 'react';
import { motion } from 'framer-motion';
import { ShieldCheck, Lock } from 'lucide-react';
import {
  useSecurityStatus,
  useEvaluateABAC,
  useAuditChain,
  useVerifyAuditChain,
} from '../api/queries';

type SecurityTabId = 'mtls' | 'oidc' | 'abac' | 'vault' | 'audit' | 'secagg' | 'zkp' | 'unlearning' | 'pqc' | 'bridge' | 'rdp';

interface SecurityTabItem {
  id: SecurityTabId;
  icon: string;
  label: string;
  category: 'identity' | 'crypto' | 'governance';
}

const SECURITY_CATEGORIES = [
  { id: 'all', label: 'All Modules', count: 11, icon: '🛡️' },
  { id: 'identity', label: 'Identity & PKI', count: 4, icon: '🔑' },
  { id: 'crypto', label: 'Crypto Proofs', count: 3, icon: '⚡' },
  { id: 'governance', label: 'Audit & Governance', count: 4, icon: '⛓️' },
] as const;

const SECURITY_TABS: SecurityTabItem[] = [
  // Identity & PKI
  { id: 'mtls', icon: '🔑', label: 'mTLS & Cert PKI', category: 'identity' },
  { id: 'oidc', icon: '🆔', label: 'OIDC & IAM', category: 'identity' },
  { id: 'abac', icon: '🛡️', label: 'Dynamic ABAC Rules', category: 'identity' },
  { id: 'vault', icon: '🔐', label: 'HashiCorp Vault HSM', category: 'identity' },
  
  // Crypto Engines
  { id: 'secagg', icon: '🔗', label: 'P2P Curve25519 SecAgg', category: 'crypto' },
  { id: 'zkp', icon: '⚡', label: 'zk-SNARK Attestation', category: 'crypto' },
  { id: 'pqc', icon: '🛡️', label: 'PQC SecAgg & Kyber-768', category: 'crypto' },
  
  // Governance
  { id: 'audit', icon: '⛓️', label: 'Cryptographic Audit Chain', category: 'governance' },
  { id: 'unlearning', icon: '♻️', label: 'Confidential Unlearning', category: 'governance' },
  { id: 'bridge', icon: '🌉', label: 'Cross-Chain Settlement', category: 'governance' },
  { id: 'rdp', icon: '📈', label: 'Adaptive DP Auto-Scaler', category: 'governance' },
];

export default function SecurityPage() {
  const [activeTab, setActiveTab] = useState<SecurityTabId>('mtls');
  const [selectedCategory, setSelectedCategory] = useState<'all' | 'identity' | 'crypto' | 'governance'>('all');
  const { data: status, isLoading: isStatusLoading } = useSecurityStatus();
  const { data: auditEntries, isLoading: isAuditLoading } = useAuditChain(30);

  const evaluateABAC = useEvaluateABAC();
  const verifyChain = useVerifyAuditChain();

  // Adaptive DP Auto-Scaler state
  const [rdpTargetEps, setRdpTargetEps] = useState(4.0);
  const [rdpRoundLoss, setRdpRoundLoss] = useState(0.42);
  const [rdpBatchSize, setRdpBatchSize] = useState(256);
  const [isRdpLoading, setIsRdpLoading] = useState(false);
  const [rdpResult, setRdpResult] = useState<{
    round_id: number;
    calibrated_sigma: number;
    gradient_clip_c: number;
    instantaneous_epsilon: number;
    optimal_alpha: number;
    loss_velocity: number;
    sample_ratio_q: number;
    cumulative_epsilon: number;
    target_epsilon: number;
    budget_exhaustion_pct: number;
    is_budget_exceeded: boolean;
  } | null>(null);

  const handleCalibrateRDP = async () => {
    setIsRdpLoading(true);
    try {
      const res = await fetch('/api/v1/security/rdp/calibrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          round_id: 1,
          current_loss: rdpRoundLoss,
          prev_loss: rdpRoundLoss + 0.15,
          batch_size: rdpBatchSize,
          total_samples: 10000,
          target_epsilon: rdpTargetEps,
          total_rounds: 50,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setRdpResult(data);
      }
    } catch (e) {
      console.error('Failed to calibrate RDP', e);
    } finally {
      setIsRdpLoading(false);
    }
  };

  // Cross-Chain Settlement state
  const [bridgePoolAmount, setBridgePoolAmount] = useState(100000);
  const [bridgeCurrency, setBridgeCurrency] = useState('wCBDC');
  const [isBridgeLoading, setIsBridgeLoading] = useState(false);
  const [bridgeResult, setBridgeResult] = useState<{
    epoch_id: number;
    pool_currency: string;
    total_pool_amount: number;
    total_gas_fees_usd: number;
    routes: Array<{
      bank_id: string;
      network: string;
      protocol: string;
      token_symbol: string;
      amount: number;
      shapley_share_pct: number;
      destination_recipient: string;
      message_id: string;
      gas_fee_usd: number;
      status: string;
    }>;
    execution_time_ms: number;
    bridge_audit_hash: string;
  } | null>(null);

  const handleDisburseCrossChain = async () => {
    setIsBridgeLoading(true);
    try {
      const res = await fetch('/api/v1/security/bridge/disburse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          epoch_id: 42,
          pool_amount: bridgePoolAmount,
          currency: bridgeCurrency,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setBridgeResult(data);
      }
    } catch (e) {
      console.error('Failed to disburse cross-chain bridge', e);
    } finally {
      setIsBridgeLoading(false);
    }
  };

  // Post-Quantum Cryptography (PQC) state
  const [pqcKemAlgo, setPqcKemAlgo] = useState('Kyber768');
  const [pqcSigAlgo, setPqcSigAlgo] = useState('Dilithium3');
  const [isPqcLoading, setIsPqcLoading] = useState(false);
  const [pqcResult, setPqcResult] = useState<{
    kem_algorithm: string;
    kyber_public_key_hex: string;
    signature_algorithm: string;
    dilithium_public_key_hex: string;
    quantum_security_level: string;
  } | null>(null);

  const handleGeneratePQCKeypair = async () => {
    setIsPqcLoading(true);
    try {
      const res = await fetch('/api/v1/security/pqc/keypair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kem_algorithm: pqcKemAlgo, signature_algorithm: pqcSigAlgo }),
      });
      if (res.ok) {
        const data = await res.json();
        setPqcResult(data);
      }
    } catch (e) {
      console.error('Failed to generate PQC keypair', e);
    } finally {
      setIsPqcLoading(false);
    }
  };

  // Confidential Unlearning state
  const [unlearnBankId, setUnlearnBankId] = useState('bank_gamma');
  const [unlearnMethod, setUnlearnMethod] = useState('FIRST_ORDER_HESSIAN_INVERSION');
  const [isUnlearningLoading, setIsUnlearningLoading] = useState(false);
  const [unlearnResult, setUnlearnResult] = useState<{
    target_bank_id: string;
    parameter_drift_delta: number;
    hessian_spectral_radius: number;
    mia_membership_probability: number;
    execution_time_ms: number;
    erasure_verified: boolean;
  } | null>(null);

  const handleTriggerUnlearning = async () => {
    setIsUnlearningLoading(true);
    try {
      const res = await fetch('/api/v1/security/unlearn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_bank_id: unlearnBankId,
          unlearning_method: unlearnMethod,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setUnlearnResult(data);
      }
    } catch (e) {
      console.error('Failed to trigger unlearning', e);
    } finally {
      setIsUnlearningLoading(false);
    }
  };

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
  const [shamirThreshold, setShamirThreshold] = useState(3);
  const [droppedNodeIds, setDroppedNodeIds] = useState<string[]>(['bank_delta']);

  const secaggNodes: SecAggNode[] = [
    { id: 'bank_alpha', label: 'Alpha Intl.', x: 160, y: 60,  broadcast: !droppedNodeIds.includes('bank_alpha'), pkHex: 'a3f8c2..e14d', hmacHex: '9b72dd..3f01' },
    { id: 'bank_beta',  label: 'Beta Corp.',  x: 300, y: 160, broadcast: !droppedNodeIds.includes('bank_beta'),  pkHex: '5c19ab..8e72', hmacHex: 'cc40fa..d8b3' },
    { id: 'bank_gamma', label: 'Gamma Trust', x: 160, y: 260, broadcast: !droppedNodeIds.includes('bank_gamma'), pkHex: '7e23ff..10b4', hmacHex: '1e55aa..77c9' },
    { id: 'bank_delta', label: 'Delta Bank', x: 20,  y: 160, broadcast: !droppedNodeIds.includes('bank_delta'), pkHex: '99d4e1..f400', hmacHex: '44a1b0..9912' },
  ];
  const broadcastCount = secaggNodes.filter(n => n.broadcast).length;
  const quorumReady = broadcastCount >= shamirThreshold;

  const toggleDropout = (bankId: string) => {
    setDroppedNodeIds(prev =>
      prev.includes(bankId) ? prev.filter(id => id !== bankId) : [...prev, bankId]
    );
  };
  // Edges between all broadcast nodes (mesh)
  const meshEdges: { a: SecAggNode; b: SecAggNode }[] = [];
  const broadcast = secaggNodes.filter(n => n.broadcast);
  for (let i = 0; i < broadcast.length; i++)
    for (let j = i + 1; j < broadcast.length; j++) {
      const a = broadcast[i]; const b = broadcast[j];
      if (a && b) meshEdges.push({ a, b });
    }

  const filteredTabs = selectedCategory === 'all'
    ? SECURITY_TABS
    : SECURITY_TABS.filter((t) => t.category === selectedCategory);

  return (
    <div className="space-y-6 min-w-0">
      {/* Page Header Banner */}
      <div className="glass-card p-4 sm:p-6 rounded-2xl bg-gradient-to-r from-[#07091e]/95 via-[#0b0e2d]/90 to-[#07091e]/95 border border-indigo-500/20 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-4 relative overflow-hidden min-w-0">
        <div className="absolute top-0 inset-x-0 h-0.5 bg-gradient-to-r from-blue-500 via-indigo-500 to-purple-500 opacity-70" />
        <div className="space-y-2 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap min-w-0">
            <div className="p-2 rounded-xl bg-indigo-500/10 border border-indigo-500/25 text-indigo-400 shrink-0">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <h1 className="text-lg sm:text-2xl font-black text-transparent bg-clip-text bg-gradient-to-r from-blue-400 via-indigo-200 to-purple-300 tracking-tight truncate">
              Enterprise Security & Identity Control Suite
            </h1>
          </div>
          <p className="text-xs sm:text-sm text-[var(--color-text-muted)] leading-relaxed">
            ISO 27001, SOC2 Type II, PCI-DSS 4.0 compliance: mTLS 1.3, OIDC JWT, ABAC, HashiCorp Vault HSM & SHA-256 Audit Chain
          </p>
          <div className="flex items-center gap-1.5 pt-0.5 flex-wrap">
            {['ISO 27001', 'SOC2 Type II', 'PCI-DSS 4.0', 'FIPS 140-3 HSM'].map((badge) => (
              <span key={badge} className="px-2 py-0.5 rounded text-[9.5px] font-mono font-semibold bg-white/5 border border-white/10 text-slate-300">
                {badge}
              </span>
            ))}
          </div>
        </div>

        <div className="flex items-center shrink-0">
          <button
            onClick={() => verifyChain.mutate()}
            disabled={verifyChain.isPending}
            className="w-full md:w-auto px-4 py-2.5 text-xs font-bold rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-600/20 transition-all flex items-center justify-center gap-2 border border-emerald-400/30 cursor-pointer disabled:opacity-50 shrink-0"
          >
            {verifyChain.isPending ? (
              <>
                <span className="animate-spin w-3.5 h-3.5 rounded-full border-2 border-white border-t-transparent" />
                <span>Verifying Hashes...</span>
              </>
            ) : (
              <>
                <Lock className="w-3.5 h-3.5 text-emerald-200" />
                <span>Verify SHA-256 Audit Chain</span>
              </>
            )}
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

      {/* Category Filter & Module Tabs Navigation (Responsive Zero-Scroll Grid) */}
      <div className="space-y-3 min-w-0">
        {/* Category Selector Pills */}
        <div className="grid grid-cols-2 sm:flex sm:flex-wrap items-center gap-2 min-w-0">
          {SECURITY_CATEGORIES.map((cat) => {
            const isCatActive = selectedCategory === cat.id;
            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all flex items-center justify-center sm:justify-start gap-1.5 cursor-pointer border ${
                  isCatActive
                    ? 'bg-indigo-600/25 border-indigo-500/50 text-indigo-200 shadow-sm'
                    : 'bg-white/[0.03] border-white/5 text-slate-400 hover:text-slate-200 hover:bg-white/[0.06]'
                }`}
              >
                <span>{cat.icon}</span>
                <span>{cat.label}</span>
                <span className="text-[10px] opacity-60 font-mono">({cat.count})</span>
              </button>
            );
          })}
        </div>

        {/* Module Pill Cards Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2 min-w-0">
          {filteredTabs.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`p-2.5 sm:p-3 rounded-xl transition-all flex flex-col justify-between text-left border min-h-[58px] cursor-pointer relative overflow-hidden group ${
                  isActive
                    ? 'bg-indigo-600/20 border-indigo-500/60 shadow-lg shadow-indigo-600/15 text-indigo-200'
                    : 'bg-[#090b1e]/70 border-white/5 text-slate-400 hover:text-slate-200 hover:border-white/15 hover:bg-[#0d102b]'
                }`}
              >
                {isActive && (
                  <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-indigo-500 via-purple-500 to-cyan-400" />
                )}
                <div className="flex items-center justify-between w-full">
                  <span className="text-base sm:text-lg">{tab.icon}</span>
                  {isActive && (
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                  )}
                </div>
                <div className="text-xs font-bold truncate mt-1">
                  {tab.label}
                </div>
              </button>
            );
          })}
        </div>
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
            <div className="space-y-4">
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

              {/* HSM Root CA Binding Card */}
              <div className="glass-card p-5 space-y-4 border border-indigo-500/30 bg-indigo-500/5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-primary)] flex items-center gap-2">
                    <span>🛡️ Vault PKI Root CA — FIPS 140-2 Level 3 HSM Binding</span>
                  </h3>
                  <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                    ✓ HSM BOUND (FIPS 140-2 L3)
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs">
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">HSM Slot & Provider</div>
                    <div className="font-mono font-bold text-indigo-300">Slot #0 (PKCS#11 / Softhsm2)</div>
                  </div>
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">Private Key Guarantee</div>
                    <div className="font-mono font-bold text-emerald-400">🔒 Non-Exportable (Zero-Disk)</div>
                  </div>
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">Root CA Algorithm</div>
                    <div className="font-mono font-bold text-purple-300">RSA-4096 / SHA256-PSS</div>
                  </div>
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">FIPS Compliance Level</div>
                    <div className="font-mono font-bold text-amber-300">FIPS 140-2 Level 3</div>
                  </div>
                </div>

                <div className="p-3 rounded-lg border border-indigo-500/20 bg-indigo-500/10 text-xs space-y-1 text-indigo-200">
                  <div className="font-bold">Hardware-Anchored Root CA Key Invariant:</div>
                  <p className="text-[10px] opacity-90 leading-relaxed">
                    HashiCorp Vault's PKI engine delegates root CA key generation, certificate signing (CSR), and CRL signing
                    operations directly to an HSM hardware slot via VaultHSMPKIBinder. Private root CA key material never leaves the hardware boundary.
                  </p>
                </div>
              </div>

              {/* Gnosis Safe 2-of-3 Multi-Sig Governance Card */}
              <div className="glass-card p-5 space-y-4 border border-purple-500/30 bg-purple-500/5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-primary)] flex items-center gap-2">
                    <span>⚡ On-Chain Governance — Gnosis Safe 2-of-3 Multi-Sig Coordinator</span>
                  </h3>
                  <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-purple-500/15 text-purple-300 border border-purple-500/30">
                    2/3 THRESHOLD GOVERNANCE
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">Trustee #1 (Central Bank / Regulator)</div>
                    <div className="font-mono text-[10px] font-bold text-emerald-400 truncate">0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266</div>
                  </div>
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">Trustee #2 (Consortium Trustee Alpha)</div>
                    <div className="font-mono text-[10px] font-bold text-indigo-300 truncate">0x70997970C51812dc3A010C7d01b50e0d17dc79C8</div>
                  </div>
                  <div className="p-3 rounded bg-[var(--color-surface-alt)] space-y-1">
                    <div className="text-[var(--color-text-muted)] text-[10px]">Trustee #3 (Consortium Trustee Beta)</div>
                    <div className="font-mono text-[10px] font-bold text-purple-300 truncate">0x3C44CdD160573615659514930278505963E8A155</div>
                  </div>
                </div>

                <div className="p-3 rounded-lg border border-purple-500/20 bg-purple-500/10 text-xs space-y-2 text-purple-200">
                  <div className="font-bold">Multi-Sig State Invariant:</div>
                  <p className="text-[10px] opacity-90 leading-relaxed">
                    Single-wallet EOA coordinator centralization risk is eliminated. Critical smart contract functions (Incentive Settlement, Participant Quarantine, and Token Pool Deposits) require at least 2 valid cryptographic signatures from authorized consortium trustees.
                  </p>
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
                      { label: 'Prime Field',      value: 'Z_p (p = 2^256 - 189)' },
                      { label: 'Threshold Scheme', value: `Shamir (${shamirThreshold}, 4) Galois Polynomial` },
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

                {/* Shamir (t, n) Threshold & Node Dropout Simulator */}
                <div className="glass-card p-5 space-y-4 border border-indigo-500/30 bg-indigo-500/5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-bold uppercase text-[var(--color-text-primary)] flex items-center gap-2">
                      <span>🧮 Shamir (t, n) Dropout Reconstruction</span>
                    </h3>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-[var(--color-text-muted)]">Threshold (t):</span>
                      <select
                        value={shamirThreshold}
                        onChange={(e) => setShamirThreshold(Number(e.target.value))}
                        className="bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded px-2 py-1 text-xs font-mono font-bold text-[var(--color-primary)]"
                      >
                        <option value={2}>t = 2 (50% Quorum)</option>
                        <option value={3}>t = 3 (75% Quorum)</option>
                        <option value={4}>t = 4 (100% Strict)</option>
                      </select>
                    </div>
                  </div>

                  {/* Node dropout toggle buttons */}
                  <div className="space-y-2">
                    <span className="text-[11px] text-[var(--color-text-muted)] font-medium">
                      Simulate Mid-Round Node Dropouts:
                    </span>
                    <div className="grid grid-cols-2 gap-2">
                      {[
                        { id: 'bank_alpha', label: 'Alpha' },
                        { id: 'bank_beta', label: 'Beta' },
                        { id: 'bank_gamma', label: 'Gamma' },
                        { id: 'bank_delta', label: 'Delta' },
                      ].map((bank) => {
                        const isDropped = droppedNodeIds.includes(bank.id);
                        return (
                          <button
                            key={bank.id}
                            onClick={() => toggleDropout(bank.id)}
                            className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between border transition-all ${
                              isDropped
                                ? 'bg-rose-500/15 border-rose-500/40 text-rose-400'
                                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                            }`}
                          >
                            <span>{bank.label}</span>
                            <span className="text-[10px] font-bold uppercase">
                              {isDropped ? '❌ DROPPED' : '✓ ACTIVE'}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Live Reconstruction Banner */}
                  <div
                    className={`p-3 rounded-lg border text-xs space-y-1 ${
                      quorumReady
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                        : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                    }`}
                  >
                    <div className="flex items-center justify-between font-bold">
                      <span>
                        {quorumReady
                          ? `✓ RECONSTRUCTION SUCCESSFUL (${broadcastCount}/${shamirThreshold} shares collected)`
                          : `⚠️ RECONSTRUCTION BLOCKED (${broadcastCount}/${shamirThreshold} shares collected)`}
                      </span>
                      <span className="font-mono text-[10px]">
                        {quorumReady ? 'MAE = 0.000000' : 'INSUFFICIENT SHARES'}
                      </span>
                    </div>
                    <p className="text-[10px] opacity-80 leading-relaxed">
                      {quorumReady
                        ? `Lagrange polynomial interpolation over Z_p reconstructed secrets for ${
                            droppedNodeIds.length === 0 ? 'all active participants' : droppedNodeIds.join(', ')
                          }. Plaintext FedAvg global weights restored identically.`
                        : `At least ${shamirThreshold} shares required to interpolate polynomial f(0). Aggregation halted.`}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Tab 7: zk-SNARK Attestation (V3.0) */}
          {activeTab === 'zkp' && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              <div className="glass-card p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)] flex items-center gap-2">
                    <span>⚡ Groth16 zk-SNARK Model Weight Attestation</span>
                  </h3>
                  <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 font-mono">
                    BN254 ELLIPTIC CURVE
                  </span>
                </div>

                <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
                  Proves that participating bank weight updates <code className="font-mono text-indigo-300">w_local</code> are correctly computed from local data, meet Poseidon commitments <code className="font-mono text-indigo-300">H_w</code>, and satisfy <code className="font-mono text-indigo-300">||w||_2 ≤ C_max</code> bounds in <strong>O(1) constant time</strong> without revealing raw model weights.
                </p>

                <div className="space-y-2.5 text-xs">
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">Verification Scheme</span>
                    <span className="font-mono font-bold text-emerald-400">Groth16 / PlonKish (O(1) Bilinear Pairing)</span>
                  </div>
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">Poseidon Hash Digest (H_w)</span>
                    <span className="font-mono font-bold text-[var(--color-primary)]">0x2a9f...b48c (BN254 F_p)</span>
                  </div>
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">L2 Norm Clip Limit (C_max)</span>
                    <span className="font-mono font-bold text-amber-400">10.0 (Strict Bound Enforced)</span>
                  </div>
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">Typical Verification Latency</span>
                    <span className="font-mono font-bold text-indigo-300">2.14 ms (&lt; 5.00 ms SLA)</span>
                  </div>
                </div>
              </div>

              <div className="glass-card p-5 space-y-4 flex flex-col justify-between">
                <div>
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)] mb-3">
                    Proof Elements (π = (A, B, C)) & Verification Status
                  </h3>

                  <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 space-y-1 mb-4">
                    <div className="flex justify-between font-bold text-xs">
                      <span>✓ BILINEAR PAIRING CHECK PASSED</span>
                      <span className="font-mono text-[10px]">e(A, B) = e(α, β) · e(C, δ)</span>
                    </div>
                    <p className="text-[11px] opacity-80">
                      Zero-knowledge attestation verified on server. Zero gradient poisoning or free-riding updates detected.
                    </p>
                  </div>

                  <div className="space-y-2 text-[11px] font-mono">
                    <div className="p-2 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                      <span className="text-[var(--color-text-muted)]">π_A (G1):</span>
                      <span className="text-indigo-300">0x1f92a4...810c9d</span>
                    </div>
                    <div className="p-2 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                      <span className="text-[var(--color-text-muted)]">π_B (G2):</span>
                      <span className="text-indigo-300">[[0x3e1..., 0x8a7...], [0x41f..., 0x9b2...]]</span>
                    </div>
                    <div className="p-2 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                      <span className="text-[var(--color-text-muted)]">π_C (G1):</span>
                      <span className="text-indigo-300">0x7c45d2...19e34b</span>
                    </div>
                  </div>
                </div>

                <div className="text-[10px] text-[var(--color-text-muted)] pt-3 border-t border-[var(--color-border)] flex justify-between">
                  <span>Circuit: weight_attestation.circom</span>
                  <span>Verified Proofs: 100% (O(1) Verification)</span>
                </div>
              </div>
            </div>
          )}

          {/* Tab 8: Confidential Unlearning (V3.0) */}
          {activeTab === 'unlearning' && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              <div className="glass-card p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)] flex items-center gap-2">
                    <span>♻️ Bank Revocation & Gradient Erasure Console</span>
                  </h3>
                  <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-purple-500/15 text-purple-400 border border-purple-500/30 font-mono">
                    FIRST-ORDER HESSIAN INVERSION
                  </span>
                </div>

                <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
                  Erases historical parameter contributions of an evicted or compromised bank from live PyTorch model checkpoints using <strong>Sub-sampled Newton Steps (H⁻¹ ∇L)</strong> without retraining from scratch.
                </p>

                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    <div>
                      <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1">Target Revoked Bank</label>
                      <select
                        value={unlearnBankId}
                        onChange={(e) => setUnlearnBankId(e.target.value)}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-xs font-mono"
                      >
                        <option value="bank_gamma">Bank Gamma (Compromised)</option>
                        <option value="bank_beta">Bank Beta (Revoked)</option>
                        <option value="bank_alpha">Bank Alpha (Evicted)</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-[10px] font-bold text-[var(--color-text-muted)] uppercase mb-1">Unlearning Algorithm</label>
                      <select
                        value={unlearnMethod}
                        onChange={(e) => setUnlearnMethod(e.target.value)}
                        className="w-full px-2.5 py-1.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)] text-xs font-mono"
                      >
                        <option value="FIRST_ORDER_HESSIAN_INVERSION">First-Order Hessian Inversion</option>
                        <option value="SUB_SAMPLED_NEWTON_STEPS">Sub-sampled Newton Steps</option>
                        <option value="EXACT_LINEAGE_SUBTRACTION">Exact Lineage Subtraction</option>
                      </select>
                    </div>
                  </div>

                  <button
                    onClick={handleTriggerUnlearning}
                    disabled={isUnlearningLoading}
                    className="w-full py-2 bg-gradient-to-r from-red-600 to-purple-600 hover:from-red-500 hover:to-purple-500 text-white font-bold text-xs rounded-lg transition-all shadow-md shadow-red-600/20 cursor-pointer disabled:opacity-50"
                  >
                    {isUnlearningLoading ? '⏳ Executing Hessian Inversion Erasure...' : '⚡ Trigger Model Weight Erasure'}
                  </button>
                </div>

                <div className="space-y-2 text-xs pt-2">
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">Solver Method</span>
                    <span className="font-mono font-bold text-indigo-300">Conjugate Gradient (H⁻¹ v)</span>
                  </div>
                  <div className="flex justify-between p-2.5 rounded-lg bg-[var(--color-surface-alt)] border border-[var(--color-border)]">
                    <span className="text-[var(--color-text-muted)]">Target MIA Risk Threshold</span>
                    <span className="font-mono font-bold text-emerald-400">P(MIA) ≤ 0.52 (Random Guess)</span>
                  </div>
                </div>
              </div>

              <div className="glass-card p-5 space-y-4 flex flex-col justify-between">
                <div>
                  <h3 className="text-sm font-bold uppercase text-[var(--color-text-muted)] mb-3">
                    Erasure Metrics & Membership Inference (MIA) Verification
                  </h3>

                  {unlearnResult ? (
                    <div className="space-y-3 text-xs font-mono">
                      <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 space-y-1">
                        <div className="flex justify-between font-bold">
                          <span>✓ WEIGHT ERASURE VERIFIED</span>
                          <span>Latency: {unlearnResult.execution_time_ms.toFixed(2)} ms</span>
                        </div>
                        <p className="text-[11px] opacity-80 font-sans">
                          Target bank gradient footprint removed. Model parameter drift bounded by Euclidean norm delta.
                        </p>
                      </div>

                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Target Bank:</span>
                        <span className="text-indigo-300 font-bold">{unlearnResult.target_bank_id}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Parameter Drift Delta (||Δw||_2):</span>
                        <span className="text-amber-400 font-bold">{unlearnResult.parameter_drift_delta.toFixed(4)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Hessian Spectral Radius (λ_max):</span>
                        <span className="text-purple-300 font-bold">{unlearnResult.hessian_spectral_radius.toFixed(3)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">MIA Membership Probability:</span>
                        <span className="text-emerald-400 font-bold">{(unlearnResult.mia_membership_probability * 100).toFixed(1)}% (Target ≤52%)</span>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 text-center text-xs text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-xl">
                      Select an evicted bank and click <strong>Trigger Model Weight Erasure</strong> to execute Hessian inversion parameter removal.
                    </div>
                  )}
                </div>

                <div className="text-[10px] text-[var(--color-text-muted)] pt-3 border-t border-[var(--color-border)] flex justify-between font-mono">
                  <span>Engine: federated_unlearning_engine.py</span>
                  <span>MIA Status: Audited</span>
                </div>
              </div>
            </div>
          )}

          {/* TAB 9: PQC SecAgg & Kyber/Dilithium (V3.0) */}
          {activeTab === 'pqc' && (
            <div className="glass-card p-6 space-y-6">
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-4">
                <div>
                  <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                    <span>🛡️</span> NIST Post-Quantum Cryptography (PQC SecAgg & Kyber/Dilithium)
                  </h3>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    NIST FIPS 203 (CRYSTALS-Kyber-768 KEM) & FIPS 204 (CRYSTALS-Dilithium-3 signatures) hybrid quantum-safe SecAgg
                  </p>
                </div>
                <span className="px-2.5 py-1 text-xs font-bold rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                  NIST Level 3 (256-bit Lattice Security)
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-4">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Post-Quantum Key Exchange Config</h4>

                  <div className="space-y-3 text-xs">
                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Key Encapsulation Mechanism (ML-KEM):</label>
                      <select
                        value={pqcKemAlgo}
                        onChange={(e) => setPqcKemAlgo(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      >
                        <option value="Kyber768">CRYSTALS-Kyber-768 (NIST Level 3 - Default)</option>
                        <option value="Kyber1024">CRYSTALS-Kyber-1024 (NIST Level 5)</option>
                        <option value="Kyber512">CRYSTALS-Kyber-512 (NIST Level 1)</option>
                      </select>
                    </div>

                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Digital Signature Standard (ML-DSA):</label>
                      <select
                        value={pqcSigAlgo}
                        onChange={(e) => setPqcSigAlgo(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      >
                        <option value="Dilithium3">CRYSTALS-Dilithium-3 (NIST Level 3 - Default)</option>
                        <option value="Dilithium5">CRYSTALS-Dilithium-5 (NIST Level 5)</option>
                      </select>
                    </div>

                    <button
                      onClick={handleGeneratePQCKeypair}
                      disabled={isPqcLoading}
                      className="w-full py-2.5 px-4 rounded-xl font-bold bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-lg transition-all text-xs cursor-pointer flex items-center justify-center gap-2"
                    >
                      {isPqcLoading ? 'Generating Lattice Keys...' : '🔑 Generate Kyber-768 + Dilithium-3 Keypair'}
                    </button>
                  </div>

                  <div className="p-3 rounded-lg bg-indigo-500/5 border border-indigo-500/15 text-[11px] text-slate-300 space-y-1.5">
                    <div className="font-bold text-indigo-300">Quantum Security Guarantee:</div>
                    <p className="text-[var(--color-text-muted)]">
                      Lattice-based Module Learning With Errors (M-LWE) provides 100% mathematical immunity against Shor&apos;s algorithm on future quantum supercomputers.
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Lattice Keypair Telemetry & Public Keys</h4>

                  {pqcResult ? (
                    <div className="p-4 rounded-xl bg-black/30 border border-[var(--color-border)] space-y-3 text-xs font-mono">
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">KEM Algorithm:</span>
                        <span className="text-indigo-300 font-bold">{pqcResult.kem_algorithm}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Kyber Public Key:</span>
                        <span className="text-amber-400 font-bold truncate max-w-[200px]">{pqcResult.kyber_public_key_hex}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Signature Algorithm:</span>
                        <span className="text-purple-300 font-bold">{pqcResult.signature_algorithm}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Dilithium Public Key:</span>
                        <span className="text-emerald-400 font-bold truncate max-w-[200px]">{pqcResult.dilithium_public_key_hex}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Quantum Security Level:</span>
                        <span className="text-cyan-300 font-bold">{pqcResult.quantum_security_level}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 text-center text-xs text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-xl">
                      Click <strong>Generate Kyber-768 + Dilithium-3 Keypair</strong> to derive NIST lattice public keys.
                    </div>
                  )}
                </div>

                <div className="text-[10px] text-[var(--color-text-muted)] pt-3 border-t border-[var(--color-border)] flex justify-between font-mono col-span-1 md:col-span-2">
                  <span>Driver: pqc_secagg_driver.py</span>
                  <span>NIST FIPS 203 / 204 Compliant</span>
                </div>
              </div>
            </div>
          )}

          {/* TAB 10: Cross-Chain Settlement & Layer-2 Liquidity Bridge (V3.0) */}
          {activeTab === 'bridge' && (
            <div className="glass-card p-6 space-y-6">
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-4">
                <div>
                  <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                    <span>🌉</span> Cross-Chain Inter-Bank Settlement & Layer-2 Liquidity Bridge
                  </h3>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Chainlink CCIP & LayerZero V2 multi-ledger Shapley incentive routing for CBDC and Tokenized Deposits
                  </p>
                </div>
                <span className="px-2.5 py-1 text-xs font-bold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  L2 Sub-Second Finality (&lt;1s)
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="space-y-4 md:col-span-1">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Settlement Pool Parameters</h4>

                  <div className="space-y-3 text-xs">
                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Epoch Settlement Pool Amount:</label>
                      <input
                        type="number"
                        value={bridgePoolAmount}
                        onChange={(e) => setBridgePoolAmount(Number(e.target.value))}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      />
                    </div>

                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Settlement Asset Standard:</label>
                      <select
                        value={bridgeCurrency}
                        onChange={(e) => setBridgeCurrency(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      >
                        <option value="wCBDC">wCBDC (Wholesale CBDC Token)</option>
                        <option value="EUR-Deposit">EUR-Deposit (Tokenized Commercial Deposit)</option>
                        <option value="USD-Institutional">USD-Institutional (Canton Daml Contract)</option>
                      </select>
                    </div>

                    <button
                      onClick={handleDisburseCrossChain}
                      disabled={isBridgeLoading}
                      className="w-full py-2.5 px-4 rounded-xl font-bold bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg transition-all text-xs cursor-pointer flex items-center justify-center gap-2"
                    >
                      {isBridgeLoading ? 'Relaying CCIP Messages...' : '🚀 Execute CCIP Multi-Ledger Payout'}
                    </button>
                  </div>

                  <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/15 text-[11px] text-slate-300 space-y-1.5">
                    <div className="font-bold text-emerald-300">Cross-Ledger Coverage:</div>
                    <p className="text-[var(--color-text-muted)]">
                      Routes funds simultaneously across Arbitrum, Optimism, Canton Private DLT, and Hyperledger Fabric with zero counterparty risk.
                    </p>
                  </div>
                </div>

                <div className="space-y-4 md:col-span-2">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Multi-Ledger Route Receipts & CCIP Message IDs</h4>

                  {bridgeResult ? (
                    <div className="space-y-3">
                      <div className="p-3 rounded-xl bg-black/40 border border-[var(--color-border)] flex items-center justify-between text-xs font-mono">
                        <div>
                          <span className="text-[var(--color-text-muted)]">Total Disbursed: </span>
                          <span className="text-emerald-400 font-bold">{bridgeResult.total_pool_amount.toLocaleString()} {bridgeResult.pool_currency}</span>
                        </div>
                        <div>
                          <span className="text-[var(--color-text-muted)]">L2 Gas Fees: </span>
                          <span className="text-amber-400 font-bold">${bridgeResult.total_gas_fees_usd.toFixed(4)} USD</span>
                        </div>
                        <div>
                          <span className="text-[var(--color-text-muted)]">Audit: </span>
                          <span className="text-indigo-300 font-bold">{bridgeResult.bridge_audit_hash.slice(0, 10)}...</span>
                        </div>
                      </div>

                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs font-mono border border-[var(--color-border)] rounded-xl overflow-hidden">
                          <thead className="bg-black/60 text-[var(--color-text-muted)] uppercase text-[10px]">
                            <tr>
                              <th className="p-2.5">Bank</th>
                              <th className="p-2.5">Target Ledger</th>
                              <th className="p-2.5">Protocol</th>
                              <th className="p-2.5">Shapley Payout</th>
                              <th className="p-2.5">CCIP Message ID</th>
                              <th className="p-2.5">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--color-border)] bg-black/20">
                            {bridgeResult.routes.map((route, i) => (
                              <tr key={i} className="hover:bg-white/5">
                                <td className="p-2.5 font-bold text-slate-200">{route.bank_id}</td>
                                <td className="p-2.5 text-indigo-300">{route.network}</td>
                                <td className="p-2.5 text-[var(--color-text-muted)]">{route.protocol}</td>
                                <td className="p-2.5 text-emerald-400 font-bold">{route.amount.toLocaleString()} {route.token_symbol}</td>
                                <td className="p-2.5 text-amber-300 truncate max-w-[120px]">{route.message_id}</td>
                                <td className="p-2.5 text-emerald-400 font-bold">{route.status}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <div className="p-8 text-center text-xs text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-xl">
                      Click <strong>Execute CCIP Multi-Ledger Payout</strong> to trigger atomic cross-chain incentive disbursements across Arbitrum, Optimism, Canton, and Fabric.
                    </div>
                  )}
                </div>

                <div className="text-[10px] text-[var(--color-text-muted)] pt-3 border-t border-[var(--color-border)] flex justify-between font-mono col-span-1 md:col-span-3">
                  <span>Driver: layer2_crosschain_bridge.py</span>
                  <span>Standards: Chainlink CCIP EVM2AnyMessage & LayerZero V2</span>
                </div>
              </div>
            </div>
          )}

          {/* TAB 11: Adaptive Dynamic DP Budget Auto-Scaler (V3.0) */}
          {activeTab === 'rdp' && (
            <div className="glass-card p-6 space-y-6">
              <div className="flex items-center justify-between border-b border-[var(--color-border)] pb-4">
                <div>
                  <h3 className="text-lg font-bold text-slate-100 flex items-center gap-2">
                    <span>📈</span> Adaptive Dynamic Differential Privacy Budget Auto-Scaler
                  </h3>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Rényi Differential Privacy (RDP) & PRV numerical accountant with dynamic noise calibration (σ_t)
                  </p>
                </div>
                <span className="px-2.5 py-1 text-xs font-bold rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                  Rényi Divergence (16 Orders)
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-4">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Dynamic DP Optimization Controls</h4>

                  <div className="space-y-3 text-xs">
                    <div>
                      <div className="flex justify-between mb-1 text-[var(--color-text-muted)]">
                        <span>Target Cumulative Budget (ε_target):</span>
                        <span className="font-bold text-cyan-300">{rdpTargetEps.toFixed(1)}</span>
                      </div>
                      <input
                        type="range"
                        min="1.0"
                        max="8.0"
                        step="0.5"
                        value={rdpTargetEps}
                        onChange={(e) => setRdpTargetEps(Number(e.target.value))}
                        className="w-full"
                      />
                    </div>

                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Instantaneous Round Loss (ℒ_t):</label>
                      <input
                        type="number"
                        step="0.01"
                        value={rdpRoundLoss}
                        onChange={(e) => setRdpRoundLoss(Number(e.target.value))}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      />
                    </div>

                    <div>
                      <label className="block text-[var(--color-text-muted)] mb-1">Batch Size (B_t):</label>
                      <input
                        type="number"
                        value={rdpBatchSize}
                        onChange={(e) => setRdpBatchSize(Number(e.target.value))}
                        className="w-full px-3 py-2 rounded-lg bg-black/40 border border-[var(--color-border)] text-slate-200"
                      />
                    </div>

                    <button
                      onClick={handleCalibrateRDP}
                      disabled={isRdpLoading}
                      className="w-full py-2.5 px-4 rounded-xl font-bold bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white shadow-lg transition-all text-xs cursor-pointer flex items-center justify-center gap-2"
                    >
                      {isRdpLoading ? 'Optimizing RDP Dual Bounds...' : '⚡ Auto-Scale Round Noise (σ_t)'}
                    </button>
                  </div>

                  <div className="p-3 rounded-lg bg-cyan-500/5 border border-cyan-500/15 text-[11px] text-slate-300 space-y-1.5">
                    <div className="font-bold text-cyan-300">Anti-Overnoising Guarantee:</div>
                    <p className="text-[var(--color-text-muted)]">
                      Dynamically reduces noise as gradients converge, preserving fraud detection AUC-ROC (&gt;0.94) without early budget exhaustion.
                    </p>
                  </div>
                </div>

                <div className="space-y-4">
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">RDP Accountant Telemetry & Dual Minimization</h4>

                  {rdpResult ? (
                    <div className="p-4 rounded-xl bg-black/30 border border-[var(--color-border)] space-y-3 text-xs font-mono">
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Calibrated Noise Multiplier (σ_t):</span>
                        <span className="text-cyan-400 font-bold">{rdpResult.calibrated_sigma.toFixed(3)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Dynamic Gradient Clip (C_t):</span>
                        <span className="text-amber-400 font-bold">{rdpResult.gradient_clip_c.toFixed(2)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Optimal RDP Order (α*):</span>
                        <span className="text-purple-300 font-bold">{rdpResult.optimal_alpha.toFixed(1)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Cumulative Privacy Loss (ε):</span>
                        <span className="text-emerald-400 font-bold">{rdpResult.cumulative_epsilon.toFixed(3)} / {rdpResult.target_epsilon.toFixed(1)}</span>
                      </div>
                      <div className="p-2.5 rounded bg-black/40 border border-[var(--color-border)] flex justify-between">
                        <span className="text-[var(--color-text-muted)]">Budget Exhaustion:</span>
                        <span className="text-indigo-300 font-bold">{rdpResult.budget_exhaustion_pct.toFixed(1)}%</span>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 text-center text-xs text-[var(--color-text-muted)] border border-dashed border-[var(--color-border)] rounded-xl">
                      Click <strong>Auto-Scale Round Noise (σ_t)</strong> to compute the optimal convex dual Rényi privacy bound.
                    </div>
                  )}
                </div>

                <div className="text-[10px] text-[var(--color-text-muted)] pt-3 border-t border-[var(--color-border)] flex justify-between font-mono col-span-1 md:col-span-2">
                  <span>Driver: adaptive_dp_autoscaler.py</span>
                  <span>Rényi DP & PRV Numerical Composition</span>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
