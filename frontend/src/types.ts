export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface PredictPayload {
  transaction_amount: number;
  merchant_category: string;
  country_code: string;
  device_type: string;
  velocity: number;
  hour_of_day: number;
  merchant_risk_score: number;
  customer_history_score: number;
  chargeback_count: number;
  account_age_days: number;
  bank_id: string;
}

export interface AlertDetails {
  alert_id: string;
  severity: string;
  reason_codes: string[];
  explanation: string;
  top_features: Array<{ feature: string; contribution: number }>;
}

export interface PredictResponse {
  fraud_probability: number;
  risk_score: number;
  is_fraud_suspected: boolean;
  risk_level: RiskLevel;
  breakdown: Record<string, number>;
  alert_details: AlertDetails | null;
  policy_action: string;
  triggered_rules: string[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: 'CUSTOMER' | 'MERCHANT' | 'DEVICE' | 'IP' | 'BANK';
  riskScore: number;
  bankId: string;
  degree: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  weight: number;
  relation: string;
}

export interface CounterfactualChange {
  feature: string;
  original_value: number | string;
  suggested_value: number | string;
  delta: number;
  description: string;
}

export interface CounterfactualReport {
  alert_id: string;
  original_score: number;
  remediated_score: number;
  is_cleared: boolean;
  changes: CounterfactualChange[];
  summary_text: string;
}

export interface FLRoundResult {
  round_number: number;
  global_loss: number;
  per_bank_loss: Record<string, number>;
  participating_bank_ids: string[];
  dropped_bank_ids: string[];
  aggregation_time_ms: number;
}

export interface FLSimulationRequest {
  num_rounds: number;
  local_epochs: number;
  learning_rate: number;
  algorithm: string;
  dp_epsilon: number;
  dp_delta: number;
}

export interface SystemHealthStatus {
  status: string;
  service: string;
  environment: string;
  redis_connected: boolean;
  version: string;
}

// ── Label Feedback & Retraining Store ────────
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

export interface FeedbackStatsResponse {
  tenant_id: string;
  total_count: number;
  fraud_count: number;
  false_positive_count: number;
  consumed_count: number;
  unconsumed_count: number;
  priority_distribution: Record<number, number>;
}

export interface RetrainingBatchResponse {
  tenant_id: string;
  batch_size: number;
  items: LabelFeedbackItem[];
  fraud_count: number;
  false_positive_count: number;
  mean_priority: number;
}

export interface DPGradientResponse {
  tenant_id: string;
  delta_weights: number[];
  sample_count: number;
  epsilon: number;
  delta: number;
  sigma: number;
}

// ── Asset Recovery & Collaborative FININT Operational Hub ─────────────────────

export interface AssetRecoverySummaryResponse {
  snapshot_at: string;
  total_events: number;
  total_eur_frozen: number;
  total_eur_recovered: number;
  contagion_containment_rate: number;
  mttr_mean_minutes: number;
  mttr_p50_minutes: number;
  mttr_p90_minutes: number;
  mttr_p99_minutes: number;
  legacy_baseline_minutes: number;
  mttr_reduction_pct: number;
  mule_chains_disrupted: number;
  consortium_banks_active: number;
  active_provisional_holds: number;
}

export interface TimelineDataPointResponse {
  period_start: string;
  eur_frozen: number;
  eur_recovered: number;
  event_count: number;
  avg_mttr_minutes: number;
}

export interface TypologyBreakdownResponse {
  typology: string;
  event_count: number;
  total_eur: number;
  avg_mttr_minutes: number;
  containment_rate: number;
  risk_label: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
}

export interface RecordRecoveryEventRequest {
  event_type: 'RECALL_SUCCESS' | 'PROVISIONAL_HOLD' | 'PARTIAL_RECOVERY';
  amount_eur: string;
  typology: string;
  originating_bank_id: string;
  receiving_bank_id: string;
  recall_message_id?: string;
  finint_ticket_id?: string;
}

export interface RecordRecoveryEventResponse {
  event_id: string;
  event_type: string;
  amount_eur: number;
  typology: string;
  mttr_minutes: number | null;
  audit_hash: string;
  recorded_at: string;
}
