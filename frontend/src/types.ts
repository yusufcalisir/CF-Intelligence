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
