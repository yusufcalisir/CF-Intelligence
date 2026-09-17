/** API response types matching the FastAPI Pydantic schemas. */

export interface SimulationConfig {
  num_rounds: number;
  local_epochs: number;
  learning_rate: number;
  batch_size: number;
  min_clients_per_round: number;
  enable_latency_simulation: boolean;
  latency_min_ms: number;
  latency_max_ms: number;
  enable_dropout_simulation: boolean;
  dropout_probability: number;
  enable_reconnect_simulation: boolean;
  privacy_mechanism: 'none' | 'differential_privacy' | 'secure_aggregation' | 'both';
  dp_epsilon: number;
  dp_delta: number;
  dp_max_grad_norm: number;
  dp_mode?: 'post_hoc' | 'opacus';
  dataset?: 'synthetic' | 'paysim' | 'ieee_cis' | 'elliptic' | 'creditcard';
  bank_a_transactions: number;
  bank_b_transactions: number;
  bank_c_transactions: number;
  aggregation_method: 'fed_avg_weighted' | 'fed_avg' | 'fed_prox' | 'krum' | 'coordinate_wise_median' | 'trimmed_mean' | 'bulyan' | 'fed_adam' | 'fed_adagrad' | 'fed_yogi' | 'scaffold';

  // Advanced Federated Optimization
  fedprox_mu?: number;
  moon_mu?: number;
  moon_temperature?: number;
  fedopt_server_lr?: number;

  enable_poisoning_simulation: boolean;
  poisoning_bank_id: string;
  poisoning_scale: number;
  fl_engine_type: 'custom' | 'flower';
  p2p_mode?: boolean;
  topology?: 'RING' | 'MESH';

  // Regulatory Compliance & Fairness (AI Act)
  enable_bias_mitigation?: boolean;
  fairness_lambda?: number;

  // Hardware & Cryptographic Isolation
  hardware_isolation_mode?: 'none' | 'tee' | 'fhe';
  enable_streaming_gnn?: boolean;

  // Web3 & CBDC Smart Contract Settlement
  enable_web3_settlement?: boolean;
  settlement_currency?: string;
  smart_contract_address?: string;

  // Active Defense & Adversarial ML Training
  enable_adversarial_training?: boolean;
  adversarial_attack_type?: string;
  adversarial_epsilon?: number;
  adversarial_alpha?: number;
  adversarial_steps?: number;
  adversarial_loss_weight?: number;
}

export interface EvaluationMetrics {
  accuracy: number;
  precision: number;
  recall: number;
  f1_score: number;
  auc_roc: number;
  loss: number;
  confusion_matrix: number[][];
  roc_fpr: number[];
  roc_tpr: number[];
  roc_thresholds: number[];
  feature_importance: Record<string, number>;

  // Fairness metrics
  disparate_impact?: number;
  equal_opportunity_diff?: number;
  protected_selection_rate?: number;
  reference_selection_rate?: number;

  // Active Defense & Adversarial Metrics
  adversarial_robustness_score?: number;
  clean_accuracy?: number;
  robust_accuracy?: number;
  fgsm_evasion_rate?: number;
  pgd_evasion_rate?: number;
}


export interface DataProfile {
  bank_name: string;
  num_transactions: number;
  fraud_ratio: number;
  mean_transaction_amount: number;
  std_transaction_amount: number;
  top_merchant_categories: string[];
  top_countries: string[];
  mean_account_age_days: number;
  mean_velocity: number;
}

// ── Data Drift / Distribution Visualization ────

export interface AmountHistogram {
  bins: number[];
  counts: number[];
  fraud_counts: number[];
}

export interface HourlyFraudRate {
  hours: number[];
  total: number[];
  fraud: number[];
}

export interface MerchantRisk {
  categories: string[];
  fraud_rates: number[];
  counts: number[];
}

export interface BankDistributionData {
  amount_histogram: AmountHistogram;
  hourly_fraud_rate: HourlyFraudRate;
  merchant_risk: MerchantRisk;
}

export interface DriftMetric {
  psi: number;
  js_divergence: number;
  ks?: number;
  status: 'stable' | 'moderate' | 'drifted';
}

export interface FeatureDriftInfo {
  overall_psi: number;
  overall_js: number;
  features: Record<string, DriftMetric>;
}

export interface ConceptDriftInfo {
  overall_psi: number;
  overall_js: number;
  model_prediction_drift: DriftMetric;
  conditional_drifts: Record<string, number>;
}

export interface DivergenceSummary {
  amount_ks_statistic: Record<string, number>;
  overall_non_iid_score: number;
  feature_drift?: Record<string, FeatureDriftInfo>;
  concept_drift?: Record<string, ConceptDriftInfo>;
}

export interface BankDistributions {
  banks: Record<string, BankDistributionData>;
  divergence_summary: DivergenceSummary;
}

export interface BankResult {
  id: string;
  name: string;
  tier: string;
  fraud_ratio: number;
  num_transactions: number;
  status: string;
  local_metrics: EvaluationMetrics | null;
  federated_metrics: EvaluationMetrics | null;
  improvement: Record<string, number> | null;
  data_profile: DataProfile | null;
  contribution_score?: number;
  quarantined?: boolean;
}

export interface CanaryEvaluation {
  version: number;
  candidate_auc: number;
  promoted_auc: number;
  is_promoted: boolean;
  reason: string;
}

export interface ModelVersion {
  version: number;
  filename: string;
  metrics: {
    accuracy?: number;
    precision?: number;
    recall?: number;
    f1_score: number;
    auc_roc: number;
    loss: number;
  };
  is_active: boolean;
  status: string;
  git_commit_hash: string;
  dataset_hash: string;
  dp_noise_profile: {
    mechanism: string;
    epsilon: number;
    delta: number;
  };
  sign_offs: Array<{
    role: string;
    user: string;
    signature: string;
    timestamp: string;
    fairness_score: number;
    bias_metric: number;
    drift_divergence: number;
  }>;
  created_at: string;
}

export interface SR117ValidationResult {
  passed: boolean;
  rule_name: string;
  checks: Record<string, any>;
  recommendations: string[];
}

export interface ModelPromoteRequest {
  target_status?: string;
  enforce_sr11_7?: boolean;
  min_auc?: number;
  min_fairness_score?: number;
}

export interface ModelPromoteResponse {
  version: number;
  target_status: string;
  message: string;
  is_active: boolean;
  status: string;
  sr11_7_validation?: SR117ValidationResult | null;
  timestamp?: string | null;
}

export interface ModelSummary {
  simulation_id: string;
  active_version?: number | null;
  champion_status: string;
  total_versions: number;
  latest_metrics: Record<string, any>;
  sr11_7_compliant: boolean;
  last_updated?: string | null;
}

export interface ModelInventoryResponse {
  models: ModelSummary[];
  total_models: number;
}

export interface CanaryDecisionItem {
  round: number;
  version: number;
  candidate_auc: number;
  promoted_auc: number;
  is_promoted: boolean;
  reason: string;
}

export interface TrainingRound {
  round_number: number;
  total_rounds: number;
  global_loss: number;
  participating_banks: string[];
  dropped_banks: string[];
  duration_ms: number;
  privacy_budget: number;
  feature_importance?: Record<string, number>;
  canary_info?: CanaryEvaluation;
}

export interface SimulationSummary {
  id: string;
  status: string;
  current_round: number;
  total_rounds: number;
  progress_pct: number;
  created_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface SimulationDetail {
  id: string;
  status: string;
  config: SimulationConfig;
  current_round: number;
  total_rounds: number;
  progress_pct: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  banks: BankResult[];
  rounds: TrainingRound[];

  // Hardware/Cryptographic Isolation telemetry
  tee_mrenclave?: string | null;
  tee_mrsigner?: string | null;
  tee_attestation_signature?: string | null;
  fhe_poly_degree?: number | null;
  fhe_noise_bound?: number | null;
  fhe_key_id?: string | null;

  // Streaming GNN Telemetry
  streaming_gnn_node_count?: number;
  streaming_gnn_edge_count?: number;
  streaming_gnn_loss_history?: number[];

  // Web3 & CBDC Settlement Telemetry
  settlement_tx_hash?: string | null;
  settlement_block_number?: number | null;
  settlement_status?: string | null;
  on_chain_payouts?: OnChainPayout[];
}

export interface OnChainPayout {
  bank_name: string;
  wallet_address: string;
  shapley_score: number;
  shapley_basis_points: number;
  share_percent: number;
  payout_usd: number;
  payout_wei: string;
  is_quarantined: boolean;
  status: 'DISTRIBUTED' | 'BLOCKED_QUARANTINE';
}

export interface SimulationCreateResponse {
  id: string;
  status: string;
  message: string;
}

export interface SimulationStatusResponse {
  id: string;
  status: string;
  current_round: number;
  total_rounds: number;
  progress_pct: number;
  error_message?: string | null;
}

export interface SimulationStopRequest {
  simulation_id: string;
  reason?: string | null;
}

export interface SimulationStopResponse {
  simulation_id: string;
  status: string;
  message: string;
  stopped_at: string;
}

export interface TrainingProgressResponse {
  simulation_id?: string | null;
  event_type: string;
  status?: string | null;
  current_round: number;
  total_rounds: number;
  progress_pct: number;
  data: Record<string, any>;
}

export interface TrainingMetricsSummaryResponse {
  simulation_id: string;
  total_rounds: number;
  global_losses: number[];
  auc_history: number[];
  per_bank_auc: Record<string, number[]>;
  per_bank_loss: Record<string, number[]>;
}

export interface TrainingHistoryItem {
  simulation_id: string;
  status: string;
  current_round: number;
  total_rounds: number;
  progress_pct: number;
  created_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
}

export interface TrainingHistoryResponse {
  total_count: number;
  runs: TrainingHistoryItem[];
}

export interface BankInfo {
  id: string;
  name: string;
  tier: string;
  description: string;
  default_fraud_ratio: number;
  default_transactions: number;
  fraud_pattern: string;
  characteristics: string[];
  hardware_enclave?: string;
  mtls_status?: string;
  status?: string;
}

export interface ConsortiumNodeSummary {
  bank_id: string;
  name: string;
  tier: string;
  status: string;
  hardware_acceleration?: string;
  last_heartbeat_timestamp?: number | null;
}

export interface ConsortiumStatusResponse {
  consortium_name: string;
  total_registered_nodes: number;
  active_nodes_count: number;
  hardware_enclave_enabled: boolean;
  mtls_status: string;
  nodes: ConsortiumNodeSummary[];
}

export interface TrainingEvent {
  event_type: string;
  data: Record<string, unknown>;
}

export const BANK_COLORS: Record<string, string> = {
  bank_a: '#6366f1',
  bank_b: '#14b8a6',
  bank_c: '#f59e0b',
};

export const BANK_NAMES: Record<string, string> = {
  bank_a: 'Meridian National',
  bank_b: 'Nexus Digital',
  bank_c: 'Heritage Regional',
};

// ── Phase 2: AML Intelligence Platform ────────

export interface Alert {
  id: string;
  bank_id: string;
  transaction_id: string;
  risk_score: number;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  status: string;
  reason_codes: string[];
  confidence: number;
  involved_entity_ids: string[];
  created_at: string;
  updated_at?: string | null;
  top_features: { feature: string; contribution: number }[];
  risk_factors: string[];
  model_confidence: number;
  triage_priority?: 'p1_critical' | 'p2_high' | 'p3_medium' | 'p4_low' | string;
  triage_action?: 'escalate_immediate' | 'investigate_case' | 'queue_standard' | 'auto_monitor' | string;
  sla_minutes?: number;
  triage_reasons?: string[];
  dedup_count?: number;
  is_duplicate?: boolean;
  dedup_key?: string | null;
}

export interface AlertStatusUpdateRequest {
  status: 'new' | 'investigating' | 'confirmed_fraud' | 'false_positive' | 'escalated' | 'closed';
  resolution_notes?: string | null;
}

export interface AlertTriageEvaluateRequest {
  transaction_amount?: number;
  country_code?: string;
  velocity?: number;
}

export interface AlertStandaloneTriageRequest {
  transaction_amount?: number;
  country_code?: string;
  velocity?: number;
  risk_score?: number;
  severity?: 'critical' | 'high' | 'medium' | 'low' | 'info';
  reason_codes?: string[];
  entity_overlap_count?: number;
  dedup_count?: number;
}

export interface AlertTriageEvaluateResponse {
  alert_id: string;
  triage_priority: string;
  triage_action: string;
  sla_minutes: number;
  triage_reasons: string[];
}

export interface AlertDeduplicationStatsResponse {
  total_processed: number;
  duplicates_detected: number;
  deduplication_ratio: number;
  active_sliding_window_keys: number;
  window_seconds: number;
}

export interface ExplainabilityReport {
  alert_id: string;
  top_features: { feature: string; contribution: number }[];
  risk_factors: string[];
  historical_evidence: string[];
  model_confidence: number;
  risk_score_breakdown: RiskSignalData[];
  explanation_text: string;
}

export interface RiskSignalData {
  signal_name: string;
  weight: number;
  raw_value: number;
  normalized_score: number;
  explanation: string;
  contribution: number;
}

export interface SharedIntelligence {
  id: string;
  source_bank_id: string;
  intelligence_type: string;
  privacy_hash: string;
  risk_indicator: number;
  description: string;
  entity_type: string | null;
  related_alert_count: number;
  created_at: string;
}

export interface IntelligenceStats {
  total_items: number;
  items_by_type: Record<string, number>;
  items_by_bank: Record<string, number>;
  avg_risk_indicator: number;
}

export interface Case {
  id: string;
  title: string;
  status: string;
  priority: string;
  assigned_to: string | null;
  alert_ids: string[];
  evidence_ids: string[];
  notes: CaseNote[];
  timeline: CaseEvent[];
  created_at: string;
  updated_at: string | null;
  closed_at: string | null;
  total_risk_score: number;
  duration_hours: number | null;
  is_open: boolean;
  supervisor_signatures?: string[];
  supervisor_signature?: string | null;
}

export interface CaseSummary {
  id: string;
  title: string;
  status: string;
  priority: string;
  assigned_to: string | null;
  alert_count: number;
  created_at: string;
  is_open: boolean;
}

export interface CaseNote {
  id: string;
  case_id: string;
  author: string;
  content: string;
  created_at: string;
}

export interface CaseEvent {
  event_type: string;
  description: string;
  actor: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}

export interface TimelineVerificationResponse {
  case_id: string;
  is_valid: boolean;
  event_count: number;
  corrupted_index: number | null;
  chain_hashes: string[];
  message: string;
}

export interface CaseCreatePayload {
  title: string;
  priority?: string;
  alert_ids?: string[];
}

export interface CaseStatusUpdatePayload {
  caseId: string;
  status: string;
  actor?: string;
  supervisor_signature?: string;
  second_supervisor_signature?: string;
  supervisor_signatures?: string[];
}

export interface CaseEscalatePayload {
  caseId: string;
  reason: string;
  actor?: string;
}

export interface CaseSignPayload {
  caseId: string;
  supervisor_id: string;
  action?: 'APPROVE' | 'REJECT';
  notes?: string;
}

export interface CaseResolvePayload {
  caseId: string;
  resolution: 'CONFIRMED_FRAUD' | 'FALSE_POSITIVE';
  primary_supervisor: string;
  secondary_supervisor: string;
  actor?: string;
}

export interface Entity {
  id: string;
  entity_type: string;
  privacy_id: string;
  bank_id: string;
  display_label: string;
  attributes: Record<string, unknown>;
  risk_level: string;
  alert_count: number;
  first_seen: string;
  last_seen: string;
}

export interface EntityProfile {
  entity_id: string;
  entity_type: string;
  privacy_id: string;
  display_label: string;
  bank_id: string;
  risk_level: string;
  alert_count: number;
  relationship_count: number;
  cross_institution_count: number;
  banks_present: string[];
  first_seen: string;
  last_seen: string;
  attributes: Record<string, unknown>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  clusters: string[][];
  center_entity_id: string;
  depth: number;
}

export interface GraphNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    label: string;
    entityType: string;
    bankId: string;
    riskLevel: string;
    alertCount: number;
    isCenter: boolean;
  };
  style: Record<string, string>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  type: string;
  animated: boolean;
  style: Record<string, string | number>;
  data: { confidence: number; relationshipType: string };
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  nodes_by_type: Record<string, number>;
  nodes_by_risk: Record<string, number>;
  cluster_count: number;
  database_backend?: string;
}

export interface MuleRingItem {
  ring_id: string;
  length: number;
  entity_ids: string[];
  banks_involved: string[];
  is_cross_bank: boolean;
  risk_score: number;
  total_volume: number;
  detected_at: string;
}

export interface MuleRingDetectionResponse {
  total_rings: number;
  cross_bank_rings: number;
  max_risk_score: number;
  rings: MuleRingItem[];
}

export interface SmurfingPatternItem {
  pattern_id: string;
  pattern_type: string;
  primary_entity: string;
  connected_entities: string[];
  total_amount: number;
  transaction_count: number;
  risk_score: number;
  bank_id: string;
}

export interface SmurfingDetectionResponse {
  patterns: SmurfingPatternItem[];
  total_patterns: number;
  fan_in_count: number;
  fan_out_count: number;
  layering_count: number;
}

export interface GraphClusterItem {
  cluster_id: number;
  entity_ids: string[];
  size: number;
}

export interface GraphSearchNodeItem {
  id: string;
  display_label: string;
  entity_type: string;
  bank_id: string;
  risk_level: string;
  alert_count: number;
}

export interface GraphEdgeItem {
  id: string;
  source: string;
  target: string;
  label: string;
  type: string;
  animated: boolean;
  confidence?: number;
  relationship_type?: string;
  style?: Record<string, unknown>;
  data?: Record<string, unknown>;
}

export interface GraphEdgesResponse {
  total_edges: number;
  count: number;
  edges: GraphEdgeItem[];
}

export interface EntityEmbeddingResponse {
  entity_id: string;
  embedding: number[];
  dimension: number;
}

export interface GNNSimilarityResponse {
  query_entity_id: string;
  similar_entities: Array<{ entity_id: string; similarity: number }>;
  count: number;
}


export interface ScenarioInfo {
  type: string;
  name: string;
  description: string;
  banks_involved: string[];
  estimated_events: number;
  estimated_duration_seconds: number;
}

export interface ScenarioStartResponse {
  scenario_id: string;
  scenario_type: string;
  name: string;
  total_events: number;
  status: string;
}

export interface ScenarioStatus {
  scenario_id: string;
  status: string;
  total_events: number;
  delivered_events: number;
  speed_multiplier: number;
  started_at: string;
}

export interface StreamingEvent {
  event_id: string;
  event_type: string;
  bank_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
  sequence: number;
  total: number;
  scenario_id: string;
}

export interface AttackInjectionRequest {
  attack_type: 'smurfing_layering' | 'byzantine_poisoning' | 'sybil_ring';
  adversary_bank?: string;
  target_bank?: string;
  intensity_rate?: number;
  defense_strategy?: 'krum' | 'trimmed_mean' | 'bulyan' | 'spectral' | 'spectral_svd' | 'psi_graph';
}

export interface AttackInjectionResponse {
  attack_id: string;
  attack_type: string;
  status: 'intercepted' | 'quarantined' | 'mitigated';
  defense_activated: string;
  adversary_quarantined: string | null;
  euclidean_distance: number;
  distance_threshold: number;
  packets_blocked: number;
  mitigation_latency_ms: number;
  /** Simulated live demo indicator/proxy of model resilience under defense, modeled from cosine alignment and boundary strain (not offline holdout validation AUC). */
  auc_protected: number;
  /** Simulated baseline AUC proxy showing hypothetical degradation under unmitigated attack. */
  auc_compromised_baseline: number;
  log_entry: string;
}


export interface DashboardStats {
  total_alerts: number;
  critical_alerts: number;
  open_cases: number;
  total_entities: number;
  shared_intelligence_items: number;
  cross_institution_matches: number;
  active_scenarios: number;
  graph_clusters: number;
}

export interface RiskWeights {
  ml_prediction: number;
  velocity_rules: number;
  merchant_reputation: number;
  country_risk: number;
  device_anomaly: number;
  customer_history: number;
  previous_alerts: number;
  chargeback_history: number;
  behavior_anomaly: number;
}

export const SEVERITY_COLORS: Record<string, string> = {
  critical: '#ef4444',
  high: '#f97316',
  medium: '#f59e0b',
  low: '#3b82f6',
  info: '#6b7280',
};

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  customer: '#6366f1',
  merchant: '#f59e0b',
  device: '#14b8a6',
  card: '#ec4899',
  email: '#8b5cf6',
  phone: '#06b6d4',
  ip_address: '#f43f5e',
};

export const PRIORITY_LABELS: Record<string, string> = {
  p1_critical: 'P1 - Critical',
  p2_high: 'P2 - High',
  p3_medium: 'P3 - Medium',
  p4_low: 'P4 - Low',
};

export const CASE_STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  assigned: 'Assigned',
  investigating: 'Investigating',
  pending_review: 'Pending Review',
  escalated: 'Escalated',
  sar_filed: 'SAR Filed',
  closed_confirmed: 'Closed (Confirmed)',
  closed_false_positive: 'Closed (FP)',
};

export interface Evidence {
  id: string;
  case_id: string;
  evidence_type: 'document' | 'kyc_profile' | 'ledger_proof';
  title: string;
  file_path: string;
  content_hash: string;
  uploaded_by: string;
  uploaded_at: string;
}

export interface InvestigatorAuditLog {
  id: string;
  investigator: string;
  action: string;
  target_id: string;
  timestamp: string;
  session_duration_sec: number | null;
  metadata: Record<string, unknown>;
}

export interface ShadowMetrics {
  champion_version: number;
  champion_auc: number;
  champion_pr_auc: number;
  champion_fpr: number;
  champion_latency_ms: number;
  challenger_auc: number;
  challenger_pr_auc: number;
  challenger_fpr: number;
  challenger_latency_ms: number;
  traffic_share: number;
  sample_count: number;
}

export interface BusinessRule {
  id: string;
  rule_name: string;
  condition: Record<string, any>;
  action: string;
  is_active: boolean;
  description?: string | null;
  priority?: number;
  created_at: string;
  updated_at?: string | null;
}

export interface BusinessRuleTestRequest {
  condition: Record<string, any>;
  transaction: Record<string, any>;
}

export interface BusinessRuleTestResponse {
  matches: boolean;
  message: string;
  matched_fields?: string[];
}

export interface RuleEvaluationRequest {
  transaction: Record<string, any>;
  tenant_id?: string | null;
  stop_on_first_match?: boolean;
}

export interface RuleEvaluationMatchItem {
  rule_id: string;
  rule_name: string;
  action: string;
  priority: number;
  matched_condition: Record<string, any>;
}

export interface RuleEvaluationResponse {
  evaluated_rules_count: number;
  triggered_rules_count: number;
  highest_severity_action: string;
  decision: 'ALLOW' | 'REVIEW' | 'BLOCK';
  risk_score_delta: number;
  triggered_rules: RuleEvaluationMatchItem[];
  latency_ms: number;
}

export interface PSIRequest {
  bank_a_id: string;
  bank_b_id: string;
  entity_type?: string;
  enable_fuzzy?: boolean;
  fuzzy_threshold?: number;
  enable_tee?: boolean;
}

export interface PSIMatch {
  privacy_hash: string;
  entity_type: string;
  display_label_a: string;
  display_label_b: string;
  risk_level_a: string;
  risk_level_b: string;
  matched_attributes: string[];
  similarity_score: number;
}

export interface PSIProtocolStats {
  computation_time_ms: number;
  data_exchanged_bytes: number;
  num_entities_a: number;
  num_entities_b: number;
  prime_bit_length: number | null;
  enclave_execution: boolean;
  mrenclave: string | null;
  mrsigner: string | null;
  attestation_verified: boolean | null;
}

export interface PSIResponse {
  matches: PSIMatch[];
  stats: PSIProtocolStats;
}

export interface EntityFuzzyResolveRequest {
  raw_identifier?: string;
  query_name?: string;
  entity_type: string;
  similarity_threshold?: number;
  threshold?: number;
  limit?: number;
  bank_id?: string;
}

export interface FuzzyMatchResponse {

  entity_id: string;
  display_label: string;
  entity_type: string;
  bank_id: string;
  risk_level: string;
  privacy_id: string;
  similarity: number;
  standardized_stored: string;
}

export interface CounterfactualChange {

  feature: string;
  original_value: string;
  remediated_value: string;
  delta_explanation: string;
}

export interface CounterfactualExplanation {
  alert_id: string;
  original_score: number;
  remediated_score: number;
  is_cleared: boolean;
  changes: CounterfactualChange[];
  summary_text: string;
}

export interface PolicyRuleEvaluation {
  rule_code: string;
  signal_name: string;
  weight: number;
  raw_value: number;
  normalized_score: number;
  contribution: number;
  triggered: boolean;
}

export interface DecisionReplayReport {
  alert_id: string;
  transaction_id: string;
  timestamp: string;
  model_version: string;
  model_auc: number;
  features_snapshot: Record<string, unknown>;
  graph_snapshot: Record<string, number>;
  policy_rules_evaluated: PolicyRuleEvaluation[];
  reconstructed_risk_score: number;
  reproduced_severity: string;
  audit_matched: boolean;
}

export interface EdgeContribution {
  source: string;
  target: string;
  relationship_type: string;
  weight: number;
  contribution_percentage: number;
}

export interface GNNExplanationReport {
  node_id: string;
  target_risk_level: string;
  subgraph_nodes_count: number;
  subgraph_edges_count: number;
  top_contributing_edges: EdgeContribution[];
  primary_driver_text: string;
}

export interface LIMEFeatureAttribution {
  feature: string;
  weight: number;
  value: number;
  direction: 'INCREASES_RISK' | 'DECREASES_RISK';
}

export interface LIMEExplanationReport {
  alert_id?: string;
  transaction_id?: string;
  intercept: number;
  fidelity_r2: number;
  kernel_width: number;
  num_samples: number;
  feature_attributions: LIMEFeatureAttribution[];
  explanation_text: string;
}

export interface SecurityStatus {
  mtls: {
    enabled: boolean;
    ca_cn: string;
    tls_version: string;
    peer_verification: string;
    sample_cert: { cn: string; sans: string[]; valid_until: string };
  };
  oidc: {
    enabled: boolean;
    issuer: string;
    client_id: string;
    supported_algorithms: string[];
    claims_extracted: string[];
  };
  abac: {
    enabled: boolean;
    active_rules_count: number;
    enforced_policies: string[];
  };
  vault: {
    enabled: boolean;
    vault_url: string;
    mount_point: string;
    sample_secret_source: string;
  };
  audit_chain: {
    enabled: boolean;
    total_events: number;
    chain_valid: boolean;
    last_hash: string;
    hashing_algorithm: string;
  };
}

export interface ABACEvalRequest {
  user_username?: string;
  user_bank_id?: string;
  user_roles?: string[];
  user_clearance?: number;
  user_shift_hours?: string;
  user_approval_tier?: number;
  resource_type?: string;
  resource_id?: string;
  resource_bank_id?: string;
  resource_amount?: number;
  resource_classification?: number;
  action?: string;
  hour_override?: number;
}

export interface ABACEvalResponse {
  allowed: boolean;
  policy_name: string;
  reason: string;
  evaluated_at: string;
}

export interface AuditChainEntry {
  index: number;
  event_type: string;
  actor: string;
  target_id: string;
  timestamp: string;
  details: Record<string, unknown>;
  prev_hash: string;
  curr_hash: string;
}

export interface AuditChainVerifyResponse {
  is_valid: boolean;
  total_records: number;
  broken_index: number | null;
  tamper_reason: string | null;
  genesis_hash: string;
  last_hash: string;
  verified_at: string;
}

export interface FeatureDriftResult {

  feature_name: string;
  ks_statistic: number;
  ks_p_value: number;
  wasserstein_distance: number;
  psi: number;
  status: string;
}

export interface CalibrationBinItem {
  bin_index: number;
  prob_min: number;
  prob_max: number;
  mean_predicted_prob: number;
  empirical_fraud_ratio: number;
  sample_count: number;
}

export interface CalibrationReport {
  brier_score: number;
  expected_calibration_error: number;
  max_calibration_error: number;
  is_well_calibrated: boolean;
  evaluated_at: string;
  bins: CalibrationBinItem[];
}

export interface DriftAnalysisReport {
  overall_status: string;
  max_psi: number;
  mean_ks_p_value: number;
  concept_drift_psi: number;
  auto_retrain_triggered: boolean;
  evaluated_at: string;
  feature_drifts: FeatureDriftResult[];
  calibration?: CalibrationReport | null;
}

export interface ActiveAlertItem {
  alert_name: string;
  severity: string;
  summary: string;
  started_at: string;
  status: string;
}

export interface RetrainTriggerResponse {
  triggered: boolean;
  reason: string;
  new_simulation_id?: string | null;
  triggered_at: string;
}

export interface HealthCheckResponse {
  status: string;
  service: string;
  timestamp: string;
  version: string;
  uptime_seconds: number;
}

export interface DependencyHealthStatus {
  status: string;
  latency_ms: number;
  message?: string | null;
}

export interface ReadinessResponse {
  status: 'ready' | 'degraded' | string;
  checks: Record<string, boolean | DependencyHealthStatus>;
  timestamp: string;
}

export interface SystemDiagnosticResponse {
  platform: string;
  python_version: string;
  cpu_count: number;
  memory: {
    total_mb: number;
    available_mb: number;
    used_mb: number;
    percent: number;
  };
  process_memory: {
    rss_mb: number;
    vms_mb: number;
  };
  uptime_seconds: number;
  timestamp: string;
}

export interface ConceptDriftPsiResponse {
  concept_drift_psi: number;
  overall_status: string;
  alert_level: string;
  max_feature_psi: number;
  evaluated_at: string;
  features: Record<string, number>;
}

export interface FairnessMetricsResponse {
  demographic_parity_ratio: number;
  disparate_impact_ratio: number;
  equalized_odds_difference: number;
  satisfies_four_fifths_rule: boolean;
  evaluated_at: string;
  protected_attributes: string[];
}

export interface TelemetryOverviewResponse {
  uptime_seconds: number;
  active_requests: number;
  metrics_scraped_total: number;
  alerts_firing: number;
  timestamp: string;
}

// ── Coordinator Types (Item 18) ─────────────────────────────

export interface HandshakeRequest {
  bank_id: string;
  pytorch_version: string;
  python_version: string;
  hardware_type: string;
  ram_gb: number;
  device_count?: number;
}

export interface HandshakeResponse {
  registered: boolean;
  status: 'COMPATIBLE' | 'INCOMPATIBLE';
  reason?: string | null;
  registered_at: number;
}

export interface ClientCapabilityItem {
  bank_id: string;
  pytorch_version: string;
  python_version: string;
  hardware_type: string;
  ram_gb: number;
  device_count: number;
  status: 'ONLINE' | 'OFFLINE';
  last_heartbeat_ago_seconds: number;
}

export interface NegotiatedParamsResponse {
  bank_id: string;
  batch_size: number;
  local_epochs: number;
  gradient_accumulation_steps: number;
  use_cuda: boolean;
  status: 'COMPATIBLE' | 'DEGRADED';
}

export interface AsyncUpdateRequest {
  bank_id: string;
  submitted_round: number;
  client_weights: Record<string, number[]>;
  layer_shapes?: Record<string, number[]> | null;
  sample_count?: number;
}

export interface AsyncUpdateResponse {
  success: boolean;
  bank_id: string;
  submitted_round: number;
  current_round: number;
  staleness_tau: number;
  staleness_attenuation: number;
  effective_alpha: number;
  layer_keys: string[];
}

export interface QuorumStatusResponse {
  round_number: number;
  registered_nodes_count: number;
  submitted_nodes_count: number;
  quorum_threshold_pct: number;
  current_quorum_pct: number;
  state: 'WAITING' | 'QUORUM_REACHED' | 'TIMEOUT_EXPIRED';
  start_time: string;
  target_window_seconds: number;
  time_remaining_seconds: number;
}

export interface AsyncFLEngineStatusResponse {
  current_round: number;
  alpha_staleness: number;
  learning_rate: number;
  max_staleness: number;
  staleness_function: string;
  total_updates: number;
  dropped_updates: number;
  applied_updates: number;
  average_staleness: number;
  max_observed_staleness: number;
}



// ── Privacy Defense Suite (Item 19) ────────────────────────────

export interface MIAAuditResult {
  membership_leakage_asr: number;
  risk_tier: 'safe' | 'low_risk' | 'moderate_risk' | 'high_risk';
  num_train_samples_audited: number;
  num_test_samples_audited: number;
  message?: string;
}

export interface ModelInversionAuditResult {
  reconstruction_risk_score: number;
  risk_tier: 'safe' | 'low_risk' | 'moderate_risk' | 'high_risk';
  mean_gradient_norm: number;
  std_gradient_norm: number;
  num_gradients_audited: number;
  message?: string;
}

export interface DLGAuditResult {
  dlg_leakage_score: number;
  risk_tier: 'safe' | 'low_risk' | 'moderate_risk' | 'high_risk';
  params_audited: number;
  message?: string;
}

export interface AggregationMethodInfo {
  id: string;
  label: string;
  description: string;
  byzantine_robust: boolean;
  colluding_defense: boolean;
  paper: string;
}

export interface BudgetLogEntry {
  simulation_id: string;
  total_epsilon: number;
  delta: number;
  rounds_spent: number;
  epsilon_per_round: number;
  epsilon_history: number[];
  budget_exhausted: boolean;
  epsilon_limit: number;
}

// ── Real-World Benchmark, Fidelity & Design Partner Types ────

export interface ConfusionMatrixAtThreshold {
  threshold: number;
  true_positives: number;
  false_positives: number;
  true_negatives: number;
  false_negatives: number;
  precision: number;
  recall: number;
  fpr: number;
  fnr: number;
  specificity: number;
  f1_score: number;
}

export interface AlertFatigueAndCostReport {
  threshold: number;
  daily_transactions: number;
  daily_alerts_generated: number;
  false_positive_alerts_daily: number;
  legitimate_customers_impacted_per_10k: number;
  estimated_daily_fraud_loss_dollars: number;
  estimated_daily_investigation_cost_dollars: number;
  total_daily_cost_dollars: number;
  optimal_cost_threshold: number;
  minimized_total_cost_dollars: number;
}

export interface FeatureFidelityMetric {
  feature_name: string;
  wasserstein_distance: number;
  js_divergence: number;
  ks_statistic: number;
  ks_p_value: number;
  real_mean: number;
  synth_mean: number;
  real_std: number;
  synth_std: number;
  fidelity_score: number;
}

export interface DistributionFidelityReport {
  dataset_name: string;
  overall_fidelity_score: number;
  avg_wasserstein_distance: number;
  avg_js_divergence: number;
  covariance_matrix_drift_frobenius: number;
  class_imbalance_ratio_real: number;
  class_imbalance_ratio_synth: number;
  feature_metrics: FeatureFidelityMetric[];
  degradation_metrics: {
    synthetic_auc: number;
    real_world_auc: number;
    auc_degradation_delta: number;
    synthetic_pr_auc: number;
    real_world_pr_auc: number;
    pr_auc_degradation_delta: number;
    recall_at_01_fpr_drop: number;
  };
  summary_verdict: 'HIGH_FIDELITY' | 'MODERATE_SHIFT' | 'EXTREME_SHIFT';
}

export interface BenchmarkModelPerformance {
  roc_auc: number;
  pr_auc: number;
  recall_at_01_fpr: number;
  cost_report: AlertFatigueAndCostReport;
}

export interface BenchmarkEvaluationResponse {
  dataset_name: string;
  source_type: string;
  total_transactions_evaluated: number;
  actual_fraud_count: number;
  actual_fraud_rate_percent: number;
  performance_comparison: {
    federated_learning: BenchmarkModelPerformance;
    isolated_local_model: BenchmarkModelPerformance;
    federated_advantage: {
      pr_auc_gain: number;
      recall_at_01_fpr_gain: number;
      daily_fraud_loss_saved_dollars: number;
      daily_investigation_saved_dollars: number;
      net_daily_economic_benefit_dollars: number;
    };
  };
  multi_threshold_confusion_matrices: ConfusionMatrixAtThreshold[];
  distribution_fidelity: DistributionFidelityReport;
  bank_partitions: Array<{
    bank_id: string;
    samples: number;
    fraud_count: number;
    fraud_ratio: number;
  }>;
}

export interface PilotComplianceChecklist {
  partner_name: string;
  jurisdiction: string;
  overall_readiness_score: number;
  status: 'APPROVED_FOR_PILOT' | 'CONDITIONAL_APPROVAL' | 'REJECTED';
  compliance_items: Array<{
    standard: string;
    clause: string;
    status: string;
    evidence: string;
  }>;
  cryptographic_guarantees: Record<string, string>;
}

export interface PiiValidationResponse {
  partner_name: string;
  schema_format: string;
  is_clean_zero_pii: boolean;
  total_records_scanned: number;
  violations: Array<{
    column: string;
    pii_type: string;
    sample_count: number;
    remediation: string;
  }>;
  status: string;
  guidance: string;
}

// ── Connector Diagnostics & Infrastructure Health ────

export interface ConnectorHealthSummary {
  connector_id: string;
  name: string;
  category: string;
  status: 'HEALTHY' | 'DEGRADED' | 'SANDBOX_ACTIVE' | 'OFFLINE';
  latency_ms: number;
  endpoint: string;
  protocol: string;
  version: string;
  last_checked: string;
  details: Record<string, any>;
}

export interface DiagnosticsOverviewResponse {
  total_connectors: number;
  healthy_connectors: number;
  avg_latency_ms: number;
  connectors: ConnectorHealthSummary[];
}

export interface ConnectorTestProbeResult {
  connector_id: string;
  name: string;
  success: boolean;
  round_trip_ms: number;
  status_code: number;
  handshake_summary: string;
  diagnostics_log: string[];
  payload_sample: Record<string, any>;
}

// ── Real Dataset Ingestion Studio Types ─────────────

export interface ColumnMappingItem {
  source_column: string;
  target_signal: string;
  data_type: string;
  sample_values: any[];
  is_required: boolean;
  confidence_score: number;
}

export interface DatasetPreviewRequest {
  filename: string;
  file_format: 'csv' | 'parquet' | 'tsv' | 'gz';
  raw_header: string[];
  sample_rows: Record<string, any>[];
  total_bytes?: number;
}

export interface DatasetPreviewResponse {
  preview_id: string;
  filename: string;
  file_format: string;
  inferred_delimiter: string;
  row_count_estimate: number;
  detected_columns: string[];
  column_mappings: ColumnMappingItem[];
  schema_compliance_ratio: number;
  pii_violations_detected: number;
  pii_masked_receipt: string;
}

export interface ExpectationCheckResult {
  expectation_name: string;
  column: string;
  status: 'passed' | 'failed' | 'warning';
  observed_value: any;
  expected_threshold: string;
  details: string;
}

export interface DatasetContractAuditRequest {
  preview_id: string;
  bank_id?: string;
  column_mapping?: Record<string, string>;
  quarantine_threshold_pct?: number;
}

export interface DatasetContractAuditResponse {
  audit_id: string;
  bank_id: string;
  status: 'passed' | 'quarantined' | 'rejected';
  total_records: number;
  passed_records: number;
  quarantined_records: number;
  contract_checks: ExpectationCheckResult[];
  overall_compliance_score: number;
  fraud_ratio_detected: number;
  dirichlet_alpha_estimate: number;
  drift_ks_score: number;
  quarantine_csv_download_url?: string | null;
  audit_message: string;
}

export interface DatasetConsortiumEnrollRequest {
  audit_id: string;
  target_bank_id?: string;
  allocation_mode?: 'replace_partition' | 'append_partition' | 'guest_node';
  trigger_fl_round?: boolean;
}

export interface DatasetConsortiumEnrollResponse {
  enrollment_id: string;
  bank_id: string;
  node_status: string;
  records_enrolled: number;
  features_dimension: number;
  partition_assigned: string;
  next_action_url: string;
}

export interface TuneRequest {
  study_name?: string;
  dirichlet_alpha?: number;
  num_clients?: number;
  num_rounds?: number;
  n_trials?: number;
  timeout_seconds?: number | null;
}

export interface TuneResponse {
  study_name: string;
  dirichlet_alpha: number;
  best_trial_number: number;
  best_value: number;
  best_params: Record<string, any>;
  param_importances: Record<string, number>;
  total_trials: number;
  completed_trials: number;
  pruned_trials: number;
  duration_ms: number;
}

export interface HyperparameterBound {
  name: string;
  type: string;
  range?: [number, number] | null;
  choices?: any[] | null;
  scale: string;
  default_value: any;
  description: string;
}

export interface HyperparameterRangesResponse {
  search_space: Record<string, HyperparameterBound>;
  sampler: string;
  pruner: string;
  default_objective: string;
}

export interface ParetoPoint {
  trial_id: number;
  learning_rate: number;
  batch_size: number;
  fedprox_mu: number;
  dp_epsilon: number;
  auc_roc: number;
  latency_ms: number;
  is_pareto_optimal: boolean;
}

export interface ParetoFrontResponse {
  total_evaluated_points: number;
  pareto_optimal_count: number;
  pareto_points: ParetoPoint[];
  recommendation?: ParetoPoint | null;
}

export interface StudyListResponse {
  studies: string[];
  total_studies: number;
}


export interface UnlearnBankRequest {
  target_bank_id: string;
  unlearning_method?: string;
  start_round?: number;
  end_round?: number;
  ascent_lr?: number;
  ascent_steps?: number;
  projection_radius?: number;
}

export interface UnlearnBankResponse {
  target_bank_id: string;
  unlearning_method: string;
  initial_model_l2_norm: number;
  unlearned_model_l2_norm: number;
  parameter_drift_delta: number;
  hessian_spectral_radius: number;
  mia_membership_probability: number | null;
  execution_time_ms: number;
  erasure_verified: boolean;
  lineage_hash: string;
  audit_log: Array<{
    step: number;
    name: string;
    status: string;
  }>;
  retained_banks: string[];
}

export interface CalibrateNoiseRequest {
  target_epsilon: number;
  target_delta?: number;
  sensitivity?: number;
  mechanism?: string;
}

export interface CalibrateNoiseResponse {
  mechanism: string;
  target_epsilon: number;
  target_delta: number;
  sensitivity: number;
  calibrated_sigma: number;
  formula: string;
}

export interface RDPCompositionRequest {
  sigmas: number[];
  target_delta?: number;
  sample_ratio_q?: number;
  orders?: number[];
}

export interface RDPCompositionResponse {
  total_rounds: number;
  cumulative_epsilon: number;
  optimal_order_alpha: number;
  naive_sum_epsilon: number;
  privacy_saving_pct: number;
  target_delta: number;
  rdp_map: Record<string, number>;
}

// ── Autonomous Agentic AML Copilot & Evidence Assembly ─────────

export interface CaseEvidenceDossier {
  case_id: string;
  case_title: string;
  case_status: string;
  total_risk_score: number;
  alert_ids: string[];
  timeline_events: Array<{
    event_type: string;
    description: string;
    actor: string;
    timestamp: string;
    metadata?: Record<string, unknown>;
  }>;
  evidence_artifacts: Array<{
    id: string;
    title: string;
    evidence_type: string;
    file_path: string;
    content_hash: string;
    uploaded_by: string;
    uploaded_at: string;
  }>;
  investigator_notes: string[];
  shap_drivers: Array<{
    feature: string;
    impact: number;
    description?: string;
  }>;
  graph_topology: Record<string, unknown>;
  pii_sanitized_count: number;
  evidence_hash: string;
  assembled_at: number;
}

export interface CopilotQueryRequest {
  case_id: string;
  include_fincen_narrative?: boolean;
  include_four_eyes_briefing?: boolean;
  custom_investigator_notes?: string | null;
  shap_attributions?: Array<{ feature: string; impact: number; description?: string }>;
  graph_metadata?: Record<string, unknown>;
}

export interface CopilotDirectGenerationRequest {
  case_id: string;
  shap_attributions?: Array<{ feature: string; impact: number; description?: string }>;
  graph_nodes?: Array<Record<string, unknown>> | Record<string, unknown>;
  custom_investigator_notes?: string | null;
  risk_score?: number | null;
  require_existing_case?: boolean;
}

export interface CopilotQueryResponse {
  case_id: string;
  fincen_sar_narrative: string;
  four_eyes_briefing: string;
  recommended_action: string;
  top_risk_drivers: Array<{
    feature: string;
    impact: number;
    description?: string;
  }>;
  graph_topology_summary: Record<string, unknown>;
  zero_pii_verified: boolean;
  generated_at: string;
  lineage_hash: string;
  evidence_count?: number;
  timeline_event_count?: number;
  sar_narrative?: string;
  supervisor_briefing?: string;
}

export interface AssembledEvidenceResponse {
  case_id: string;
  case_title: string;
  case_status: string;
  total_risk_score: number;
  evidence_hash: string;
  evidence_count: number;
  timeline_event_count: number;
  pii_sanitized_count: number;
  assembled_at: number;
}

export interface CopilotStatusResponse {
  status: string;
  zero_pii_engine: string;
  synthesized_analyses_count: number;
  timestamp: string;
}

// ── Label Feedback Loop & Retraining Store ────────
export type FeedbackLabel = 'CONFIRMED_FRAUD' | 'FALSE_POSITIVE';

export interface LabelFeedbackItem {
  transaction_id_hash: string;
  label: FeedbackLabel;
  weight: number;
  priority: number;
  feature_vector?: number[] | null;
  notes?: string | null;
  metadata?: Record<string, unknown>;
  recorded_at: string;
  consumed_for_retraining: boolean;
}

export interface AnalystFeedbackIngestRequest {
  tenant_id?: string;
  transaction_id_hash?: string | null;
  alert_id?: string | null;
  determination?: string;
  priority?: number | null;
  weight?: number | null;
  feature_vector?: number[] | null;
  notes?: string | null;
  raw_attributes?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
}

export interface AnalystFeedbackIngestResponse {
  status: string;
  item: Record<string, unknown>;
}

export interface FeedbackStatsResponse {
  tenant_id: string;
  total_count: number;
  fraud_count: number;
  false_positive_count: number;
  consumed_count: number;
  unconsumed_count: number;
  priority_distribution: Record<number, number>;
}

export interface RetrainingBatchRequest {
  tenant_id?: string;
  batch_size?: number;
  mark_consumed?: boolean;
  min_priority?: number;
  stratified?: boolean;
}

export interface RetrainingBatchResponse {
  tenant_id: string;
  batch_size: number;
  items: Array<Record<string, unknown>>;
  fraud_count: number;
  false_positive_count: number;
  mean_priority: number;
}

export interface DPGradientRequest {
  tenant_id?: string;
  epsilon?: number;
  delta?: number;
  clip_norm?: number;
}

export interface DPGradientResponse {
  tenant_id: string;
  delta_weights: number[];
  sample_count: number;
  epsilon: number;
  delta: number;
  sigma: number;
}

export interface ClearFeedbackBufferResponse {
  status: string;
  tenant_id: string;
  cleared_count: number;
}

// ── FinCEN SAR 2.0 e-Filing & Regulatory Submissions ────────
export interface SARFilingRecord {
  filing_id: string;
  submission_id: string;
  case_id: string;
  sha256_hash: string;
  status: 'FILED' | 'VALIDATED';
  institution_name: string;
  created_at: string;
  xml_path: string;
  alert_count: number;
  subject_count: number;
  total_risk_score: number;
}

export interface SARValidationResult {
  valid: boolean;
  sha256_hash: string;
  root_element: string;
  schema_version: string;
  byte_size: number;
}

export interface ExportFinCENXmlRequest {
  case_id: string;
  filer_id?: string | null;
  narrative_override?: string | null;
  institution_name?: string | null;
}

export interface ExportFinCENXmlResponse {
  submission_id: string;
  status: string;
  xml: string;
  xml_payload?: string | null;
  sha256_hash?: string | null;
  filing_status?: string | null;
  pdf_download_url: string;
}

export interface SARGenerateRequest {
  case_id: string;
  institution_name?: string | null;
  tin_type?: string | null;
  narrative_override?: string | null;
}

// ── Enterprise Data Retention & GDPR Art. 17 Erasure ────────
export type RetentionDataCategory =
  | 'TRANSACTION_LOGS'
  | 'INFERENCE_AUDITS'
  | 'GRAPH_EDGES'
  | 'EXPLAINABILITY_REPORTS'
  | 'CUSTOMER_ENTITIES';

export type ErasureSanitizationMethod =
  | 'HARD_DELETE'
  | 'CRYPTOGRAPHIC_ZEROIZATION'
  | 'ANONYMIZATION';

export interface RetentionPolicyRequest {
  tenant_id: string;
  category: RetentionDataCategory;
  ttl_days: number;
  erasure_method?: ErasureSanitizationMethod;
}

export interface RetentionPolicyResponse {
  tenant_id: string;
  category: RetentionDataCategory;
  ttl_days: number;
  erasure_method: ErasureSanitizationMethod;
}

export interface RetentionPurgeRequest {
  tenant_id: string;
}

export interface GDPRErasureRequest {
  tenant_id: string;
  entity_id_hash: string;
  category?: RetentionDataCategory | null;
}

export interface ErasureAuditRecordResponse {
  erasure_id: string;
  tenant_id: string;
  category: RetentionDataCategory;
  records_erased_count: number;
  erasure_hash: string;
  timestamp: string;
  status: string;
  prev_erasure_hash?: string | null;
  affected_tables?: string[];
}

export interface ErasureChainVerificationResponse {
  valid: boolean;
  total_records: number;
  last_hash: string;
  tamper_reason?: string | null;
}

// ── Authentication & Token Lifecycle Types ────────────────────────────────────

export interface LoginRequest {
  username: string;
  password: string;
  tenant_id?: string | null;
}

export interface UserProfileResponse {
  user_id: string;
  username: string;
  bank_id: string;
  tenant_id: string;
  roles: string[];
  clearance_level: number;
  permissions: string[];
  is_active?: boolean;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  refresh_expires_in: number;
  tenant_id: string;
  role: string;
  user: UserProfileResponse;
}

export type TokenResponse = LoginResponse;

export interface RefreshTokenRequest {
  refresh_token: string;
}

export type RefreshRequest = RefreshTokenRequest;

export interface TokenVerifyResponse {
  valid: boolean;
  claims: Record<string, unknown> | null;
  detail: string;
}

export interface LogoutRequest {
  token?: string | null;
}

export interface LogoutResponse {
  status: string;
  detail: string;
}

export interface LockoutStatusResponse {
  identifier: string;
  is_locked: boolean;
  failed_attempts: number;
  max_attempts: number;
  remaining_seconds: number;
  lockout_duration_seconds: number;
  is_locked_out?: boolean | null;
  remaining_lockout_seconds?: number | null;
  user_failure_count?: number | null;
  ip_failure_count?: number | null;
}

// ── Gateway & Perimeter Ingress Types ─────────────────────────────────────────

export interface GatewayServiceRoute {
  service_name: string;
  http_url: string;
  ws_url: string;
  healthy: boolean;
  latency_ms: number;
}

export interface GatewayRateLimitConfig {
  enabled: boolean;
  limit_per_minute: number;
  tracked_clients: number;
  storage_backend: string;
}

export interface GatewayStatusResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  uptime_seconds: number;
  mode: string;
  downstream_services: Record<string, GatewayServiceRoute>;
  path_mappings_count: number;
  rate_limit: GatewayRateLimitConfig;
  timestamp: string;
}

export interface GatewayHealthResponse {
  status: string;
  healthy: boolean;
  service: string;
  services_ready: Record<string, boolean>;
  timestamp: string;
}

export interface GatewayMetricsResponse {
  service: string;
  requests_total: number;
  requests_by_method: Record<string, number>;
  rate_limited_total: number;
  auth_failures_total: number;
  abac_denials_total: number;
  downstream_errors_total: number;
  avg_latency_ms: number;
  uptime_seconds: number;
}

// ── Developer Webhook Gateway Types ──────────────────────────────────────────

export type WebhookEventType =
  | 'ALERT_CREATED'
  | 'CASE_RESOLVED'
  | 'MODEL_PROMOTED'
  | 'DRIFT_DETECTED';

export interface WebhookSubscriptionRequest {
  tenant_id: string;
  target_url: string;
  events: WebhookEventType[];
}

export interface WebhookSubscriptionResponse {
  subscription_id: string;
  tenant_id: string;
  target_url: string;
  secret_key: string;
  events: WebhookEventType[];
  created_at?: string;
  is_active: boolean;
}

export interface WebhookSubscriptionItem {
  subscription_id: string;
  tenant_id: string;
  target_url: string;
  events: WebhookEventType[];
  created_at?: string;
  is_active: boolean;
}

export interface WebhookSubscriptionListResponse {
  tenant_id?: string | null;
  subscriptions: WebhookSubscriptionItem[];
  total_count: number;
}

export interface WebhookTestDispatchResponse {
  dispatched_count: number;
  event_type: string;
  sample_signature?: string | null;
}

export interface WebhookVerifyRequest {
  payload: Record<string, unknown>;
  signature: string;
  secret_key: string;
}

export interface WebhookVerifyResponse {
  valid: boolean;
  algorithm: string;
}

export interface WebhookDeliveryLogItem {
  delivery_id: string;
  target_url: string;
  event_type: string;
  status_code?: number | null;
  success: boolean;
  attempt_count: number;
  error_message?: string | null;
  timestamp: string;
}

export interface WebhookDeliveryLogsResponse {
  deliveries: WebhookDeliveryLogItem[];
  total_count: number;
}

export interface WebhookDeleteResponse {
  subscription_id: string;
  deleted: boolean;
  message: string;
}

export interface WebhookHealthResponse {
  status: string;
  service: string;
  active_subscriptions: number;
  total_deliveries: number;
  timestamp: string;
}

// ── Real-Time Scoring, Batch Predict & Inference Types ──────────────────────

export interface SignalBreakdownItem {
  signal_name: string;
  weight: number;
  raw_value: number;
  normalized_score: number;
  explanation: string;
}

export interface AlertDetailsItem {
  alert_id: string;
  severity: string;
  status: string;
  reason_codes: string[];
  explanation: string;
  top_features: Array<Record<string, any>>;
  risk_factors: string[];
}

export interface TransactionPredictRequest {
  transaction_amount: number;
  merchant_category?: string;
  country_code?: string;
  device_type?: string;
  velocity?: number;
  hour_of_day?: number;
  merchant_risk_score?: number;
  customer_history_score?: number;
  chargeback_count?: number;
  account_age_days?: number;
  bank_id?: string;
  simulation_id?: string;
  transaction_id?: string;
}

export interface TransactionPredictResponse {
  transaction_id: string | null;
  fraud_probability: number;
  risk_score: number;
  is_fraud_suspected: boolean;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  breakdown: SignalBreakdownItem[];
  alert_details?: AlertDetailsItem | null;
  policy_action: string;
  triggered_rules: string[];
  latency_ms: number;
}

export interface BatchPredictionItem {
  transaction_id: string;
  fraud_probability: number;
  risk_score: number;
  decision: 'ALLOW' | 'REVIEW' | 'BLOCK';
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  is_fraud_suspected: boolean;
  policy_action: string;
  latency_ms: number;
}

export interface BatchPredictionResponse {
  total_processed: number;
  fraud_suspected_count: number;
  predictions: BatchPredictionItem[];
  batch_latency_ms: number;
}

export interface FeatureAttributionItem {
  feature: string;
  value: number;
  contribution: number;
  direction: 'INCREASES_RISK' | 'DECREASES_RISK';
  description: string;
}

export interface CounterfactualPathItem {
  feature: string;
  original_value: number;
  target_value: number;
  description: string;
}

export interface ExplainTransactionResponse {
  transaction_id: string;
  method: string;
  base_value: number;
  predicted_score: number;
  attributions: FeatureAttributionItem[];
  summary: string;
  counterfactual_paths: CounterfactualPathItem[];
  latency_ms: number;
}

export interface ScoreTransactionRequest {
  transaction_id: string;
  account_id: string;
  amount: number;
  currency?: string;
  merchant_id: string;
  country?: string;
  device_id: string;
}

export interface FeatureContributionItem {
  feature: string;
  contribution: number;
}

export interface RelatedEntityItem {
  entity_type: string;
  risk: string;
}

export interface ScoreTransactionResponse {
  risk_score: number;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH';
  decision: 'ALLOW' | 'REVIEW' | 'BLOCK';
  model_version: string;
  explanations: FeatureContributionItem[];
  related_entities: RelatedEntityItem[];
  latency_ms: number;
}

export interface RealtimeInferenceRequest {
  transaction_id: string;
  amount: number;
  currency?: string;
  source_account: string;
  target_account: string;
  merchant_category?: string;
  velocity_1h?: number;
  force_fallback?: boolean;
}

export interface RealtimeInferenceResponse {
  transaction_id: string;
  risk_score: number;
  decision: 'ALLOW' | 'REVIEW' | 'BLOCK';
  latency_ms: number;
  evaluated_by: 'ML_MODEL' | 'HEURISTIC_FALLBACK';
  explanation: string;
}

export interface InferenceQuotaResponse {
  tenant_id: string;
  tier: string;
  daily_inferences_limit: number;
  daily_inferences_used: number;
  daily_inferences_remaining: number;
  monthly_fl_rounds_limit: number;
  monthly_fl_rounds_used: number;
  monthly_fl_rounds_remaining: number;
  storage_used_mb: number;
  max_storage_mb: number;
  reset_date: string;
}

// ── Open Banking PSD2 & ISO 20022 Schemas ──────────────────────────────────

export interface PSD2ConsentRequest {
  account_id: string;
  permissions?: string[];
  valid_until: number;
  debtor_iban?: string;
  tenant_id?: string;
}

export interface PSD2ConsentResponse {
  consent_id: string;
  status: 'valid' | 'expired' | 'revoked';
  account_id: string;
  permissions: string[];
  valid_until: number;
  debtor_iban?: string;
  created_at: string;
  client_id: string;
  tenant_id: string;
}

export interface PSD2Account {
  account_id: string;
  iban: string;
  currency: string;
  balance: number;
  bank_name: string;
  status: string;
}

export interface PSD2Transaction {
  transaction_id: string;
  amount: number;
  currency: string;
  booking_date: string;
  debtor_name: string;
  creditor_name: string;
  remittance_info: string;
  status: string;
}

export interface PaymentInitiationRequest {
  debtor_account: string;
  creditor_account: string;
  instructed_amount: number;
  currency?: string;
  creditor_name: string;
  debtor_name?: string;
  remittance_information?: string;
  payment_product?: 'sepa-credit-transfers' | 'instant-sepa-credit-transfers' | 'cross-border-credit-transfers';
  consent_id?: string;
  debtor_agent_bic?: string;
  creditor_agent_bic?: string;
  tenant_id?: string;
}

export interface PaymentInitiationResponse {
  payment_id: string;
  transaction_status: 'RCVD' | 'ACTC' | 'ACSP' | 'ACCP' | 'RJCT';
  debtor_account: string;
  creditor_account: string;
  instructed_amount: number;
  currency: string;
  creditor_name: string;
  payment_product: string;
  risk_score: number;
  is_flagged_for_review: boolean;
  created_at: string;
  estimated_settlement: string;
  tenant_id: string;
}

export interface PaymentStatusResponse {
  payment_id: string;
  transaction_status: 'RCVD' | 'ACTC' | 'ACSP' | 'ACCP' | 'RJCT';
  debtor_account: string;
  creditor_account: string;
  instructed_amount: number;
  currency: string;
  created_at: string;
  last_updated: string;
  clearing_system_ref?: string;
  tenant_id: string;
}

export interface ISO20022ParseRequest {
  raw_content: string;
  message_type?: 'auto' | 'pacs.008' | 'pain.001' | 'mt103';
  anonymize_pii?: boolean;
  salt?: string;
}

export interface ISO20022ParseResponse {
  message_type: string;
  transaction_id: string;
  amount: number;
  currency: string;
  date: string;
  sender_name?: string;
  sender_account: string;
  sender_bic?: string;
  sender_country: string;
  receiver_name?: string;
  receiver_account: string;
  receiver_bic?: string;
  receiver_country: string;
  remittance_info?: string;
  is_valid_debtor_iban: boolean;
  is_valid_creditor_iban: boolean;
  privacy_features?: Record<string, unknown>;
}
export interface EntityRelationshipItem {
  id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: string;
  confidence: number;
  evidence: string;
  created_at: string;
}

export interface EntityDeleteResponse {
  deleted: boolean;
  entity_id: string;
  policy: string;
}

export interface HMACTokenizeRequest {
  identifier: string;
  tenant_salt?: string;
}

export interface HMACTokenizeResponse {
  hmac_token: string;
  policy: string;
  algorithm: string;
}

export interface PSIMatchDirectRequest {
  source_bank_id?: string;
  target_bank_id?: string;
  client_ecdh_blinded_hashes?: string[];
  enable_fuzzy?: boolean;
}

export interface PSIMatchDirectResponse {
  protocol: string;
  matched_cardinality: number;
  matches: Array<Record<string, unknown>>;
  stats: Record<string, unknown>;
  zero_raw_pii_enforced: boolean;
}

export interface PSIStatsResponse {
  protocol: string;
  prime_bit_length: number;
  hash_function: string;
  supported_modes: string[];
  zero_raw_pii_guarantee: string;
}


