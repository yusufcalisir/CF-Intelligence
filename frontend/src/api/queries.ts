import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import type {
  Alert,
  BankDistributions,
  BankInfo,
  Case,
  CaseSummary,
  DashboardStats,
  Entity,
  ExplainabilityReport,
  GraphData,
  GraphStats,
  IntelligenceStats,
  RiskWeights,
  ScenarioInfo,
  ScenarioStartResponse,
  ScenarioStatus,
  SharedIntelligence,
  SimulationConfig,
  SimulationCreateResponse,
  SimulationDetail,
  SimulationSummary,
  TrainingRound,
  ModelVersion,
  Evidence,
  InvestigatorAuditLog,
  ShadowMetrics,
  BusinessRule,
  PSIRequest,
  PSIResponse,
  EntityFuzzyResolveRequest,
  FuzzyMatchResponse,
  CounterfactualExplanation,

  DecisionReplayReport,
  GNNExplanationReport,
  SecurityStatus,
  ABACEvalRequest,
  ABACEvalResponse,
  AuditChainEntry,

  AuditChainVerifyResponse,
  DriftAnalysisReport,
  CalibrationReport,
  ActiveAlertItem,
  RetrainTriggerResponse,
  ClientCapabilityItem,
  NegotiatedParamsResponse,
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
  return useMutation<Case, Error, { caseId: string; status: string; actor?: string; supervisor_signature?: string }>({
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

// ── Phase 2: Dashboard ─────────────────────

export function useDashboardStats() {
  return useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/dashboard/stats');
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

export function useAlertsBySeverity() {
  return useQuery<Record<string, number>>({
    queryKey: ['alerts-by-severity'],
    queryFn: async () => {
      const { data } = await apiClient.get('/api/v1/dashboard/alerts-by-severity');
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
  return useQuery<any[]>({
    queryKey: ['canary-history', simulationId],
    queryFn: async () => {
      const { data } = await apiClient.get(`/api/v1/registry/${simulationId}/canary`);
      return data;
    },
    enabled: !!simulationId,
    refetchInterval: 3000,
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

export function useTestRule() {
  return useMutation<{ matches: boolean; message: string }, Error, { condition: Record<string, any>; transaction: Record<string, any> }>({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/api/v1/rules/test', payload);
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
      const { data } = await apiClient.post('/api/v1/entities/fuzzy-resolve', payload);
      return data;
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




