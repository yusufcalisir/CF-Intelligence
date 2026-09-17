import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import type {
  Alert,
  AlertDeduplicationStatsResponse,
  AlertStatusUpdateRequest,
  AlertTriageEvaluateRequest,
  AlertTriageEvaluateResponse,
  BankDistributions,
  BankInfo,
  ConsortiumStatusResponse,
  Case,
  CaseSummary,
  CaseEscalatePayload,
  CaseSignPayload,
  CaseResolvePayload,
  TimelineVerificationResponse,
  DashboardStats,
  Entity,
  ExplainabilityReport,
  GraphData,
  GraphStats,
  MuleRingDetectionResponse,
  SmurfingDetectionResponse,
  GraphClusterItem,
  GraphEdgesResponse,
  EntityEmbeddingResponse,
  GNNSimilarityResponse,
  IntelligenceStats,
  RiskWeights,
  ScenarioInfo,
  ScenarioStartResponse,
  ScenarioStatus,
  ScenarioStopResponse,
  ActiveScenarioItem,
  MerchantRiskItem,
  AttackInjectionRequest,
  AttackInjectionResponse,
  SharedIntelligence,
  SimulationConfig,
  SimulationCreateResponse,
  SimulationDetail,
  SimulationStatusResponse,
  SimulationStopRequest,
  SimulationStopResponse,
  SimulationSummary,
  TrainingRound,
  TrainingProgressResponse,
  TrainingMetricsSummaryResponse,
  TrainingHistoryResponse,
  ModelVersion,
  ModelInventoryResponse,
  ModelPromoteRequest,
  ModelPromoteResponse,
  CanaryDecisionItem,
  Evidence,
  InvestigatorAuditLog,
  ShadowMetrics,
  BusinessRule,
  BusinessRuleTestRequest,
  BusinessRuleTestResponse,
  RuleEvaluationRequest,
  RuleEvaluationResponse,
  PSIRequest,
  PSIResponse,
  PSIMatchDirectRequest,
  PSIMatchDirectResponse,
  PSIStatsResponse,
  EntityRelationshipItem,
  EntityDeleteResponse,
  HMACTokenizeRequest,
  HMACTokenizeResponse,
  EntityFuzzyResolveRequest,
  FuzzyMatchResponse,
  CounterfactualExplanation,

  DecisionReplayReport,
  GNNExplanationReport,
  LIMEExplanationReport,
  SecurityStatus,
  ABACEvalRequest,
  ABACEvalResponse,
  AuditChainEntry,

  AuditChainVerifyResponse,
  KMSKeyRotateRequest,
  KMSKeyRotateResponse,
  KMSKeyMetadataResponse,
  VaultSealStatusResponse,
  VerifyZKProofRequest,
  VerifyZKProofResponse,
  ZKVerifierStatusResponse,
  DriftAnalysisReport,
  CalibrationReport,
  ActiveAlertItem,
  RetrainTriggerResponse,
  HealthCheckResponse,
  ReadinessResponse,
  SystemDiagnosticResponse,
  ConceptDriftPsiResponse,
  FairnessMetricsResponse,
  TelemetryOverviewResponse,
  ClientCapabilityItem,
  NegotiatedParamsResponse,
  DatasetPreviewRequest,
  DatasetPreviewResponse,
  DatasetContractAuditRequest,
  DatasetContractAuditResponse,
  DatasetConsortiumEnrollRequest,
  DatasetConsortiumEnrollResponse,
  TuneRequest,
  TuneResponse,
  HyperparameterRangesResponse,
  ParetoFrontResponse,
  UnlearnBankRequest,
  UnlearnBankResponse,
  CalibrateNoiseRequest,
  CalibrateNoiseResponse,
  RDPCompositionRequest,
  RDPCompositionResponse,
  CaseEvidenceDossier,
  CopilotDirectGenerationRequest,
  CopilotQueryRequest,
  CopilotQueryResponse,
  AssembledEvidenceResponse,
  CopilotStatusResponse,
  AnalystFeedbackIngestRequest,
  AnalystFeedbackIngestResponse,
  FeedbackStatsResponse,
  RetrainingBatchRequest,
  RetrainingBatchResponse,
  DPGradientRequest,
  DPGradientResponse,
  ClearFeedbackBufferResponse,
  SARFilingRecord,
  SARValidationResult,
  ExportFinCENXmlRequest,
  ExportFinCENXmlResponse,
  SARGenerateRequest,
  SARFilingDetailResponse,
  RetentionPolicyRequest,
  RetentionPolicyResponse,
  RetentionPurgeRequest,
  GDPRErasureRequest,
  ErasureAuditRecordResponse,
  ErasureChainVerificationResponse,
  FairnessAuditRequest,
  FairnessAuditResponse,
  CanaryGateRequest,
  CanaryGateResponse,
  SOC2EvidenceReportResponse,
  SR117AuditReportResponse,
  SR117ScheduleResponse,
  LoginRequest,
  LoginResponse,
  RefreshTokenRequest,
  UserProfileResponse,
  LogoutResponse,
  LockoutStatusResponse,
  GatewayStatusResponse,
  GatewayHealthResponse,
  GatewayMetricsResponse,
  WebhookSubscriptionRequest,
  WebhookSubscriptionResponse,
  WebhookSubscriptionListResponse,
  WebhookVerifyRequest,
  WebhookVerifyResponse,
  WebhookDeliveryLogsResponse,
  WebhookDeleteResponse,
  WebhookHealthResponse,
  TransactionPredictRequest,
  TransactionPredictResponse,
  BatchPredictionResponse,
  ExplainTransactionResponse,
  ScoreTransactionRequest,
  ScoreTransactionResponse,
  RealtimeInferenceRequest,
  RealtimeInferenceResponse,
  InferenceQuotaResponse,
  PSD2ConsentRequest,
  PSD2ConsentResponse,
  PSD2Account,
  PSD2Transaction,
  PaymentInitiationRequest,
  PaymentInitiationResponse,
  PaymentStatusResponse,
  ISO20022ParseRequest,
  ISO20022ParseResponse,
  BankCABundleResponse,
  PilotLeadRequest,
  PilotLeadResponse,
  AdminDashboardSummaryResponse,
  AdminRoleConfigResponse,
  AdminConfigResponse,
  AdminTenantListResponse,
  MaintenanceWindowResponse,
  CronCleanupResponse,
  CronHealthStatusResponse,
  CronScheduleResponse,
  ConsortiumAlliance,
  ConsortiumMember,
  CreateConsortiumPayload,
  GovernanceProposal,
  CreateMembershipProposalPayload,
  CreatePolicyProposalPayload,
  VoteProposalPayload,
  VoteProposalReceipt,
  CoordinatorClientCapability,
  CoordinatorQuorumStatus,
  CoordinatorAsyncStatus,
  SettlementContractInfo,
  SettlementReceipt,
  SettlementTriggerPayload,
  PayoutClaimPayload,
  PayoutClaimReceipt,
  MultiSigProposal,
  MultiSigProposePayload,
  MultiSigProposeReceipt,
  MultiSigConfirmPayload,
  MultiSigConfirmReceipt,
  MultiSigRevokePayload,
  MultiSigRevokeReceipt,
  QuarantinePayload,
  QuarantineReceipt,
  SlashPenaltyPayload,
  SlashPenaltyReceipt,
  SlashedNodesCatalog,
} from './types';








// ── Phase 1: Simulations ───────────────────

export function useSimulations() {
  return useQuery<SimulationSummary[]>({
    queryKey: ['simulations'],
    queryFn: async () => {
      try {
        const { data } = await apiClient.get('/api/v1/simulations');
        return data;
      } catch {
        // Fallback for cold start / offline backend
        return [];
      }
    },
    refetchInterval: 5000,
  });
}

export function useSimulation(id: string | undefined) {
  return useQuery<SimulationDetail>({
    queryKey: ['simulation', id],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/simulations/${id}`);
      return data;
    },
    enabled: !!id,
    retry: (failureCount, error) => {
      if ((error as any)?.response?.status === 404) return false;
      return failureCount < 2;
    },
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const status = query.state.data?.status;
      if (status === 'completed' || status === 'failed') return false;
      return 2000;
    },
  });
}

export function useAIActComplianceReport(id: string | undefined, enabled: boolean) {
  return useQuery<any>({
    queryKey: ['ai-act-report', id],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/simulations/${id}/ai-act-report`);
      return data;
    },
    enabled: enabled && !!id,
    retry: false,
  });
}

export function useCreateSimulation() {
  return useMutation<SimulationCreateResponse, Error, Partial<SimulationConfig>>({
    mutationFn: async (config) => {
      const { data } = await apiClient.post('/api/v1/simulations', config);
      return data;
    },
  });
}

export function useSimulationStatus(id: string | undefined) {
  return useQuery<SimulationStatusResponse>({
    queryKey: ['simulation-status', id],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/simulation/${id}/status`);
      return data;
    },
    enabled: !!id,
    refetchInterval: (query) => {
      const st = query.state.data?.status;
      if (st === 'completed' || st === 'failed' || st === 'stopped') return false;
      return 1500;
    },
  });
}

export function useStopSimulation() {
  const queryClient = useQueryClient();
  return useMutation<SimulationStopResponse, Error, SimulationStopRequest>({
    mutationFn: async ({ simulation_id, reason }) => {
      const { data } = await apiClient.post(`/api/v1/simulation/${simulation_id}/stop`, null, {
        params: reason ? { reason } : undefined,
      });
      return data;
    },
    onSuccess: (_, { simulation_id }) => {
      queryClient.invalidateQueries({ queryKey: ['simulations'] });
      queryClient.invalidateQueries({ queryKey: ['simulation', simulation_id] });
      queryClient.invalidateQueries({ queryKey: ['simulation-status', simulation_id] });
    },
  });
}

export function useBanks() {
  return useQuery<BankInfo[]>({
    queryKey: ['banks'],
    queryFn: async () => {
      try {
        const { data } = await apiClient.get('/api/v1/banks');
        return data;
      } catch {
        // Fallback mock banks on timeout or cold start
        return [
          { id: 'bank_a', name: 'Bank A — National Trust', tier: 'global', default_transactions: 50000, default_fraud_ratio: 0.012, fraud_pattern: 'High-frequency structuring & card cloning', characteristics: ['Global operations', 'High volume', 'Strict SLA'] },
          { id: 'bank_b', name: 'Bank B — Metro Commercial', tier: 'regional', default_transactions: 35000, default_fraud_ratio: 0.025, fraud_pattern: 'Cross-border wire diversion & synthetic ID', characteristics: ['Regional focus', 'Commercial loans', 'Fast growth'] },
          { id: 'bank_c', name: 'Bank C — Heritage Regional', tier: 'community', default_transactions: 15000, default_fraud_ratio: 0.038, fraud_pattern: 'Account takeover & ATO burst attacks', characteristics: ['Local retail', 'High retail fraud', 'Legacy stack'] },
        ];
      }
    },
  });
}

export function useBankDistributions() {
  return useQuery<BankDistributions>({
    queryKey: ['bank-distributions'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/banks/distributions');
      return data;
    },
    staleTime: 5 * 60 * 1000, // Static data, cache for 5 minutes
  });
}

export interface ScoringVolumePoint {
  time: string;
  volume: number;
}

export function useScoringVolume() {
  return useQuery<ScoringVolumePoint[]>({
    queryKey: ['scoring-volume'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/banks/scoring-volume');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useConsortiumStatus() {
  return useQuery<ConsortiumStatusResponse>({
    queryKey: ['consortium-status'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/banks/status');
      return data;
    },
    staleTime: 30 * 1000,
  });
}

export function useTrainingRounds(simulationId: string | undefined) {
  return useQuery<TrainingRound[]>({
    queryKey: ['training-rounds', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get(
        `/api/v1/training/${simulationId}/rounds`,
      );
      return data;
    },
    enabled: !!simulationId,
    retry: (failureCount, error) => {
      if ((error as any)?.response?.status === 404) return false;
      return failureCount < 3;
    },
    refetchInterval: (query) => {
      if (query.state.error) return false;
      return 1000;
    },
  });
}

export function useTrainingProgress(simulationId?: string) {
  return useQuery<TrainingProgressResponse>({
    queryKey: ['training-progress', simulationId],
    queryFn: async () => {
      const url = simulationId
        ? `/api/v1/training/${simulationId}/progress`
        : '/api/v1/training/progress';
      const { data } = await apiClient.get(url);
      return data;
    },
    refetchInterval: 1000,
  });
}

export function useTrainingMetrics(simulationId?: string) {
  return useQuery<TrainingMetricsSummaryResponse>({
    queryKey: ['training-metrics', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/training/metrics', {
        params: simulationId ? { simulation_id: simulationId } : undefined,
      });
      return data;
    },
    refetchInterval: 3000,
  });
}

export function useTrainingHistory(limit: number = 10) {
  return useQuery<TrainingHistoryResponse>({
    queryKey: ['training-history', limit],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/training/history', {
        params: { limit },
      });
      return data;
    },
    staleTime: 10000,
  });
}

// ── Phase 2: Alerts ────────────────────────

export function useAlerts(filters?: { bank_id?: string; severity?: string; status?: string }) {
  return useQuery<Alert[]>({
    queryKey: ['alerts', filters],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/alerts', { params: filters });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useAlert(id: string | undefined) {
  return useQuery<Alert>({
    queryKey: ['alert', id],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useUpdateAlertStatus() {
  const queryClient = useQueryClient();
  return useMutation<Alert, Error, { alertId: string; payload: AlertStatusUpdateRequest }>({
    mutationFn: async ({ alertId, payload }) => {
      const { data } = await apiClient.patch(`/api/v1/alerts/${alertId}/status`, payload);
      return data;
    },
    onSuccess: (_, { alertId }) => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      queryClient.invalidateQueries({ queryKey: ['alert', alertId] });
    },
  });
}

export function useEvaluateAlertTriage() {
  const queryClient = useQueryClient();
  return useMutation<AlertTriageEvaluateResponse, Error, { alertId: string; payload?: AlertTriageEvaluateRequest }>({
    mutationFn: async ({ alertId, payload }) => {
      const { data } = await apiClient.post(`/api/v1/alerts/${alertId}/triage`, payload ?? {});
      return data;
    },
    onSuccess: (_, { alertId }) => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      queryClient.invalidateQueries({ queryKey: ['alert', alertId] });
    },
  });
}

export function useAlertDedupStats() {
  return useQuery<AlertDeduplicationStatsResponse>({
    queryKey: ['alert-dedup-stats'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/alerts/dedup/stats');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useTransactionExplainability(transactionId: string | undefined) {
  return useQuery<ExplainabilityReport>({
    queryKey: ['transaction-explain', transactionId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/explanation/${transactionId}`);
      return data;
    },
    enabled: !!transactionId,
  });
}

export function useAlertExplainability(alertId: string | undefined) {
  return useQuery<ExplainabilityReport>({
    queryKey: ['alert-explain', alertId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${alertId}/explain`);
      return data;
    },
    enabled: !!alertId,
  });
}

export function useIntelligence(bankId?: string) {
  return useQuery<SharedIntelligence[]>({
    queryKey: ['intelligence', bankId],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/intelligence', {
        params: bankId ? { bank_id: bankId } : {},
      });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useIntelligenceStats() {
  return useQuery<IntelligenceStats>({
    queryKey: ['intelligence-stats'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/intelligence/stats');
      return data;
    },
    refetchInterval: 10000,
  });
}

// ── Phase 2: Cases ─────────────────────────

export function useCases(filters?: { status?: string; priority?: string }) {
  return useQuery<CaseSummary[]>({
    queryKey: ['cases', filters],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/cases', { params: filters });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useCase(id: string | undefined) {
  return useQuery<Case>({
    queryKey: ['case', id],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateCase() {
  const queryClient = useQueryClient();
  return useMutation<Case, Error, { title: string; priority?: string; alert_ids?: string[] }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/cases', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
    },
  });
}

export function useUpdateCaseStatus() {
  const queryClient = useQueryClient();
  return useMutation<
    Case,
    Error,
    {
      caseId: string;
      status: string;
      actor?: string;
      supervisor_signature?: string;
      second_supervisor_signature?: string;
      supervisor_signatures?: string[];
    }
  >({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.patch(`/api/v1/cases/${caseId}`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
    },
  });
}

export function useAddCaseNote() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, { caseId: string; author: string; content: string }>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/notes`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
    },
  });
}

export function useEscalateCase() {
  const queryClient = useQueryClient();
  return useMutation<Case, Error, CaseEscalatePayload>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/escalate`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
    },
  });
}

export function useSignCase() {
  const queryClient = useQueryClient();
  return useMutation<Case, Error, CaseSignPayload>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/sign`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
    },
  });
}

export function useResolveCase() {
  const queryClient = useQueryClient();
  return useMutation<Case, Error, CaseResolvePayload>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/resolve`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['cases'] });
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
    },
  });
}

export function useLinkAlertToCase() {
  const queryClient = useQueryClient();
  return useMutation<Case, Error, { caseId: string; alert_id: string }>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/alerts`, body);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['case', variables.caseId] });
      queryClient.invalidateQueries({ queryKey: ['cases'] });
    },
  });
}

export function useCaseTimeline(caseId: string | undefined) {
  return useQuery({
    queryKey: ['case-timeline', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${caseId}/timeline`);
      return data;
    },
    enabled: !!caseId,
  });
}

export function useVerifyCaseTimeline(caseId: string | undefined) {
  return useQuery<TimelineVerificationResponse>({
    queryKey: ['case-timeline-verify', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${caseId}/timeline/verify`);
      return data;
    },
    enabled: !!caseId,
  });
}

export const useExportFinCENXml = useExportFinCENXmlMutation;


// ── Phase 2: Entities ──────────────────────

export function useEntities(filters?: { entity_type?: string; bank_id?: string; risk_level?: string }) {
  return useQuery<Entity[]>({
    queryKey: ['entities', filters],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/entities', { params: filters });
      return data;
    },
    refetchInterval: 10000,
  });
}

// ── Phase 2: Graph ─────────────────────────

export function useGraph(entityId: string | undefined, depth: number = 2, maxNodes: number = 100) {
  return useQuery<GraphData>({
    queryKey: ['graph', entityId, depth, maxNodes],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/graph/${entityId}`, {
        params: { depth, max_nodes: maxNodes },
      });
      return data;
    },
    enabled: !!entityId,
  });
}

export function useGraphStats() {
  return useQuery<GraphStats>({
    queryKey: ['graph-stats'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/graph/stats/summary');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useDetectMuleRings(minLength = 3, maxLength = 7, bankId?: string) {
  return useQuery<MuleRingDetectionResponse>({
    queryKey: ['mule-rings', minLength, maxLength, bankId],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/graph/rings', {
        params: { min_length: minLength, max_length: maxLength, bank_id: bankId },
      });
      return data;
    },
    refetchInterval: 15000,
  });
}

export function useDetectSmurfing(windowHours = 24, minFan = 3, maxDepth = 3, bankId?: string) {
  return useQuery<SmurfingDetectionResponse>({
    queryKey: ['smurfing-patterns', windowHours, minFan, maxDepth, bankId],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/graph/smurfing/detect', {
        params: { window_hours: windowHours, min_fan: minFan, max_depth: maxDepth, bank_id: bankId },
      });
      return data;
    },
  });
}

export function useGraphClusters(minSize = 3) {
  return useQuery<GraphClusterItem[]>({
    queryKey: ['graph-clusters', minSize],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/graph/clusters/list', {
        params: { min_size: minSize },
      });
      return data;
    },
  });
}

export function useGraphEdges(sourceId?: string, targetId?: string, limit = 50) {
  return useQuery<GraphEdgesResponse>({
    queryKey: ['graph-edges', sourceId, targetId, limit],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/graph/edges', {
        params: { source_id: sourceId, target_id: targetId, limit },
      });
      return data;
    },
  });
}

export function useNodeEmbedding(entityId: string | undefined) {
  return useQuery<EntityEmbeddingResponse>({
    queryKey: ['node-embedding', entityId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/graph/embeddings/${entityId}`);
      return data;
    },
    enabled: !!entityId,
  });
}

export function useSimilarEntities() {
  return useMutation<GNNSimilarityResponse, Error, { entity_id: string; top_k?: number; threshold?: number }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/graph/embeddings/similar', payload);
      return data;
    },
  });
}

// ── Phase 2: Scenarios ─────────────────────

export function useScenarios() {
  return useQuery<ScenarioInfo[]>({
    queryKey: ['scenarios'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/scenarios');
      return data;
    },
  });
}

export function useStartScenario() {
  return useMutation<ScenarioStartResponse, Error, { scenario_type: string; speed_multiplier?: number }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/scenarios/start', payload, { timeout: 60000 });
      return data;
    },
  });
}

export function useScenarioStatus(scenarioId: string | undefined) {
  return useQuery<ScenarioStatus>({
    queryKey: ['scenario-status', scenarioId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/scenarios/${scenarioId}/status`, { timeout: 30000 });
      return data;
    },
    enabled: !!scenarioId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'completed' || status === 'stopped' || status === 'failed') {
        return false;
      }
      return 1500;
    },
    retry: 2,
    retryDelay: 1000,
  });
}

export function useInjectAttack() {
  return useMutation<AttackInjectionResponse, Error, AttackInjectionRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/scenarios/inject-attack', payload);
      return data;
    },
  });
}

export function useStopScenario() {
  const queryClient = useQueryClient();
  return useMutation<ScenarioStopResponse, Error, string>({
    mutationFn: async (scenarioId: string) => {
      const { data } = await apiClient.post(`/api/v1/scenarios/${scenarioId}/stop`);
      return data;
    },
    onSuccess: (_, scenarioId) => {
      queryClient.invalidateQueries({ queryKey: ['scenario-status', scenarioId] });
      queryClient.invalidateQueries({ queryKey: ['active-scenarios'] });
    },
  });
}

export function useActiveScenarios() {
  return useQuery<ActiveScenarioItem[]>({
    queryKey: ['active-scenarios'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/scenarios/active/list');
      return data;
    },
    refetchInterval: 5000,
  });
}

// ── Phase 2: Dashboard ─────────────────────

export function useDashboardStats(bankId?: string) {
  return useQuery<DashboardStats>({
    queryKey: ['dashboard-stats', bankId],
    queryFn: async () => {
      const params = bankId ? { bank_id: bankId } : undefined;
      const { data } = await apiClient.get('/api/v1/dashboard/stats', { params });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useRiskWeights() {
  return useQuery<RiskWeights>({
    queryKey: ['risk-weights'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/dashboard/risk-weights');
      return data;
    },
  });
}

export function useUpdateRiskWeights() {
  const queryClient = useQueryClient();
  return useMutation<RiskWeights, Error, Partial<RiskWeights>>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.put('/api/v1/dashboard/risk-weights', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['risk-weights'] });
    },
  });
}

export function useAlertsBySeverity(bankId?: string) {
  return useQuery<Record<string, number>>({
    queryKey: ['alerts-by-severity', bankId],
    queryFn: async () => {
      const params = bankId ? { bank_id: bankId } : undefined;
      const { data } = await apiClient.get('/api/v1/dashboard/alerts-by-severity', { params });
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useAlertsByBank() {
  return useQuery<Record<string, number>>({
    queryKey: ['alerts-by-bank'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/dashboard/alerts-by-bank');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useEntitiesByRisk(bankId?: string) {
  return useQuery<Record<string, number>>({
    queryKey: ['entities-by-risk', bankId],
    queryFn: async () => {
      const params = bankId ? { bank_id: bankId } : undefined;
      const { data } = await apiClient.get('/api/v1/dashboard/entities-by-risk', { params });
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useTopRiskyMerchants(limit = 10, bankId?: string) {
  return useQuery<MerchantRiskItem[]>({
    queryKey: ['top-risky-merchants', limit, bankId],
    queryFn: async () => {
      const params: Record<string, unknown> = { limit };
      if (bankId) params.bank_id = bankId;
      const { data } = await apiClient.get('/api/v1/dashboard/top-risky-merchants', { params });
      return data;
    },
    refetchInterval: 10000,
  });
}

// ── Model Registry & Rollback ──────────────

export function useModelVersions(simulationId: string | undefined) {
  return useQuery<ModelVersion[]>({
    queryKey: ['model-versions', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/registry/${simulationId}/versions`);
      return data;
    },
    enabled: !!simulationId,
    refetchInterval: 3000,
  });
}

export function useRollbackModel() {
  return useMutation<ModelVersion, Error, { simulationId: string; version: number }>({
    mutationFn: async ({ simulationId, version }) => {
      const { data } = await apiClient.post(`/api/v1/registry/${simulationId}/rollback/${version}`);
      return data;
    },
  });
}

export function useCanaryHistory(simulationId: string | undefined) {
  return useQuery<CanaryDecisionItem[]>({
    queryKey: ['canary-history', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get<CanaryDecisionItem[]>(`/api/v1/registry/${simulationId}/canary`);
      return data;
    },
    enabled: !!simulationId,
    refetchInterval: 3000,
  });
}

export function useModelInventory() {
  return useQuery<ModelInventoryResponse>({
    queryKey: ['model-inventory'],
    queryFn: async () => {
      const { data } = await apiClient.get<ModelInventoryResponse>('/api/v1/models');
      return data;
    },
    staleTime: 10 * 1000,
  });
}

export function usePromoteModelVersion() {
  const queryClient = useQueryClient();
  return useMutation<
    ModelPromoteResponse,
    Error,
    { simulationId: string; version: number; payload?: ModelPromoteRequest }
  >({
    mutationFn: async ({ simulationId, version, payload }) => {
      const { data } = await apiClient.post<ModelPromoteResponse>(
        `/api/v1/registry/${simulationId}/versions/${version}/promote`,
        payload || { target_status: 'champion', enforce_sr11_7: true }
      );
      return data;
    },
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['model-versions', vars.simulationId] });
      queryClient.invalidateQueries({ queryKey: ['model-inventory'] });
    },
  });
}


export function useCaseEvidence(caseId: string | undefined) {
  return useQuery<Evidence[]>({
    queryKey: ['case-evidence', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${caseId}/evidence`);
      return data;
    },
    enabled: !!caseId,
    refetchInterval: 5000,
  });
}

export function useAddEvidence() {
  return useMutation<Evidence, Error, { caseId: string; evidence_type: string; title: string; file_path: string; content: string; uploaded_by?: string }>({
    mutationFn: async ({ caseId, ...body }) => {
      const { data } = await apiClient.post(`/api/v1/cases/${caseId}/evidence`, body);
      return data;
    },
  });
}

export function useAuditLogs() {
  return useQuery<InvestigatorAuditLog[]>({
    queryKey: ['audit-logs'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/cases/audit/logs');
      return data;
    },
    retry: false,
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });
}

export function useLogSessionDuration() {
  return useMutation<unknown, Error, { investigator: string; duration_seconds: number }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/cases/audit/session', payload);
      return data;
    },
  });
}

export function useSignOffModel() {
  return useMutation<
    ModelVersion,
    Error,
    {
      simulationId: string;
      version: number;
      role: 'compliance' | 'ml_engineer';
      user: string;
      signature: string;
      fairness_score: number;
      bias_metric: number;
      drift_divergence: number;
    }
  >({
    mutationFn: async ({ simulationId, version, ...body }) => {
      const { data } = await apiClient.post(
        `/api/v1/registry/${simulationId}/versions/${version}/signoff`,
        body
      );
      return data;
    },
  });
}

export function useShadowMetrics(simulationId: string | undefined) {
  return useQuery<ShadowMetrics>({
    queryKey: ['shadow-metrics', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/registry/${simulationId}/shadow/metrics`);
      return data;
    },
    enabled: !!simulationId,
    retry: false,
    refetchInterval: (query) => (query.state.error ? false : 3000),
  });
}

export function useSubmitFeedback() {
  return useMutation<unknown, Error, { simulationId?: string; simulation_id?: string; transaction_id: string; actual_label: number }>({
    mutationFn: async (payload) => {
      const simId = payload.simulation_id || payload.simulationId || 'live_prod_v2';
      const body = {
        transaction_id: payload.transaction_id,
        actual_label: payload.actual_label,
        simulation_id: simId,
        simulationId: simId,
      };
      const { data } = await apiClient.post('/api/v1/predict/feedback', body);
      return data;
    },
  });
}

export function useRules() {
  return useQuery<BusinessRule[]>({
    queryKey: ['business-rules'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/rules');
      return data;
    },
    retry: false,
  });
}

export function useCreateRule() {
  const queryClient = useQueryClient();
  return useMutation<BusinessRule, Error, { rule_name: string; condition: Record<string, any>; action: string; is_active: boolean }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/rules', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-rules'] });
    },
  });
}

export function useUpdateRule() {
  const queryClient = useQueryClient();
  return useMutation<BusinessRule, Error, { id: string; rule_name?: string; condition?: Record<string, any>; action?: string; is_active?: boolean }>({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await apiClient.put(`/api/v1/rules/${id}`, payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-rules'] });
    },
  });
}

export function useDeleteRule() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: async (id) => {
      const { data } = await apiClient.delete(`/api/v1/rules/${id}`);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['business-rules'] });
    },
  });
}

export function useRuleDetails(ruleId: string) {
  return useQuery<BusinessRule>({
    queryKey: ['business-rule', ruleId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/rules/${ruleId}`);
      return data;
    },
    enabled: Boolean(ruleId),
    retry: false,
  });
}

export function useTestRule() {
  return useMutation<BusinessRuleTestResponse, Error, BusinessRuleTestRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/rules/test', payload);
      return data;
    },
  });
}

export function useEvaluateRules() {
  return useMutation<RuleEvaluationResponse, Error, RuleEvaluationRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/rules/evaluate', payload);
      return data;
    },
  });
}


export function useRunPSI() {
  return useMutation<PSIResponse, Error, PSIRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/entities/psi', payload);
      return data;
    },
  });
}

export function useFuzzyResolve() {
  return useMutation<FuzzyMatchResponse[], Error, EntityFuzzyResolveRequest>({
    mutationFn: async (payload) => {
      const formattedPayload = {
        query_name: payload.query_name || payload.raw_identifier || '',
        raw_identifier: payload.raw_identifier || payload.query_name || '',
        entity_type: payload.entity_type || 'customer',
        threshold: payload.threshold ?? payload.similarity_threshold ?? 0.7,
        similarity_threshold: payload.similarity_threshold ?? payload.threshold ?? 0.7,
        bank_id: payload.bank_id,
        limit: payload.limit ?? 50,
      };
      const { data } = await apiClient.post('/api/v1/entities/fuzzy-resolve', formattedPayload);
      if (data && Array.isArray(data.matches)) {
        return data.matches.map((m: any) => ({
          entity_id: m.entity?.id || m.entity_id || '',
          display_label: m.entity?.display_label || m.display_label || '',
          entity_type: m.entity?.entity_type || m.entity_type || '',
          bank_id: m.entity?.bank_id || m.bank_id || '',
          risk_level: m.entity?.risk_level || m.risk_level || '',
          privacy_id: m.entity?.privacy_id || m.privacy_id || '',
          similarity: m.similarity_score ?? m.similarity ?? 0,
          standardized_stored:
            m.entity?.attributes?.raw_standardized || m.standardized_stored || '',
        }));
      }
      return Array.isArray(data) ? data : [];
    },
  });
}

export function useAlertCounterfactuals(alertId: string | undefined, targetScore: number = 350.0) {
  return useQuery<CounterfactualExplanation>({
    queryKey: ['alert-counterfactuals', alertId, targetScore],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${alertId}/counterfactuals`, {
        params: { target_score: targetScore },
      });
      return data;
    },
    enabled: !!alertId,
  });
}

export function useAlertDecisionReplay(alertId: string | undefined) {
  return useQuery<DecisionReplayReport>({
    queryKey: ['alert-decision-replay', alertId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${alertId}/decision-replay`);
      return data;
    },
    enabled: !!alertId,
  });
}

export function useAlertGNNExplanation(alertId: string | undefined) {
  return useQuery<GNNExplanationReport>({
    queryKey: ['alert-gnn-explanation', alertId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${alertId}/gnn-explanation`);
      return data;
    },
    enabled: !!alertId,
  });
}

export function useAlertLIMEExplanation(
  alertId: string | undefined,
  kernelWidth: number = 0.75,
  numSamples: number = 100
) {
  return useQuery<LIMEExplanationReport>({
    queryKey: ['alert-lime-explanation', alertId, kernelWidth, numSamples],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/alerts/${alertId}/lime-explanation`, {
        params: { kernel_width: kernelWidth, num_samples: numSamples },
      });
      return data;
    },
    enabled: !!alertId,
  });
}

export function useSecurityStatus() {
  return useQuery<SecurityStatus>({
    queryKey: ['security-status'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/security/status');
      return data;
    },
    retry: false,
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });
}

export function useEvaluateABAC() {
  return useMutation<ABACEvalResponse, Error, ABACEvalRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/security/abac/evaluate', payload);
      return data;
    },
  });
}

export function useAuditChain(limit: number = 50) {
  return useQuery<AuditChainEntry[]>({
    queryKey: ['audit-chain', limit],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/security/audit-chain', {
        params: { limit },
      });
      return data;
    },
    retry: false,
    refetchInterval: (query) => (query.state.error ? false : 5000),
  });
}

export function useVerifyAuditChain() {
  return useMutation<AuditChainVerifyResponse, Error, void>({
    mutationFn: async () => {
      const { data } = await apiClient.post('/api/v1/security/audit-chain/verify');
      return data;
    },
  });
}

export function useRotateKMSKey() {
  const queryClient = useQueryClient();
  return useMutation<KMSKeyRotateResponse, Error, KMSKeyRotateRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<KMSKeyRotateResponse>('/api/v1/security/kms/rotate', payload);
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['kms-key-metadata', variables.bank_id] });
    },
  });
}

export function useKMSKeyMetadata(bankId: string) {
  return useQuery<KMSKeyMetadataResponse>({
    queryKey: ['kms-key-metadata', bankId],
    queryFn: async () => {
      const { data } = await apiClient.get<KMSKeyMetadataResponse>(`/api/v1/security/kms/keys/${bankId}`);
      return data;
    },
    enabled: !!bankId,
  });
}

export function useVaultSealStatus() {
  return useQuery<VaultSealStatusResponse>({
    queryKey: ['vault-seal-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<VaultSealStatusResponse>('/api/v1/security/vault/seal-status');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useVerifyZKProof() {
  return useMutation<VerifyZKProofResponse, Error, VerifyZKProofRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<VerifyZKProofResponse>('/api/v1/security/zk/verify', payload);
      return data;
    },
  });
}

export function useZKVerifierStatus() {
  return useQuery<ZKVerifierStatusResponse>({
    queryKey: ['zk-verifier-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<ZKVerifierStatusResponse>('/api/v1/security/zk/status');
      return data;
    },
  });
}


export function useDriftAnalysis(severeDrift: boolean = false) {

  return useQuery<DriftAnalysisReport>({
    queryKey: ['drift-analysis', severeDrift],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/drift/analyze', {
        params: { severe_drift: severeDrift },
      });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useCalibrationReport() {
  return useQuery<CalibrationReport>({
    queryKey: ['monitoring-calibration'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/calibration');
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useActiveAlerts() {
  return useQuery<ActiveAlertItem[]>({
    queryKey: ['monitoring-alerts'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/alerts');
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useTriggerAutoRetrain() {
  return useMutation<RetrainTriggerResponse, Error, string | undefined>({
    mutationFn: async (reason) => {
      const { data } = await apiClient.post('/api/v1/monitoring/drift/trigger-retrain', null, {
        params: { reason },
      });
      return data;
    },
  });
}

export function useHealth() {
  return useQuery<HealthCheckResponse>({
    queryKey: ['platform-health'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/health');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useReadiness() {
  return useQuery<ReadinessResponse>({
    queryKey: ['platform-readiness'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/health/ready');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useSystemDiagnostics() {
  return useQuery<SystemDiagnosticResponse>({
    queryKey: ['system-diagnostics'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/diagnostics/system');
      return data;
    },
    refetchInterval: 15000,
  });
}

export function useConceptDriftPsi() {
  return useQuery<ConceptDriftPsiResponse>({
    queryKey: ['monitoring-concept-drift-psi'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/drift/psi');
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useFairnessMetrics() {
  return useQuery<FairnessMetricsResponse>({
    queryKey: ['monitoring-fairness-metrics'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/fairness');
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useTelemetryOverview() {
  return useQuery<TelemetryOverviewResponse>({
    queryKey: ['monitoring-telemetry-overview'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/monitoring/telemetry');
      return data;
    },
    refetchInterval: 5000,
  });
}

// ── Coordinator Hooks (Item 18) ───────────────────────────────

export function useRegisteredClients() {
  return useQuery<ClientCapabilityItem[]>({
    queryKey: ['coordinator', 'clients'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/coordinator/clients');
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useNegotiatedParams(bankId: string, baseBatchSize: number, baseEpochs: number) {
  return useQuery<NegotiatedParamsResponse>({
    queryKey: ['coordinator', 'negotiate', bankId, baseBatchSize, baseEpochs],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/coordinator/negotiate', {
        params: { bank_id: bankId, base_batch_size: baseBatchSize, base_epochs: baseEpochs },
      });
      return data;
    },
    enabled: !!bankId,
  });
}



// ── Privacy Defense Suite (Item 19) ──────────────────────────

export function useAggregationMethods() {
  return useQuery<import('./types').AggregationMethodInfo[]>({
    queryKey: ['privacy-defense', 'aggregation-methods'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/privacy-defense/aggregation-methods');
      return data;
    },
    staleTime: Infinity,
  });
}

export function usePrivacyBudgetLog(epsilonLimit = 8.0) {
  return useQuery<import('./types').BudgetLogEntry[]>({
    queryKey: ['privacy-defense', 'budget-log', epsilonLimit],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/privacy-defense/budget-log', {
        params: { epsilon_limit: epsilonLimit },
      });
      return data;
    },
    refetchInterval: 5000,
  });
}

export function useAuditMIA() {
  return useMutation<
    import('./types').MIAAuditResult,
    Error,
    { train_losses: number[]; test_losses: number[] }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/privacy-defense/audit/mia', payload);
      return data;
    },
  });
}

export function useAuditModelInversion() {
  return useMutation<
    import('./types').ModelInversionAuditResult,
    Error,
    { gradient_norms: number[] }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post(
        '/api/v1/privacy-defense/audit/model-inversion',
        payload,
      );
      return data;
    },
  });
}

export function useAuditDLG() {
  return useMutation<
    import('./types').DLGAuditResult,
    Error,
    { original_gradients: number[]; received_gradients: number[] }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/privacy-defense/audit/dlg', payload);
      return data;
    },
  });
}

// ── Real-World Benchmark & Design Partner Pilot Hooks ────

export function useBenchmarkEvaluation(dataset: string = 'paysim', nSamples: number = 10000, dailyVolume: number = 100000) {
  return useQuery<import('./types').BenchmarkEvaluationResponse>({
    queryKey: ['benchmark-evaluation', dataset, nSamples, dailyVolume],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/design-partner/evaluate-benchmark', {
        params: { dataset, n_samples: nSamples, daily_volume: dailyVolume },
      });
      return data;
    },
    staleTime: 60000,
  });
}

export function useDistributionFidelity(dataset: string = 'paysim') {
  return useQuery<import('./types').DistributionFidelityReport>({
    queryKey: ['distribution-fidelity', dataset],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/design-partner/distribution-fidelity', {
        params: { dataset },
      });
      return data;
    },
    staleTime: 60000,
  });
}

export function usePilotReadinessChecklist(partnerName: string = 'Design Partner Bank', jurisdiction: string = 'EU/TR/US') {
  return useQuery<import('./types').PilotComplianceChecklist>({
    queryKey: ['pilot-readiness', partnerName, jurisdiction],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/design-partner/readiness-checklist', {
        params: { partner_name: partnerName, jurisdiction },
      });
      return data;
    },
  });
}

export function useValidateDataIngestion() {
  return useMutation<
    import('./types').PiiValidationResponse,
    Error,
    { partner_name: string; schema_format: string; sample_records: Array<Record<string, any>> }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/design-partner/validate-ingest', payload);
      return data;
    },
  });
}

// ── Connector Diagnostics & Infrastructure Health ────

export function useConnectorDiagnostics() {
  return useQuery<import('./types').DiagnosticsOverviewResponse>({
    queryKey: ['connector-diagnostics'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/diagnostics/connectors', {
        timeout: 5000,
      });
      return data;
    },
    refetchInterval: 15000,
  });
}

export function useTestConnector() {
  const queryClient = useQueryClient();
  return useMutation<
    import('./types').ConnectorTestProbeResult,
    Error,
    { connector_id: string }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/diagnostics/test-connector', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connector-diagnostics'] });
    },
  });
}

// ── Real Dataset Ingestion Studio Hooks ──────────

export function useValidateDatasetPreview() {
  return useMutation<DatasetPreviewResponse, Error, DatasetPreviewRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/datasets/validate-preview', payload);
      return data;
    },
  });
}

export function useAuditDatasetContract() {
  return useMutation<DatasetContractAuditResponse, Error, DatasetContractAuditRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/datasets/contract-audit', payload);
      return data;
    },
  });
}

export function useEnrollDatasetConsortium() {
  const queryClient = useQueryClient();
  return useMutation<DatasetConsortiumEnrollResponse, Error, DatasetConsortiumEnrollRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/datasets/consortium-enroll', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bank-distributions'] });
      queryClient.invalidateQueries({ queryKey: ['coordinator-nodes'] });
    },
  });
}

export function useOptimizationStudies() {
  return useQuery<string[]>({
    queryKey: ['optimization-studies'],
    queryFn: async () => {
      const { data } = await apiClient.get<string[]>('/v1/admin/optimization/studies');
      return data;
    },
    retry: false,
  });
}

export function useOptimizationStudyDetails(studyName: string | undefined) {
  return useQuery<TuneResponse>({
    queryKey: ['optimization-study', studyName],
    queryFn: async () => {
      const { data } = await apiClient.get<TuneResponse>(`/v1/admin/optimization/studies/${studyName}`);
      return data;
    },
    enabled: !!studyName,
    retry: false,
  });
}

export function useTriggerHyperparameterTuning() {
  const queryClient = useQueryClient();
  return useMutation<TuneResponse, Error, TuneRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<TuneResponse>('/v1/admin/optimization/tune', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['optimization-studies'] });
    },
  });
}

export function useHyperparameterRanges() {
  return useQuery<HyperparameterRangesResponse>({
    queryKey: ['optimization-hyperparameters'],
    queryFn: async () => {
      const { data } = await apiClient.get<HyperparameterRangesResponse>('/api/v1/optimization/hyperparameters');
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useParetoFront(studyName?: string) {
  return useQuery<ParetoFrontResponse>({
    queryKey: ['optimization-pareto', studyName],
    queryFn: async () => {
      const { data } = await apiClient.get<ParetoFrontResponse>('/api/v1/optimization/pareto', {
        params: studyName ? { study_name: studyName } : undefined,
      });
      return data;
    },
  });
}

export function useTriggerUnlearning() {
  return useMutation<UnlearnBankResponse, Error, UnlearnBankRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<UnlearnBankResponse>('/api/v1/security/unlearn', payload);
      return data;
    },
  });
}

export function useCalibrateNoise() {
  return useMutation<CalibrateNoiseResponse, Error, CalibrateNoiseRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<CalibrateNoiseResponse>(
        '/api/v1/privacy-defense/calibrate-noise',
        payload
      );
      return data;
    },
  });
}

export function useRDPComposition() {
  return useMutation<RDPCompositionResponse, Error, RDPCompositionRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<RDPCompositionResponse>(
        '/api/v1/privacy-defense/rdp-composition',
        payload
      );
      return data;
    },
  });
}

// ── Autonomous Agentic AML Copilot & Evidence Assembly Hooks ───

export function useGenerateCopilotNarrative() {
  const queryClient = useQueryClient();
  return useMutation<CopilotQueryResponse, Error, { caseId: string; request?: CopilotQueryRequest }>({
    mutationFn: async ({ caseId, request }) => {
      const { data } = await apiClient.post<CopilotQueryResponse>(
        `/api/v1/cases/${caseId}/copilot/narrative`,
        request || { case_id: caseId }
      );
      return data;
    },
    onSuccess: (_, { caseId }) => {
      queryClient.invalidateQueries({ queryKey: ['copilot-summary', caseId] });
      queryClient.invalidateQueries({ queryKey: ['copilot-evidence', caseId] });
    },
  });
}

export function useCopilotSummary(caseId: string | undefined) {
  return useQuery<{
    case_id: string;
    recommended_action: string;
    top_risk_drivers: Array<{ feature: string; impact: number; description?: string }>;
    graph_topology_summary: Record<string, unknown>;
    zero_pii_verified: boolean;
    lineage_hash: string;
    evidence_count?: number;
    timeline_event_count?: number;
    evidence_hash?: string;
  }>({
    queryKey: ['copilot-summary', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${caseId}/copilot/summary`);
      return data;
    },
    enabled: !!caseId,
    retry: false,
  });
}

export function useCaseEvidenceDossier(caseId: string | undefined) {
  return useQuery<CaseEvidenceDossier>({
    queryKey: ['copilot-evidence', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/cases/${caseId}/copilot/evidence`);
      return data;
    },
    enabled: !!caseId,
    retry: false,
  });
}

export function useDirectGenerateSAR() {
  return useMutation<CopilotQueryResponse, Error, CopilotDirectGenerationRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<CopilotQueryResponse>(
        '/api/v1/copilot/generate-sar',
        payload
      );
      return data;
    },
  });
}

export function useAssembleEvidence() {
  return useMutation<AssembledEvidenceResponse, Error, CopilotDirectGenerationRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<AssembledEvidenceResponse>(
        '/api/v1/copilot/assemble-evidence',
        payload
      );
      return data;
    },
  });
}

export function useCopilotStatus() {
  return useQuery<CopilotStatusResponse>({
    queryKey: ['copilot-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<CopilotStatusResponse>('/api/v1/copilot/status');
      return data;
    },
    refetchInterval: 30000,
  });
}

export function useCopilotCaseEvidence(caseId: string | undefined) {
  return useQuery<AssembledEvidenceResponse>({
    queryKey: ['copilot-case-evidence', caseId],
    queryFn: async () => {
      const { data } = await apiClient.get<AssembledEvidenceResponse>(
        `/api/v1/copilot/cases/${caseId}/evidence`
      );
      return data;
    },
    enabled: !!caseId,
    retry: false,
  });
}

export function useCopilotCaseNarrative() {
  const queryClient = useQueryClient();
  return useMutation<CopilotQueryResponse, Error, { caseId: string; request?: CopilotQueryRequest }>({
    mutationFn: async ({ caseId, request }) => {
      const { data } = await apiClient.post<CopilotQueryResponse>(
        `/api/v1/copilot/cases/${caseId}/narrative`,
        request || { case_id: caseId }
      );
      return data;
    },
    onSuccess: (_, { caseId }) => {
      queryClient.invalidateQueries({ queryKey: ['copilot-summary', caseId] });
      queryClient.invalidateQueries({ queryKey: ['copilot-evidence', caseId] });
      queryClient.invalidateQueries({ queryKey: ['copilot-case-evidence', caseId] });
    },
  });
}

// ── Phase 63: Label Feedback Loop & Retraining Store ────────
export function useFeedbackStats(tenantId: string | undefined) {
  return useQuery<FeedbackStatsResponse>({
    queryKey: ['feedback-stats', tenantId],
    queryFn: async () => {
      const target = tenantId || 'bank_alpha';
      const { data } = await apiClient.get<FeedbackStatsResponse>(`/api/v1/feedback/stats/${target}`);
      return data;
    },
    enabled: !!tenantId,
    refetchInterval: 10000,
  });
}

export function useIngestFeedback() {
  const queryClient = useQueryClient();
  return useMutation<AnalystFeedbackIngestResponse, Error, AnalystFeedbackIngestRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<AnalystFeedbackIngestResponse>(
        '/api/v1/feedback/ingest',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['feedback-stats', variables.tenant_id || 'bank_alpha'] });
    },
  });
}

export function useSubmitAnalystFeedback() {
  const queryClient = useQueryClient();
  return useMutation<AnalystFeedbackIngestResponse, Error, AnalystFeedbackIngestRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<AnalystFeedbackIngestResponse>(
        '/api/v1/feedback/submit',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['feedback-stats', variables.tenant_id || 'bank_alpha'] });
    },
  });
}

export function useSampleRetrainingBatch() {
  const queryClient = useQueryClient();
  return useMutation<RetrainingBatchResponse, Error, RetrainingBatchRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<RetrainingBatchResponse>(
        '/api/v1/feedback/retraining-batch',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      if (variables.mark_consumed) {
        queryClient.invalidateQueries({ queryKey: ['feedback-stats', variables.tenant_id || 'bank_alpha'] });
      }
    },
  });
}

export function useComputeDPGradient() {
  return useMutation<DPGradientResponse, Error, DPGradientRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<DPGradientResponse>(
        '/api/v1/feedback/dp-gradient',
        payload
      );
      return data;
    },
  });
}

export function useClearFeedbackBuffer() {
  const queryClient = useQueryClient();
  return useMutation<ClearFeedbackBufferResponse, Error, string>({
    mutationFn: async (tenantId) => {
      const { data } = await apiClient.delete<ClearFeedbackBufferResponse>(
        `/api/v1/feedback/buffer/${tenantId}`
      );
      return data;
    },
    onSuccess: (_, tenantId) => {
      queryClient.invalidateQueries({ queryKey: ['feedback-stats', tenantId] });
    },
  });
}

// ── FinCEN SAR 2.0 e-Filing & Regulatory Queries ────────

export function useSarFilingsQuery(limit: number = 50) {
  return useQuery<SARFilingRecord[]>({
    queryKey: ['sar-filings', limit],
    queryFn: async () => {
      const { data } = await apiClient.get<SARFilingRecord[]>(
        `/api/v1/compliance/sar/filings?limit=${limit}`
      );
      return data;
    },
  });
}

export function useGenerateSarFilingMutation() {
  const queryClient = useQueryClient();
  return useMutation<SARFilingRecord, Error, SARGenerateRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<SARFilingRecord>(
        '/api/v1/compliance/sar/generate',
        payload
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sar-filings'] });
    },
  });
}

export function useValidateSarXmlMutation() {
  return useMutation<SARValidationResult, Error, { xml_content: string }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<SARValidationResult>(
        '/api/v1/compliance/sar/validate',
        payload
      );
      return data;
    },
  });
}

export function useExportFinCENXmlMutation() {
  const queryClient = useQueryClient();
  return useMutation<ExportFinCENXmlResponse, Error, ExportFinCENXmlRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ExportFinCENXmlResponse>(
        '/api/v1/cases/export/fincen-xml',
        payload
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sar-filings'] });
    },
  });
}

// ── Enterprise Data Retention & GDPR Art. 17 Erasure ────────
export function useRetentionPoliciesQuery(tenantId: string = 'bank_alpha') {
  return useQuery<RetentionPolicyResponse[]>({
    queryKey: ['retention-policies', tenantId],
    queryFn: async () => {
      const { data } = await apiClient.get<RetentionPolicyResponse[]>(
        `/api/v1/compliance/retention/policies?tenant_id=${encodeURIComponent(tenantId)}`
      );
      return data;
    },
    enabled: !!tenantId,
  });
}

export function useConfigureRetentionPolicyMutation() {
  const queryClient = useQueryClient();
  return useMutation<RetentionPolicyResponse, Error, RetentionPolicyRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<RetentionPolicyResponse>(
        '/api/v1/compliance/retention/policies',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['retention-policies', variables.tenant_id] });
    },
  });
}

export function useRetentionPurgeMutation() {
  const queryClient = useQueryClient();
  return useMutation<ErasureAuditRecordResponse[], Error, RetentionPurgeRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ErasureAuditRecordResponse[]>(
        '/api/v1/compliance/retention/purge',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['erasure-audit-trail', variables.tenant_id] });
    },
  });
}

export function useExecuteGDPRErasureMutation() {
  const queryClient = useQueryClient();
  return useMutation<ErasureAuditRecordResponse, Error, GDPRErasureRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ErasureAuditRecordResponse>(
        '/api/v1/compliance/gdpr/erasure',
        payload
      );
      return data;
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['erasure-audit-trail', variables.tenant_id] });
      queryClient.invalidateQueries({ queryKey: ['entities'] });
    },
  });
}

export function useErasureAuditTrailQuery(tenantId: string = 'bank_alpha') {
  return useQuery<ErasureAuditRecordResponse[]>({
    queryKey: ['erasure-audit-trail', tenantId],
    queryFn: async () => {
      const { data } = await apiClient.get<ErasureAuditRecordResponse[]>(
        `/api/v1/compliance/retention/audit-trail?tenant_id=${encodeURIComponent(tenantId)}`
      );
      return data;
    },
    enabled: !!tenantId,
  });
}

export function useVerifyErasureChainQuery(tenantId: string = 'bank_alpha') {
  return useQuery<ErasureChainVerificationResponse>({
    queryKey: ['erasure-chain-verify', tenantId],
    queryFn: async () => {
      const { data } = await apiClient.get<ErasureChainVerificationResponse>(
        `/api/v1/compliance/retention/audit-trail/verify?tenant_id=${encodeURIComponent(tenantId)}`
      );
      return data;
    },
    enabled: !!tenantId,
  });
}

// ── Authentication & Session Hooks ────────────────────────────────────────────

export function useCurrentUserQuery(token?: string) {
  return useQuery<UserProfileResponse>({
    queryKey: ['current-user', token],
    queryFn: async () => {
      const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
      const { data } = await apiClient.get<UserProfileResponse>('/api/v1/auth/me', { headers });
      return data;
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useLoginMutation() {
  const queryClient = useQueryClient();
  return useMutation<LoginResponse, Error, LoginRequest>({
    mutationFn: async (credentials) => {
      const { data } = await apiClient.post<LoginResponse>('/api/v1/auth/login', credentials);
      if (typeof window !== 'undefined' && data.access_token) {
        localStorage.setItem('cfi_token', data.access_token);
        if (data.tenant_id) {
          localStorage.setItem('cfi_tenant_id', data.tenant_id);
        }
      }
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['current-user'], data.user);
      queryClient.invalidateQueries({ queryKey: ['current-user'] });
    },
  });
}

export function useRefreshTokenMutation() {
  return useMutation<LoginResponse, Error, RefreshTokenRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<LoginResponse>('/api/v1/auth/refresh', payload);
      if (typeof window !== 'undefined' && data.access_token) {
        localStorage.setItem('cfi_token', data.access_token);
      }
      return data;
    },
  });
}

export function useLogoutMutation() {
  const queryClient = useQueryClient();
  return useMutation<LogoutResponse, Error, { token?: string } | void>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<LogoutResponse>('/api/v1/auth/logout', payload ?? {});
      if (typeof window !== 'undefined') {
        localStorage.removeItem('cfi_token');
        sessionStorage.removeItem('cfi_token');
        localStorage.removeItem('cfi_tenant_id');
        sessionStorage.removeItem('cfi_tenant_id');
      }
      return data;
    },
    onSuccess: () => {
      queryClient.clear();
    },
  });
}

export function useLockoutStatusQuery(identifier: string) {
  return useQuery<LockoutStatusResponse>({
    queryKey: ['lockout-status', identifier],
    queryFn: async () => {
      const { data } = await apiClient.get<LockoutStatusResponse>(
        `/api/v1/auth/lockout-status?identifier=${encodeURIComponent(identifier)}`
      );
      return data;
    },
    enabled: !!identifier,
    retry: false,
  });
}

// ── Gateway Ingress Telemetry & Status Hooks ──────────────────────────────────

export function useGatewayStatusQuery() {
  return useQuery<GatewayStatusResponse>({
    queryKey: ['gateway-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<GatewayStatusResponse>('/api/v1/gateway/status');
      return data;
    },
    retry: false,
    refetchInterval: 10000,
  });
}

export function useGatewayHealthQuery() {
  return useQuery<GatewayHealthResponse>({
    queryKey: ['gateway-health'],
    queryFn: async () => {
      const { data } = await apiClient.get<GatewayHealthResponse>('/api/v1/gateway/health');
      return data;
    },
    retry: false,
  });
}

export function useGatewayMetricsQuery() {
  return useQuery<GatewayMetricsResponse>({
    queryKey: ['gateway-metrics'],
    queryFn: async () => {
      const { data } = await apiClient.get<GatewayMetricsResponse>('/api/v1/gateway/metrics');
      return data;
    },
    retry: false,
    refetchInterval: 5000,
  });
}

// ── Developer Webhook Gateway Hooks ──────────────────────────────────────────

export function useWebhookSubscriptionsQuery(tenantId?: string) {
  return useQuery<WebhookSubscriptionListResponse>({
    queryKey: ['webhook-subscriptions', tenantId],
    queryFn: async () => {
      const url = tenantId
        ? `/api/v1/webhooks/subscriptions?tenant_id=${encodeURIComponent(tenantId)}`
        : '/api/v1/webhooks/subscriptions';
      const { data } = await apiClient.get<WebhookSubscriptionListResponse>(url);
      return data;
    },
    staleTime: 30000,
  });
}

export function useRegisterWebhookMutation() {
  const queryClient = useQueryClient();
  return useMutation<WebhookSubscriptionResponse, Error, WebhookSubscriptionRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<WebhookSubscriptionResponse>(
        '/api/v1/webhooks/subscriptions',
        payload
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhook-subscriptions'] });
      queryClient.invalidateQueries({ queryKey: ['webhook-health'] });
    },
  });
}

export function useDeleteWebhookMutation() {
  const queryClient = useQueryClient();
  return useMutation<WebhookDeleteResponse, Error, string>({
    mutationFn: async (subscriptionId) => {
      const { data } = await apiClient.delete<WebhookDeleteResponse>(
        `/api/v1/webhooks/subscriptions/${encodeURIComponent(subscriptionId)}`
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhook-subscriptions'] });
      queryClient.invalidateQueries({ queryKey: ['webhook-health'] });
    },
  });
}

export function useWebhookDeliveryLogsQuery(limit: number = 50) {
  return useQuery<WebhookDeliveryLogsResponse>({
    queryKey: ['webhook-deliveries', limit],
    queryFn: async () => {
      const { data } = await apiClient.get<WebhookDeliveryLogsResponse>(
        `/api/v1/webhooks/deliveries?limit=${limit}`
      );
      return data;
    },
    refetchInterval: 10000,
  });
}

export function useWebhookHealthQuery() {
  return useQuery<WebhookHealthResponse>({
    queryKey: ['webhook-health'],
    queryFn: async () => {
      const { data } = await apiClient.get<WebhookHealthResponse>('/api/v1/webhooks/health');
      return data;
    },
    retry: false,
  });
}

export function useWebhookVerifyMutation() {
  return useMutation<WebhookVerifyResponse, Error, WebhookVerifyRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<WebhookVerifyResponse>(
        '/api/v1/webhooks/verify',
        payload
      );
      return data;
    },
  });
}

// ── Real-Time Scoring, Batch Predict & Inference Hooks ──────────────────────

export function usePredictTransactionMutation() {
  return useMutation<TransactionPredictResponse, Error, TransactionPredictRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<TransactionPredictResponse>(
        '/api/v1/predict',
        payload
      );
      return data;
    },
  });
}

export function usePredictBatchMutation() {
  return useMutation<
    BatchPredictionResponse,
    Error,
    { transactions: TransactionPredictRequest[]; bank_id?: string; simulation_id?: string }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<BatchPredictionResponse>(
        '/api/v1/predict/batch',
        payload
      );
      return data;
    },
  });
}

export function useExplainTransactionMutation() {
  return useMutation<
    ExplainTransactionResponse,
    Error,
    { transaction: TransactionPredictRequest; method?: string; simulation_id?: string }
  >({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ExplainTransactionResponse>(
        '/api/v1/predict/explain',
        payload
      );
      return data;
    },
  });
}

export function useScoreTransactionMutation() {
  return useMutation<ScoreTransactionResponse, Error, ScoreTransactionRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ScoreTransactionResponse>(
        '/api/v1/transactions/score',
        payload
      );
      return data;
    },
  });
}

export function useRealtimeInferenceScoreMutation() {
  return useMutation<RealtimeInferenceResponse, Error, RealtimeInferenceRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<RealtimeInferenceResponse>(
        '/api/v1/inference/score',
        payload
      );
      return data;
    },
  });
}

export function useInferenceQuotaQuery(tenantId?: string) {
  return useQuery<InferenceQuotaResponse>({
    queryKey: ['inference-quota', tenantId || 'default'],
    queryFn: async () => {
      const headers = tenantId ? { 'X-Tenant-ID': tenantId } : undefined;
      const { data } = await apiClient.get<InferenceQuotaResponse>(
        '/api/v1/inference/quota',
        { headers }
      );
      return data;
    },
    refetchInterval: 30000,
  });
}

// ── Open Banking PSD2 & ISO 20022 Hooks ──────────────────────────────────

export function useCreatePSD2ConsentMutation() {
  const queryClient = useQueryClient();
  return useMutation<PSD2ConsentResponse, Error, PSD2ConsentRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<PSD2ConsentResponse>('/api/v1/psd2/consents', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['psd2-consents'] });
    },
  });
}

export function useGetPSD2ConsentQuery(consentId: string, enabled = true) {
  return useQuery<PSD2ConsentResponse>({
    queryKey: ['psd2-consent', consentId],
    queryFn: async () => {
      const { data } = await apiClient.get<PSD2ConsentResponse>(`/api/v1/psd2/consents/${consentId}`);
      return data;
    },
    enabled: enabled && !!consentId,
  });
}

export function useRevokePSD2ConsentMutation() {
  const queryClient = useQueryClient();
  return useMutation<PSD2ConsentResponse, Error, string>({
    mutationFn: async (consentId) => {
      const { data } = await apiClient.delete<PSD2ConsentResponse>(`/api/v1/psd2/consents/${consentId}`);
      return data;
    },
    onSuccess: (_, consentId) => {
      queryClient.invalidateQueries({ queryKey: ['psd2-consent', consentId] });
      queryClient.invalidateQueries({ queryKey: ['psd2-consents'] });
    },
  });
}

export function useGetPSD2AccountsQuery(consentId: string, enabled = true) {
  return useQuery<PSD2Account[]>({
    queryKey: ['psd2-accounts', consentId],
    queryFn: async () => {
      const { data } = await apiClient.get<PSD2Account[]>('/api/v1/psd2/accounts', {
        headers: { 'consent-id': consentId },
      });
      return data;
    },
    enabled: enabled && !!consentId,
  });
}

export function useGetPSD2TransactionsQuery(accountId: string, consentId: string, enabled = true) {
  return useQuery<PSD2Transaction[]>({
    queryKey: ['psd2-transactions', accountId, consentId],
    queryFn: async () => {
      const { data } = await apiClient.get<PSD2Transaction[]>(
        `/api/v1/psd2/accounts/${accountId}/transactions`,
        { headers: { 'consent-id': consentId } }
      );
      return data;
    },
    enabled: enabled && !!accountId && !!consentId,
  });
}

export function useInitiatePaymentMutation() {
  const queryClient = useQueryClient();
  return useMutation<PaymentInitiationResponse, Error, PaymentInitiationRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<PaymentInitiationResponse>('/api/v1/psd2/payments', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['psd2-payments'] });
    },
  });
}

export function useGetPaymentStatusQuery(paymentId: string, enabled = true) {
  return useQuery<PaymentStatusResponse>({
    queryKey: ['psd2-payment-status', paymentId],
    queryFn: async () => {
      const { data } = await apiClient.get<PaymentStatusResponse>(`/api/v1/psd2/payments/${paymentId}`);
      return data;
    },
    enabled: enabled && !!paymentId,
    refetchInterval: (query) => {
      const currentStatus = query.state.data?.transaction_status;
      return currentStatus === 'RCVD' || currentStatus === 'ACTC' || currentStatus === 'ACSP' ? 5000 : false;
    },
  });
}

export function useParseISO20022Mutation() {
  return useMutation<ISO20022ParseResponse, Error, ISO20022ParseRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ISO20022ParseResponse>('/api/v1/psd2/iso20022/parse', payload);
      return data;
    },
  });
}

export function useEntityRelationships(entityId?: string) {
  return useQuery<EntityRelationshipItem[]>({
    queryKey: ['entity-relationships', entityId],
    queryFn: async () => {
      if (!entityId) return [];
      const { data } = await apiClient.get<EntityRelationshipItem[]>(`/api/v1/entities/${entityId}/relationships`);
      return data;
    },
    enabled: !!entityId,
  });
}

export function useDeleteEntity() {
  const queryClient = useQueryClient();
  return useMutation<EntityDeleteResponse, Error, string>({
    mutationFn: async (entityId: string) => {
      const { data } = await apiClient.delete<EntityDeleteResponse>(`/api/v1/entities/${entityId}`);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['entities'] });
    },
  });
}

export function useHMACTokenize() {
  return useMutation<HMACTokenizeResponse, Error, HMACTokenizeRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<HMACTokenizeResponse>('/api/v1/entities/hmac-tokenize', payload);
      return data;
    },
  });
}

export function usePSIMatchDirect() {
  return useMutation<PSIMatchDirectResponse, Error, PSIMatchDirectRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<PSIMatchDirectResponse>('/api/v1/psi/match', payload);
      return data;
    },
  });
}

export function usePSIProtocolStats() {
  return useQuery<PSIStatsResponse>({
    queryKey: ['psi-protocol-stats'],
    queryFn: async () => {
      const { data } = await apiClient.get<PSIStatsResponse>('/api/v1/psi/stats');
      return data;
    },
  });
}

// ── Bank Onboarding & CA Trust Bundle Hooks ────

export function useBankCABundle() {
  return useQuery<BankCABundleResponse>({
    queryKey: ['bank-ca-bundle'],
    queryFn: async () => {
      const { data } = await apiClient.get<BankCABundleResponse>('/api/v1/onboarding/ca-bundle');
      return data;
    },
  });
}

// ── Design Partner Commercial Pilot Hooks ────

export function useEnrollPilotLead() {
  const queryClient = useQueryClient();
  return useMutation<PilotLeadResponse, Error, PilotLeadRequest>({
    mutationFn: async (payload: PilotLeadRequest) => {
      const { data } = await apiClient.post<PilotLeadResponse>('/api/v1/design-partner/leads', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pilot-leads'] });
    },
  });
}

export function usePilotLeads() {
  return useQuery<PilotLeadResponse[]>({
    queryKey: ['pilot-leads'],
    queryFn: async () => {
      const { data } = await apiClient.get<PilotLeadResponse[]>('/api/v1/design-partner/leads');
      return data;
    },
  });
}

// ── Admin Web Console & Multi-Tenant Administration Hooks ────

export function useAdminDashboardSummary() {
  return useQuery<AdminDashboardSummaryResponse>({
    queryKey: ['admin-dashboard-summary'],
    queryFn: async () => {
      const { data } = await apiClient.get<AdminDashboardSummaryResponse>('/api/v1/admin/dashboard/summary');
      return data;
    },
  });
}

export function useAdminRoleConfig(role: string = 'EXECUTIVE') {
  return useQuery<AdminRoleConfigResponse>({
    queryKey: ['admin-role-config', role],
    queryFn: async () => {
      const { data } = await apiClient.get<AdminRoleConfigResponse>('/api/v1/admin/dashboard/role-config', {
        params: { role },
      });
      return data;
    },
  });
}

export function useAdminSystemConfig() {
  return useQuery<AdminConfigResponse>({
    queryKey: ['admin-system-config'],
    queryFn: async () => {
      const { data } = await apiClient.get<AdminConfigResponse>('/api/v1/admin/config');
      return data;
    },
  });
}

export function useAdminTenants() {
  return useQuery<AdminTenantListResponse>({
    queryKey: ['admin-tenants'],
    queryFn: async () => {
      const { data } = await apiClient.get<AdminTenantListResponse>('/api/v1/admin/tenants');
      return data;
    },
  });
}

export function useAdminMaintenanceWindow() {
  return useQuery<MaintenanceWindowResponse>({
    queryKey: ['admin-maintenance-window'],
    queryFn: async () => {
      const { data } = await apiClient.get<MaintenanceWindowResponse>('/api/v1/admin/maintenance');
      return data;
    },
  });
}

// ── Maintenance CronJob Query Hooks ────

export function useExecuteSystemCleanup() {
  const queryClient = useQueryClient();
  return useMutation<CronCleanupResponse, Error, void>({
    mutationFn: async () => {
      const { data } = await apiClient.post<CronCleanupResponse>('/api/v1/cron/cleanup-sessions');
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cron-health-check'] });
    },
  });
}

export function useCronHealthCheck() {
  return useQuery<CronHealthStatusResponse>({
    queryKey: ['cron-health-check'],
    queryFn: async () => {
      const { data } = await apiClient.get<CronHealthStatusResponse>('/api/v1/cron/health-check');
      return data;
    },
  });
}

export function useCronSchedules() {
  return useQuery<CronScheduleResponse>({
    queryKey: ['cron-schedules'],
    queryFn: async () => {
      const { data } = await apiClient.get<CronScheduleResponse>('/api/v1/cron/schedule');
      return data;
    },
  });
}

// ── Consortium Governance & Coordinator Hooks ────

export function useConsortium(consortiumId: string = 'cfi-consortium') {
  return useQuery<ConsortiumAlliance>({
    queryKey: ['consortium', consortiumId],
    queryFn: async () => {
      const { data } = await apiClient.get<ConsortiumAlliance>(`/api/v1/coordinator/consortium/${consortiumId}`);
      return data;
    },
    enabled: Boolean(consortiumId),
  });
}

export function useConsortiumMembers(consortiumId: string = 'cfi-consortium') {
  return useQuery<ConsortiumMember[]>({
    queryKey: ['consortium-members', consortiumId],
    queryFn: async () => {
      const { data } = await apiClient.get<ConsortiumMember[]>(`/api/v1/coordinator/consortium/${consortiumId}/members`);
      return data;
    },
    enabled: Boolean(consortiumId),
  });
}

export function useCreateConsortium() {
  const queryClient = useQueryClient();
  return useMutation<ConsortiumAlliance, Error, CreateConsortiumPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<ConsortiumAlliance>('/api/v1/coordinator/consortium', payload);
      return data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['consortium', data.consortium_id] });
      queryClient.invalidateQueries({ queryKey: ['consortium-members', data.consortium_id] });
    },
  });
}


export function useGovernanceProposals(consortiumId: string = 'cfi-consortium', status?: string) {
  return useQuery<GovernanceProposal[]>({
    queryKey: ['governance-proposals', consortiumId, status],
    queryFn: async () => {
      const params = new URLSearchParams();
      params.append('consortium_id', consortiumId);
      if (status) params.append('status', status);
      const { data } = await apiClient.get<GovernanceProposal[]>(`/api/v1/coordinator/proposals?${params.toString()}`);
      return data;
    },
  });
}

export function useGovernanceProposal(proposalId: string | undefined) {
  return useQuery<GovernanceProposal>({
    queryKey: ['governance-proposal', proposalId],
    queryFn: async () => {
      const { data } = await apiClient.get<GovernanceProposal>(`/api/v1/coordinator/proposals/${proposalId}`);
      return data;
    },
    enabled: Boolean(proposalId),
  });
}

export function useCreateMembershipProposal() {
  const queryClient = useQueryClient();
  return useMutation<GovernanceProposal, Error, CreateMembershipProposalPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<GovernanceProposal>('/api/v1/coordinator/proposals/membership', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['governance-proposals'] });
    },
  });
}

export function useCreatePolicyProposal() {
  const queryClient = useQueryClient();
  return useMutation<GovernanceProposal, Error, CreatePolicyProposalPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<GovernanceProposal>('/api/v1/coordinator/proposals/policy', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['governance-proposals'] });
    },
  });
}

export function useVoteProposal() {
  const queryClient = useQueryClient();
  return useMutation<VoteProposalReceipt, Error, { proposalId: string; payload: VoteProposalPayload }>({
    mutationFn: async ({ proposalId, payload }) => {
      const { data } = await apiClient.post<VoteProposalReceipt>(`/api/v1/coordinator/proposals/${proposalId}/vote`, payload);
      return data;
    },
    onSuccess: (_, { proposalId }) => {
      queryClient.invalidateQueries({ queryKey: ['governance-proposals'] });
      queryClient.invalidateQueries({ queryKey: ['governance-proposal', proposalId] });
    },
  });
}

export function useCoordinatorClients() {
  return useQuery<CoordinatorClientCapability[]>({
    queryKey: ['coordinator-clients'],
    queryFn: async () => {
      const { data } = await apiClient.get<CoordinatorClientCapability[]>('/api/v1/coordinator/clients');
      return data;
    },
  });
}

export function useCoordinatorQuorumStatus(roundId?: number) {
  return useQuery<CoordinatorQuorumStatus>({
    queryKey: ['coordinator-quorum', roundId],
    queryFn: async () => {
      const url = roundId ? `/api/v1/coordinator/quorum-status?round_id=${roundId}` : '/api/v1/coordinator/quorum-status';
      const { data } = await apiClient.get<CoordinatorQuorumStatus>(url);
      return data;
    },
  });
}

export function useCoordinatorAsyncStatus() {
  return useQuery<CoordinatorAsyncStatus>({
    queryKey: ['coordinator-async-status'],
    queryFn: async () => {
      const { data } = await apiClient.get<CoordinatorAsyncStatus>('/api/v1/coordinator/async-status');
      return data;
    },
  });
}

// ── Web3 & CBDC Smart Contract Settlement Hooks ────

export function useSettlementContractInfo() {
  return useQuery<SettlementContractInfo>({
    queryKey: ['settlement-contract-info'],
    queryFn: async () => {
      const { data } = await apiClient.get<SettlementContractInfo>('/api/v1/settlement/contract-info');
      return data;
    },
  });
}

export function useSettlementHistory() {
  return useQuery<SettlementReceipt[]>({
    queryKey: ['settlement-history'],
    queryFn: async () => {
      const { data } = await apiClient.get<SettlementReceipt[]>('/api/v1/settlement/history');
      return data;
    },
  });
}

export function useTriggerSettlement() {
  const queryClient = useQueryClient();
  return useMutation<SettlementReceipt, Error, SettlementTriggerPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<SettlementReceipt>('/api/v1/settlement/trigger', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-history'] });
      queryClient.invalidateQueries({ queryKey: ['settlement-contract-info'] });
    },
  });
}

export function useClaimPayout() {
  const queryClient = useQueryClient();
  return useMutation<PayoutClaimReceipt, Error, PayoutClaimPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<PayoutClaimReceipt>('/api/v1/settlement/claim', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-history'] });
      queryClient.invalidateQueries({ queryKey: ['settlement-contract-info'] });
    },
  });
}

export function useMultiSigProposals() {
  return useQuery<MultiSigProposal[]>({
    queryKey: ['multisig-proposals'],
    queryFn: async () => {
      const { data } = await apiClient.get<MultiSigProposal[]>('/api/v1/settlement/multisig/proposals');
      return data;
    },
  });
}

export function useMultiSigProposal(txId: number | undefined) {
  return useQuery<MultiSigProposal>({
    queryKey: ['multisig-proposal', txId],
    queryFn: async () => {
      const { data } = await apiClient.get<MultiSigProposal>(`/api/v1/settlement/multisig/proposals/${txId}`);
      return data;
    },
    enabled: txId !== undefined,
  });
}

export function useProposeMultiSigAction() {
  const queryClient = useQueryClient();
  return useMutation<MultiSigProposeReceipt, Error, MultiSigProposePayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<MultiSigProposeReceipt>('/api/v1/settlement/multisig/propose', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['multisig-proposals'] });
    },
  });
}

export function useConfirmMultiSigAction() {
  const queryClient = useQueryClient();
  return useMutation<MultiSigConfirmReceipt, Error, MultiSigConfirmPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<MultiSigConfirmReceipt>('/api/v1/settlement/multisig/confirm', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['multisig-proposals'] });
    },
  });
}

export function useRevokeMultiSigAction() {
  const queryClient = useQueryClient();
  return useMutation<MultiSigRevokeReceipt, Error, MultiSigRevokePayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<MultiSigRevokeReceipt>('/api/v1/settlement/multisig/revoke', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['multisig-proposals'] });
    },
  });
}

export function useQuarantineBank() {
  const queryClient = useQueryClient();
  return useMutation<QuarantineReceipt, Error, QuarantinePayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<QuarantineReceipt>('/api/v1/settlement/quarantine', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contract-info'] });
    },
  });
}

export function useClearQuarantine() {
  const queryClient = useQueryClient();
  return useMutation<QuarantineReceipt, Error, QuarantinePayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<QuarantineReceipt>('/api/v1/settlement/clear-quarantine', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contract-info'] });
    },
  });
}

export function useSlashBank() {
  const queryClient = useQueryClient();
  return useMutation<SlashPenaltyReceipt, Error, SlashPenaltyPayload>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<SlashPenaltyReceipt>('/api/v1/settlement/slash', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settlement-contract-info'] });
      queryClient.invalidateQueries({ queryKey: ['slashed-nodes'] });
    },
  });
}

export function useSlashedNodes() {
  return useQuery<SlashedNodesCatalog>({
    queryKey: ['slashed-nodes'],
    queryFn: async () => {
      const { data } = await apiClient.get<SlashedNodesCatalog>('/api/v1/settlement/slashed');
      return data;
    },
  });
}

// ── Compliance, Model Governance & FinCEN SAR Hooks ──

export function useSoc2EvidenceQuery() {
  return useQuery<SOC2EvidenceReportResponse>({
    queryKey: ['soc2-evidence'],
    queryFn: async () => {
      const { data } = await apiClient.get<SOC2EvidenceReportResponse>('/api/v1/compliance/soc2-evidence');
      return data;
    },
  });
}

export function useSr117AuditQuery() {
  return useQuery<SR117AuditReportResponse>({
    queryKey: ['sr117-audit'],
    queryFn: async () => {
      const { data } = await apiClient.get<SR117AuditReportResponse>('/api/v1/compliance/sr11-7/audit');
      return data;
    },
  });
}

export function useSr117ScheduleQuery(year: number = 2026) {
  return useQuery<SR117ScheduleResponse>({
    queryKey: ['sr117-schedule', year],
    queryFn: async () => {
      const { data } = await apiClient.get<SR117ScheduleResponse>(`/api/v1/compliance/sr11-7/schedule?year=${year}`);
      return data;
    },
  });
}

export function useEvaluateFairnessMutation() {
  return useMutation<FairnessAuditResponse, Error, FairnessAuditRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<FairnessAuditResponse>('/api/v1/compliance/fairness/evaluate', payload);
      return data;
    },
  });
}

export function useEvaluateCanaryGateMutation() {
  return useMutation<CanaryGateResponse, Error, CanaryGateRequest>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post<CanaryGateResponse>('/api/v1/compliance/sr11-7/canary-gate', payload);
      return data;
    },
  });
}

export function useSarFilingDetailQuery(filingId: string) {
  return useQuery<SARFilingDetailResponse>({
    queryKey: ['sar-filing-detail', filingId],
    queryFn: async () => {
      const { data } = await apiClient.get<SARFilingDetailResponse>(`/api/v1/compliance/sar/filings/${filingId}`);
      return data;
    },
    enabled: Boolean(filingId),
  });
}











