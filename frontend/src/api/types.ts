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
  dp_mode: 'post_hoc' | 'opacus';
  bank_a_transactions: number;
  bank_b_transactions: number;
  bank_c_transactions: number;
  aggregation_method: 'fed_avg_weighted' | 'fed_avg' | 'krum' | 'coordinate_wise_median' | 'trimmed_mean' | 'bulyan' | 'fed_adam' | 'fed_adagrad' | 'fed_yogi' | 'scaffold';

  enable_poisoning_simulation: boolean;
  poisoning_bank_id: string;
  poisoning_scale: number;
  fl_engine_type: 'custom' | 'flower';

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

export interface BankInfo {
  id: string;
  name: string;
  tier: string;
  description: string;
  default_fraud_ratio: number;
  default_transactions: number;
  fraud_pattern: string;
  characteristics: string[];
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
  top_features: { feature: string; contribution: number }[];
  risk_factors: string[];
  model_confidence: number;
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
  created_at: string;
  updated_at?: string;
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
  raw_identifier: string;
  entity_type: string;
  similarity_threshold?: number;
  limit?: number;
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
