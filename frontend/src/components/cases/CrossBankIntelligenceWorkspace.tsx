import { useState, useId, FC, ChangeEvent } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  Clock,
  Layers,
  Database,
  Lock,
  RefreshCw,
  Search,
} from 'lucide-react';
import {
  useCaseEligibleEntities,
  useCaseIntelligenceEvidence,
  useQueryMultiCounterpartyIntelligence,
  useAttachMultiCounterpartyEvidence,
  useCrossBankQueryCapabilities,
} from '../../api/queries';
import {
  MultiCounterpartyIntelligenceQueryResponse,
  CrossBankMultiEvidenceAttachResponse,
} from '../../api/types';
import { formatApiError } from '../../api/client';

export interface CrossBankIntelligenceWorkspaceProps {
  caseId: string;
  bankId?: string | null;
  assignedTo?: string | null;
  isOpen?: boolean;
}

const FALLBACK_CONSORTIUMS = [
  { id: 'consortium_alpha', name: 'Consortium Alpha (Tier-1 AML Federation)' },
  { id: 'consortium_beta', name: 'Consortium Beta (Cross-Border Payments)' },
];

const DEFAULT_COUNTERPARTS = ['bank_b', 'bank_c', 'bank_d'];

const PURPOSE_OPTIONS = [
  { value: 'FRAUD_INVESTIGATION', label: 'Fraud & Financial Crime Investigation' },
  { value: 'AML_HIGH_RISK_MATCHING', label: 'AML High-Risk Entity Correlation' },
  { value: 'SANCTIONS_SCREENING', label: 'Cross-Border Sanctions Intelligence' },
  { value: 'REGULATORY_SAR_EVIDENCE', label: 'FinCEN SAR Regulatory Evidence Corroboration' },
];

export const CrossBankIntelligenceWorkspace: FC<CrossBankIntelligenceWorkspaceProps> = ({
  caseId,
  bankId,
  assignedTo: _assignedTo = 'analyst',
}) => {
  const [selectedEntityId, setSelectedEntityId] = useState<string>('');
  const [selectedConsortium, setSelectedConsortium] = useState<string>('');
  const [targetBanks, setTargetBanks] = useState<string[]>(['bank_b', 'bank_c']);
  const [purpose, setPurpose] = useState<string>('FRAUD_INVESTIGATION');
  const [analystNotes, setAnalystNotes] = useState<string>('');
  const [queryError, setQueryError] = useState<string | null>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [copiedHash, setCopiedHash] = useState<string | null>(null);

  // Stable idempotency keys across retries (Gate D)
  const [queryIdempotencyKey, setQueryIdempotencyKey] = useState<string>(() => `query_${crypto.randomUUID()}`);
  const [attachIdempotencyKey, setAttachIdempotencyKey] = useState<string>(() => `attach_${crypto.randomUUID()}`);

  const [queryResponse, setQueryResponse] = useState<MultiCounterpartyIntelligenceQueryResponse | null>(null);
  const [attachResponse, setAttachResponse] = useState<CrossBankMultiEvidenceAttachResponse | null>(null);

  // Queries & Mutations
  const {
    data: rawEligibleEntities,
    isLoading: isLoadingEntities,
    error: entitiesError,
    refetch: refetchEntities,
  } = useCaseEligibleEntities(caseId);

  const eligibleEntities = Array.isArray(rawEligibleEntities) ? rawEligibleEntities : [];

  const {
    data: rawPersistedEvidence,
    isLoading: isLoadingEvidence,
    refetch: refetchEvidence,
  } = useCaseIntelligenceEvidence(caseId);

  const persistedEvidence = Array.isArray(rawPersistedEvidence) ? rawPersistedEvidence : [];

  // Gate C: Query Capabilities from server-authoritative bilateral grants
  const {
    data: capabilitiesData,
    isLoading: isLoadingCapabilities,
    refetch: refetchCapabilities,
  } = useCrossBankQueryCapabilities(bankId ?? undefined);

  const queryMultiMutation = useQueryMultiCounterpartyIntelligence();
  const attachMultiMutation = useAttachMultiCounterpartyEvidence();

  const notesInputId = useId();

  // Active consortia from query capabilities or fallback
  const activeConsortia = (capabilitiesData?.consortia || []).filter(
    (c) => c.status.toLowerCase() === 'active',
  );

  const effectiveConsortiumId =
    selectedConsortium || (activeConsortia.length > 0 && activeConsortia[0] ? activeConsortia[0].consortium_id : 'consortium_alpha');

  const currentConsortium = activeConsortia.find(
    (c) => c.consortium_id === effectiveConsortiumId,
  );

  const availableCounterparts = currentConsortium?.counterparts || [];

  // Auto-select first entity when loaded if none selected
  const activeEntityId =
    selectedEntityId || (eligibleEntities.length > 0 ? (eligibleEntities[0]?.entity_id ?? '') : '');

  const handleTargetBankToggle = (bankKey: string) => {
    setQueryIdempotencyKey(`query_${crypto.randomUUID()}`);
    setTargetBanks((prev) =>
      prev.includes(bankKey) ? prev.filter((b) => b !== bankKey) : [...prev, bankKey],
    );
  };

  const handleEntityChange = (newEid: string) => {
    setSelectedEntityId(newEid);
    setQueryIdempotencyKey(`query_${crypto.randomUUID()}`);
  };

  const handleConsortiumChange = (newCid: string) => {
    setSelectedConsortium(newCid);
    setQueryIdempotencyKey(`query_${crypto.randomUUID()}`);
  };

  const handlePurposeChange = (newPurp: string) => {
    setPurpose(newPurp);
    setQueryIdempotencyKey(`query_${crypto.randomUUID()}`);
  };

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedHash(id);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  const handleDispatchQuery = async () => {
    if (!activeEntityId || targetBanks.length === 0) return;
    setQueryError(null);
    setAttachResponse(null);

    const effBankId = (bankId || 'bank_a').toLowerCase().trim();

    try {
      const resp = await queryMultiMutation.mutateAsync({
        requesting_bank_id: effBankId,
        query_entity_id: activeEntityId,
        consortium_id: effectiveConsortiumId,
        target_bank_ids: targetBanks,
        case_id: caseId,
        purpose,
        execution_mode: 'VERIFIED_OPERATIONAL',
        use_established_psi: true,
        require_established_psi: true,
        require_hardware_attestation: false,
        idempotency_key: queryIdempotencyKey,
      });
      setQueryResponse(resp);
      // Reset attachment idempotency key for newly received query batch
      setAttachIdempotencyKey(`attach_${crypto.randomUUID()}`);
    } catch (err: unknown) {
      setQueryError(formatApiError(err, 'Multi-counterparty intelligence query execution failed.'));
    }
  };

  const handleAttachEvidence = async () => {
    if (!queryResponse || !caseId) return;
    setAttachError(null);

    try {
      const resp = await attachMultiMutation.mutateAsync({
        query_id: queryResponse.query_id,
        case_id: caseId,
        notes: analystNotes.trim() || undefined,
        idempotency_key: attachIdempotencyKey,
      });
      setAttachResponse(resp);
      refetchEvidence();
    } catch (err: unknown) {
      setAttachError(formatApiError(err, 'Failed to assemble batch evidence into case dossier.'));
    }
  };

  // Gate D: Allow attaching verified results (including negative matches NO_MATCH)
  const hasEligibleSignals =
    queryResponse &&
    queryResponse.results &&
    Object.keys(queryResponse.results).length > 0;

  return (
    <div
      className="p-4 sm:p-6 rounded-2xl bg-gradient-to-br from-slate-900/90 via-slate-900/50 to-indigo-950/30 border border-indigo-500/30 backdrop-blur-md shadow-2xl space-y-6"
      data-testid="cross-bank-intelligence-workspace"
    >
      {/* Workspace Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-indigo-500/20">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-indigo-600/20 border border-indigo-500/30 text-indigo-400">
            <ShieldAlert className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base sm:text-lg font-bold text-slate-100 tracking-wide">
                Cross-Bank Consortium Intelligence Workspace
              </h2>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                M6.6 VERIFIED
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Multi-Counterparty Private Set Intersection (PSI) & Authoritative Case Evidence Assembly
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span className="px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">
            TENANT: {(bankId || 'bank_a').toUpperCase()}
          </span>
          <span className="px-2.5 py-1 rounded-lg text-xs font-mono font-bold bg-emerald-950 text-emerald-300 border border-emerald-500/30 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            OPERATIONAL ASSURANCE
          </span>
          <span className="px-2.5 py-1 rounded-lg text-xs font-mono text-slate-400 bg-slate-950/80 border border-slate-800 flex items-center gap-1">
            <Clock className="w-3.5 h-3.5 text-amber-400" />
            24h Bounded Lifecycle
          </span>
        </div>
      </div>

      {/* Grid: Left Column (Query Builder) & Right Column (Execution / Persisted Trail) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Sub-Panel 1: Query Builder (5 Cols) */}
        <div className="lg:col-span-5 space-y-4">
          <div className="p-4 rounded-xl bg-slate-950/70 border border-indigo-500/20 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
                <Database className="w-4 h-4 text-indigo-400" />
                Authorized Query Builder
              </h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    refetchEntities();
                    refetchCapabilities();
                  }}
                  disabled={isLoadingEntities || isLoadingCapabilities}
                  className="text-[11px] font-mono text-indigo-400 hover:text-indigo-300 flex items-center gap-1 transition"
                  title="Refresh authorized case entities and consortium capabilities"
                >
                  <RefreshCw className={`w-3 h-3 ${(isLoadingEntities || isLoadingCapabilities) ? 'animate-spin' : ''}`} />
                  Refresh
                </button>
              </div>
            </div>

            {/* Entity Selector (Strictly alert-linked entities only; no arbitrary inventory probing) */}
            <div className="space-y-1.5">
              <label htmlFor="eligible-entity-select" className="text-xs font-semibold text-slate-300">
                Suspect Investigation Entity <span className="text-rose-400">*</span>
              </label>
              {isLoadingEntities ? (
                <div className="p-2.5 rounded-lg bg-slate-900 text-xs font-mono text-slate-400 border border-slate-800 animate-pulse">
                  Loading authorized case entities...
                </div>
              ) : entitiesError ? (
                <div className="p-2.5 rounded-lg bg-rose-950/40 border border-rose-500/30 text-rose-300 text-xs font-mono">
                  Failed to load entities: {formatApiError(entitiesError)}
                </div>
              ) : eligibleEntities.length === 0 ? (
                <div className="p-3 rounded-lg bg-amber-950/30 border border-amber-500/30 text-amber-200 text-xs leading-relaxed">
                  No institution-owned entities currently linked to this case. Please link alerts or ensure bank customer/merchant entities exist.
                </div>
              ) : (
                <select
                  id="eligible-entity-select"
                  value={activeEntityId}
                  onChange={(e) => handleEntityChange(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-xs font-mono text-slate-100 focus:outline-none focus:border-indigo-500"
                  data-testid="case-entity-select"
                >
                  {eligibleEntities.map((ent) => (
                    <option key={ent.entity_id} value={ent.entity_id}>
                      {ent.display_label} ({ent.entity_type.toUpperCase()} · Risk: {ent.risk_level.toUpperCase()}
                      {ent.source_alert_id ? ` · Alert: ${ent.source_alert_id.slice(-6)}` : ''})
                    </option>
                  ))}
                </select>
              )}
              <p className="text-[10px] text-slate-500">
                Authoritative local institution-owned entity derived directly from case alert ledger.
              </p>
            </div>

            {/* Consortium Selector (Derived from server-authoritative capabilities) */}
            <div className="space-y-1.5">
              <label htmlFor="consortium-select" className="text-xs font-semibold text-slate-300">
                Governing Consortium <span className="text-rose-400">*</span>
              </label>
              <select
                id="consortium-select"
                value={effectiveConsortiumId}
                onChange={(e) => handleConsortiumChange(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                {activeConsortia.length > 0
                  ? activeConsortia.map((c) => (
                      <option key={c.consortium_id} value={c.consortium_id}>
                        {c.name} ({c.status.toUpperCase()})
                      </option>
                    ))
                  : FALLBACK_CONSORTIUMS.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
              </select>
            </div>

            {/* Target Counterparty Selection (Derived from real pairwise grants) */}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-300">
                Target Counterparty Institutions <span className="text-rose-400">*</span>
              </label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {availableCounterparts.length > 0
                  ? availableCounterparts.map((cp) => {
                      const bankKey = cp.counterpart_bank_id;
                      const isChecked = targetBanks.includes(bankKey);
                      const isAuth = cp.is_authorized;
                      return (
                        <button
                          type="button"
                          key={bankKey}
                          onClick={() => isAuth && handleTargetBankToggle(bankKey)}
                          disabled={!isAuth}
                          className={`px-3 py-2 rounded-lg text-xs font-mono font-bold transition flex flex-col justify-between border text-left ${
                            isChecked
                              ? 'bg-indigo-600/30 border-indigo-500 text-indigo-200'
                              : isAuth
                              ? 'bg-slate-900/60 border-slate-800 text-slate-400 hover:border-slate-700'
                              : 'bg-slate-950/40 border-slate-900 text-slate-600 cursor-not-allowed opacity-60'
                          }`}
                        >
                          <div className="flex items-center justify-between w-full">
                            <span>{cp.display_name || bankKey.toUpperCase()}</span>
                            {isChecked ? <Check className="w-3.5 h-3.5 text-indigo-400" /> : null}
                          </div>
                          <span className={`text-[9px] mt-1 font-mono ${isAuth ? 'text-emerald-400' : 'text-slate-500'}`}>
                            {isAuth ? cp.execution_readiness : cp.governance_status}
                          </span>
                        </button>
                      );
                    })
                  : DEFAULT_COUNTERPARTS.map((bankKey) => {
                      const isChecked = targetBanks.includes(bankKey);
                      return (
                        <button
                          type="button"
                          key={bankKey}
                          onClick={() => handleTargetBankToggle(bankKey)}
                          className={`px-3 py-2 rounded-lg text-xs font-mono font-bold transition flex items-center justify-between border ${
                            isChecked
                              ? 'bg-indigo-600/30 border-indigo-500 text-indigo-200'
                              : 'bg-slate-900/60 border-slate-800 text-slate-400 hover:border-slate-700'
                          }`}
                        >
                          <span>{bankKey.toUpperCase()}</span>
                          {isChecked ? <Check className="w-3.5 h-3.5 text-indigo-400" /> : null}
                        </button>
                      );
                    })}
              </div>
              <p className="text-[10px] text-slate-500">
                Private set intersection is evaluated separately and privately per counterpart institution.
              </p>
            </div>

            {/* Purpose Selector */}
            <div className="space-y-1.5">
              <label htmlFor="purpose-select" className="text-xs font-semibold text-slate-300">
                Regulatory & Investigative Purpose <span className="text-rose-400">*</span>
              </label>
              <select
                id="purpose-select"
                value={purpose}
                onChange={(e) => handlePurposeChange(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-xs text-slate-100 focus:outline-none focus:border-indigo-500"
              >
                {PURPOSE_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Assurance Guarantees Indicator */}
            <div className="p-2.5 rounded-lg bg-indigo-950/40 border border-indigo-500/20 text-[11px] text-indigo-300 space-y-1">
              <div className="flex items-center gap-1.5 font-bold">
                <Lock className="w-3.5 h-3.5 text-indigo-400" />
                Operational Assurance Guarantees
              </div>
              <p className="text-[10px] text-slate-400 leading-normal">
                Strict multi-party Diffie-Hellman / ECDH PSI protocol. Counterparties execute isolated queries without learning raw query identifiers. Anonymous counterparty masking enforced by default policy.
              </p>
            </div>

            {/* Query Dispatch Error */}
            {queryError && (
              <div className="p-3 rounded-lg bg-rose-950/50 border border-rose-500/40 text-rose-200 text-xs flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                <div className="break-all">{queryError}</div>
              </div>
            )}

            {/* Dispatch Action */}
            <button
              type="button"
              onClick={handleDispatchQuery}
              disabled={!activeEntityId || targetBanks.length === 0 || queryMultiMutation.isPending}
              className="w-full py-2.5 px-4 rounded-xl text-xs font-bold tracking-wide uppercase bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white shadow-lg shadow-indigo-600/30 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              data-testid="dispatch-multi-query-btn"
            >
              {queryMultiMutation.isPending ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" />
                  <span>Executing Private Set Intersection...</span>
                </>
              ) : (
                <>
                  <Search className="w-4 h-4" />
                  <span>Dispatch Multi-Counterparty Query</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Sub-Panel 2: Execution Results & Truthful Partial Outcomes (7 Cols) */}
        <div className="lg:col-span-7 space-y-4">
          <div className="p-4 rounded-xl bg-slate-950/70 border border-indigo-500/20 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
                <Layers className="w-4 h-4 text-purple-400" />
                Execution Results & Partial Outcomes
              </h3>
              {queryResponse && (
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase ${
                      queryResponse.overall_outcome === 'COMPLETED'
                        ? 'bg-emerald-950 text-emerald-300 border border-emerald-500/30'
                        : queryResponse.overall_outcome === 'PARTIAL_FAILURE'
                        ? 'bg-amber-950 text-amber-300 border border-amber-500/30'
                        : 'bg-rose-950 text-rose-300 border border-rose-500/30'
                    }`}
                  >
                    {queryResponse.overall_outcome}
                  </span>
                </div>
              )}
            </div>

            {!queryResponse && !queryMultiMutation.isPending ? (
              <div className="py-12 px-4 text-center rounded-xl border border-dashed border-slate-800 text-slate-400 text-xs">
                <Search className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <p className="font-semibold text-slate-300">No active query execution results</p>
                <p className="text-[11px] text-slate-500 mt-1 max-w-sm mx-auto">
                  Select an authorized case entity and target counterparties on the left to execute genuine cross-bank private matching.
                </p>
              </div>
            ) : queryMultiMutation.isPending ? (
              <div className="py-12 px-4 text-center rounded-xl bg-slate-900/40 border border-indigo-500/20 space-y-3">
                <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mx-auto" />
                <div className="text-xs text-indigo-200 font-semibold">
                  Executing Server-Authoritative Multi-Counterparty PSI...
                </div>
                <p className="text-[11px] text-slate-400 max-w-md mx-auto">
                  Coordinating cryptographic zero-knowledge intersection protocols across requested counterpart institutions.
                </p>
              </div>
            ) : queryResponse ? (
              <div className="space-y-4">
                {/* Batch Query Metadata */}
                <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 font-mono">BATCH ID:</span>
                    <span className="font-mono text-cyan-400 font-bold">{queryResponse.query_id}</span>
                    <button
                      type="button"
                      onClick={() => handleCopy(queryResponse.query_id, 'batch-id')}
                      className="p-1 text-slate-400 hover:text-slate-200 transition"
                      title="Copy batch query ID"
                    >
                      {copiedHash === 'batch-id' ? (
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                  <div className="text-[11px] font-mono text-slate-400">
                    {new Date(queryResponse.executed_at).toLocaleTimeString()} · Counterparts:{' '}
                    {queryResponse.counterpart_count}
                  </div>
                </div>

                {/* Per-Counterparty Outcomes */}
                <div className="space-y-2.5" data-testid="counterparty-outcomes-list">
                  {targetBanks.map((bankKey) => {
                    const signal = queryResponse.results ? queryResponse.results[bankKey] : null;
                    const failure = queryResponse.failures ? queryResponse.failures[bankKey] : null;

                    // Gate B: Strictly enforce anonymous masking (null target bank) under ANONYMIZED_SUMMARY
                    const isAnonymized =
                      signal?.disclosure_mode === 'ANONYMIZED_SUMMARY' ||
                      signal?.disclosure_mode === 'ANONYMOUS_FLAG' ||
                      !signal?.target_bank_id;

                    const displayCounterpart = isAnonymized
                      ? 'Anonymous Counterparty'
                      : (signal?.target_bank_id || bankKey).toUpperCase();

                    return (
                      <div
                        key={bankKey}
                        className={`p-3 rounded-xl border transition-all ${
                          signal?.match_confirmed
                            ? 'bg-emerald-950/20 border-emerald-500/40'
                            : signal
                            ? 'bg-slate-900/60 border-slate-800'
                            : failure
                            ? 'bg-rose-950/20 border-rose-500/30'
                            : 'bg-slate-900/40 border-slate-800'
                        }`}
                        data-testid={`outcome-card-${bankKey}`}
                      >
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-mono font-bold text-slate-200">
                              {displayCounterpart}
                            </span>
                            {isAnonymized && signal && (
                              <span className="px-1.5 py-0.5 rounded text-[9px] font-mono bg-purple-900/50 text-purple-300 border border-purple-500/30">
                                ANONYMOUS MASKED
                              </span>
                            )}
                          </div>

                          {signal?.match_confirmed ? (
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 flex items-center gap-1">
                              <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                              MATCH CONFIRMED ({signal.match_category})
                            </span>
                          ) : signal ? (
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700 flex items-center gap-1">
                              <ShieldCheck className="w-3 h-3 text-slate-400" />
                              VERIFIED NO MATCH
                            </span>
                          ) : failure ? (
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-300 border border-rose-500/40 flex items-center gap-1">
                              <XCircle className="w-3 h-3 text-rose-400" />
                              {failure.error_category}
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-500">
                              PENDING
                            </span>
                          )}
                        </div>

                        {/* Signal Match Details */}
                        {signal?.match_confirmed && (
                          <div className="mt-2.5 pt-2.5 border-t border-emerald-500/20 flex flex-wrap items-center gap-3 text-xs">
                            <div className="text-slate-300 font-mono">
                              Confidence: <span className="font-bold text-emerald-300">{(signal.match_confidence * 100).toFixed(0)}%</span>
                            </div>
                            <div className="text-slate-300 font-mono">
                              Risk Level: <span className="font-bold text-amber-300">{signal.sanitized_risk_level}</span>
                            </div>
                            {signal.sanitized_typology_indicators && signal.sanitized_typology_indicators.length > 0 && (
                              <div className="flex items-center gap-1 flex-wrap">
                                {signal.sanitized_typology_indicators.map((typ) => (
                                  <span
                                    key={typ}
                                    className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/20 text-amber-200 border border-amber-500/30"
                                  >
                                    {typ}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Truthful Sanitized Failure Details */}
                        {failure && (
                          <div className="mt-2 pt-2 border-t border-rose-500/20 text-[11px] text-rose-300/90 leading-relaxed font-mono">
                            {failure.error_message}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Batch Evidence Attachment Action */}
                <div className="p-3.5 rounded-xl bg-slate-900/90 border border-indigo-500/30 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-200 flex items-center gap-1.5">
                      <ShieldCheck className="w-4 h-4 text-emerald-400" />
                      Assemble Verified Signals into Case Dossier
                    </span>
                    <span className="text-[10px] font-mono text-slate-400">
                      Single-Case Binding Enforced
                    </span>
                  </div>

                  <div className="space-y-1">
                    <label htmlFor={notesInputId} className="text-xs text-slate-300">
                      Analyst Investigative Notes (Optional)
                    </label>
                    <input
                      id={notesInputId}
                      type="text"
                      placeholder="e.g. Verified multi-counterparty intersection confirmed rapid velocity pattern across peer."
                      value={analystNotes}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setAnalystNotes(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  {attachError && (
                    <div className="p-2.5 rounded-lg bg-rose-950/50 border border-rose-500/40 text-rose-200 text-xs flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
                      <span>{attachError}</span>
                    </div>
                  )}

                  {attachResponse && (
                    <div
                      className="p-3 rounded-lg bg-emerald-950/50 border border-emerald-500/40 text-emerald-200 text-xs space-y-1.5"
                      data-testid="attach-success-banner"
                    >
                      <div className="font-bold flex items-center gap-1.5 text-emerald-300">
                        <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                        Batch Evidence Assembly: {attachResponse.assembly_status}
                      </div>
                      <div className="text-[11px] font-mono text-slate-300 flex items-center gap-3">
                        <span>Newly Attached: {attachResponse.attached_count}</span>
                        <span>Replayed: {attachResponse.replayed_count}</span>
                        <span>Omitted/Failed: {attachResponse.failed_count}</span>
                      </div>
                      {attachResponse.omitted_or_failed && attachResponse.omitted_or_failed.length > 0 && (
                        <div className="pt-1.5 border-t border-emerald-500/20 text-[10px] font-mono text-amber-300 space-y-0.5">
                          {attachResponse.omitted_or_failed.map((fail, idx) => (
                            <div key={idx}>
                              [{fail.category}] {fail.reason}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={handleAttachEvidence}
                    disabled={!hasEligibleSignals || attachMultiMutation.isPending}
                    className="w-full py-2.5 px-4 rounded-xl text-xs font-bold tracking-wide uppercase bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white shadow-lg shadow-emerald-600/20 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    data-testid="attach-batch-evidence-btn"
                  >
                    {attachMultiMutation.isPending ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        <span>Binding Evidence to Case Ledger...</span>
                      </>
                    ) : (
                      <>
                        <ShieldCheck className="w-4 h-4" />
                        <span>Attach Eligible Signals to Case Evidence</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* Sub-Panel 3: Persisted Case Intelligence Evidence Trail */}
      <div className="p-4 sm:p-5 rounded-xl bg-slate-950/80 border border-indigo-500/20 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h3 className="text-xs sm:text-sm font-bold uppercase tracking-wider text-slate-200 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              Persisted Authoritative Intelligence Evidence Trail
            </h3>
            <p className="text-[11px] text-slate-400 mt-0.5">
              Cryptographically verified evidence items permanently committed to this investigation dossier
            </p>
          </div>
          <button
            type="button"
            onClick={() => refetchEvidence()}
            disabled={isLoadingEvidence}
            className="px-2.5 py-1 rounded-lg text-xs font-mono text-indigo-400 hover:text-indigo-300 bg-slate-900 border border-slate-700 flex items-center gap-1 transition"
          >
            <RefreshCw className={`w-3 h-3 ${isLoadingEvidence ? 'animate-spin' : ''}`} />
            Refresh Ledger
          </button>
        </div>

        {isLoadingEvidence ? (
          <div className="py-8 text-center text-xs font-mono text-slate-500 animate-pulse">
            Loading persisted evidence ledger...
          </div>
        ) : persistedEvidence.length === 0 ? (
          <div
            className="py-8 px-4 text-center rounded-xl border border-dashed border-slate-800 text-slate-400 text-xs font-mono"
            data-testid="no-persisted-evidence"
          >
            No cross-bank intelligence evidence registered on this case yet.
          </div>
        ) : (
          <div className="space-y-3" data-testid="persisted-evidence-list">
            {persistedEvidence.map((ev) => {
              const isAnonymized =
                ev.disclosure_mode === 'ANONYMIZED_SUMMARY' ||
                ev.disclosure_mode === 'ANONYMOUS_FLAG' ||
                !ev.target_bank_id;

              const displayCounterpart = isAnonymized
                ? 'Anonymous Counterparty'
                : (ev.target_bank_id || 'Consortium Peer');

              const isExpired = ev.retention_status === 'RETENTION_EXPIRED';

              return (
                <div
                  key={ev.evidence_id}
                  className="p-3.5 rounded-xl bg-slate-900/70 border border-indigo-500/20 hover:border-indigo-500/40 transition space-y-2.5"
                  data-testid={`evidence-item-${ev.evidence_id}`}
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold text-slate-100">{ev.title}</span>
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                        {isAnonymized ? 'ANONYMOUS MASKED' : ev.target_bank_id?.toUpperCase()}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${
                          ev.verification_state === 'VERIFIED'
                            ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                            : ev.verification_state === 'INTEGRITY_VERIFIED_ONLY'
                            ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                            : ev.verification_state === 'FAILED_VERIFICATION'
                            ? 'bg-rose-500/20 text-rose-300 border-rose-500/40'
                            : 'bg-slate-800 text-slate-400 border-slate-700'
                        }`}
                      >
                        {ev.verification_state}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      {isExpired ? (
                        <span
                          className="px-2 py-0.5 rounded text-[10px] font-mono bg-slate-800 text-slate-400 border border-slate-700 flex items-center gap-1"
                          title="Intermediate query batch record expired per 24h retention policy; evidence remains permanently in this case ledger."
                        >
                          <Clock className="w-3 h-3 text-slate-400" />
                          Batch Record Purged (Retention Bounded)
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          Batch Active
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Evidence Payload Details */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                    <div className="p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
                      <span className="text-[10px] uppercase font-mono text-slate-500 block">Counterpart Scope</span>
                      <span className="font-mono text-slate-200 font-semibold">{displayCounterpart}</span>
                    </div>
                    <div className="p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
                      <span className="text-[10px] uppercase font-mono text-slate-500 block">Match Status</span>
                      <span className="font-mono text-emerald-400 font-semibold">
                        {ev.match_confirmed ? `MATCH CONFIRMED (${ev.match_category || 'EXACT'})` : 'NO MATCH'}
                      </span>
                    </div>
                    <div className="p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
                      <span className="text-[10px] uppercase font-mono text-slate-500 block">Assurance Level</span>
                      <span className="font-mono text-cyan-300 font-semibold">{ev.operational_status}</span>
                    </div>
                  </div>

                  {/* Analyst Notes if recorded */}
                  {ev.analyst_notes && (
                    <div className="text-xs text-slate-300 bg-slate-950/40 p-2.5 rounded-lg border border-slate-800">
                      <span className="text-slate-500 font-mono text-[10px] block mb-0.5">Analyst Note:</span>
                      {ev.analyst_notes}
                    </div>
                  )}

                  {/* Verification Explanation */}
                  {ev.explanation && (
                    <div className="text-[11px] font-mono text-slate-400 bg-slate-950/40 p-2 rounded border border-slate-800/80">
                      <span className="text-slate-500 block mb-0.5">Verification Detail:</span>
                      {ev.explanation}
                    </div>
                  )}

                  {/* Cryptographic SHA-256 Digest & Audit Footer */}
                  <div className="pt-2 border-t border-slate-800/60 flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-slate-400">
                    <div className="flex items-center gap-1.5">
                      <span>SHA-256 Digest:</span>
                      <span className="text-cyan-400 font-bold break-all">{ev.content_hash}</span>
                      <button
                        type="button"
                        onClick={() => handleCopy(ev.content_hash, ev.evidence_id)}
                        className="p-1 text-slate-400 hover:text-slate-200 transition"
                        title="Copy cryptographic digest"
                      >
                        {copiedHash === ev.evidence_id ? (
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                      </button>
                    </div>

                    <div className="text-slate-500 text-[10px]">
                      By {ev.uploaded_by} on {new Date(ev.uploaded_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
