import type { Alert } from '../../api/types';

let alertIdCounter = 1;

/**
 * Creates a deterministic mock Alert object with optional overrides.
 */
export function createMockAlert(overrides: Partial<Alert> = {}): Alert {
  const id = overrides.id || `ALT-${String(alertIdCounter++).padStart(4, '0')}`;
  const bankId = overrides.bank_id || 'bank_a';
  const severity = overrides.severity || 'critical';
  const score = overrides.risk_score ?? (severity === 'critical' ? 880 : severity === 'high' ? 680 : 350);

  return {
    id,
    bank_id: bankId,
    transaction_id: `TXN-${id}`,
    risk_score: score,
    severity,
    status: overrides.status || 'open',
    reason_codes: overrides.reason_codes || ['HIGH_VELOCITY', 'STRUCTURING_RISK', 'CRYPTO_OFFRAMP'],
    confidence: overrides.confidence ?? 0.94,
    involved_entity_ids: overrides.involved_entity_ids || [`ENT-${id}-01`, `ENT-${id}-02`],
    created_at: '2026-08-14T10:00:00Z',
    top_features: overrides.top_features || [
      { feature: 'amount', contribution: 0.42 },
      { feature: 'velocity', contribution: 0.35 },
      { feature: 'device_anomaly', contribution: 0.23 },
    ],
    risk_factors: overrides.risk_factors || [
      'Structuring Pattern: Transaction split below $10k threshold within 1 hour',
      'Consortium Risk: Counterparty flagged by Bank B in Round 4',
    ],
    model_confidence: overrides.model_confidence ?? 0.94,
    ...overrides,
  };
}

/**
 * Generates a batch of mock alerts.
 */
export function createMockAlertList(count: number, overrides: Partial<Alert> = {}): Alert[] {
  return Array.from({ length: count }, (_, i) =>
    createMockAlert({
      ...overrides,
      id: `ALT-BATCH-${String(i + 1).padStart(3, '0')}`,
    })
  );
}

/**
 * Creates mock Explainability details for a given alert.
 */
export function createMockAlertExplainability(alertId: string, overrides: Record<string, any> = {}) {
  return {
    alert_id: alertId,
    shap_values: [
      { feature: 'transaction_amount', value: 25000.0, shap_value: 0.45 },
      { feature: 'velocity_1h', value: 14.0, shap_value: 0.38 },
      { feature: 'counterparty_risk_tier', value: 4.0, shap_value: 0.25 },
      { feature: 'off_hours_activity', value: 1.0, shap_value: 0.12 },
    ],
    base_value: 0.05,
    prediction_value: 0.92,
    explanation_summary: 'Alert triggered primarily by extreme 1-hour velocity combined with high transaction amount.',
    ...overrides,
  };
}

/**
 * Creates mock Decision Replay audit traces.
 */
export function createMockDecisionReplay(alertId: string, overrides: Record<string, any> = {}) {
  return {
    alert_id: alertId,
    model_version: 'v2.4.0-champion',
    evaluation_timestamp: '2026-08-14T10:00:01Z',
    input_snapshot: {
      amount: 25000.0,
      velocity: 14.0,
      country_code: 'NG',
      device_type: 'mobile_app',
    },
    layer_activations: {
      embedding_layer: [0.12, 0.45, 0.88, 0.31],
      gnn_aggregator: [0.89, 0.94, 0.72],
      output_logit: 0.92,
    },
    triggered_rules: ['RULE-001-STRUCTURING', 'RULE-004-VELOCITY'],
    policy_action: 'BLOCK_AND_FLAG',
    ...overrides,
  };
}

/**
 * Creates mock GNN Attribution graph data.
 */
export function createMockGNNAttribution(alertId: string, overrides: Record<string, any> = {}) {
  return {
    alert_id: alertId,
    subgraph_nodes: [
      { id: 'node_01', type: 'account', label: 'Primary Mule Account', risk: 0.94 },
      { id: 'node_02', type: 'merchant', label: 'High-Risk Crypto Exchange', risk: 0.88 },
      { id: 'node_03', type: 'device', label: 'Emulated Device ID', risk: 0.76 },
    ],
    subgraph_edges: [
      { source: 'node_01', target: 'node_02', weight: 0.92, label: 'RAPID_TRANSFER' },
      { source: 'node_01', target: 'node_03', weight: 0.85, label: 'DEVICE_FINGERPRINT' },
    ],
    gnn_model_architecture: 'GraphSAGE-Heterogeneous-2L',
    attention_weights: [0.65, 0.35],
    ...overrides,
  };
}
