import {
  createMockAlert,
  createMockCase,
  createMockBusinessRule,
  createMockBank,
  createMockSecurityStatus,
} from '../factories';

/**
 * Standard canonical test fixtures for end-to-end and integration suites.
 */
export const CANONICAL_FIXTURES = {
  CRITICAL_STRUCTURING_ALERT: createMockAlert({
    id: 'ALT-CRITICAL-001',
    severity: 'critical',
    risk_score: 910,
    bank_id: 'bank_a',
    reason_codes: ['STRUCTURING', 'RAPID_MOVEMENT'],
  }),

  HIGH_VELOCITY_ALERT: createMockAlert({
    id: 'ALT-HIGH-002',
    severity: 'high',
    risk_score: 720,
    bank_id: 'bank_b',
    reason_codes: ['VELOCITY_SURGE'],
  }),

  STRUCTURING_INVESTIGATION_CASE: createMockCase({
    id: 'CASE-CANONICAL-01',
    title: 'Cross-Bank Smurfing & Layering Ring',
    status: 'investigating',
    priority: 'critical',
    assigned_to: 'lead_investigator_01',
    total_risk_score: 935,
  }),

  CLOSED_CONFIRMED_CASE: createMockCase({
    id: 'CASE-CLOSED-02',
    title: 'Confirmed Crypto Mule Network',
    status: 'closed_confirmed',
    priority: 'high',
    assigned_to: 'lead_investigator_01',
    closed_at: '2026-08-14T11:00:00Z',
    is_open: false,
  }),

  AST_RULE_STRUCTURING: createMockBusinessRule('RULE-STRUCTURING-01', {
    rule_name: 'Block Structuring Near CTR Threshold',
    condition: {
      and: [
        { field: 'amount', operator: 'gte', value: 9000 },
        { field: 'amount', operator: 'lt', value: 10000 },
        { field: 'velocity_24h', operator: 'gte', value: 3 },
      ],
    },
  }),

  PRIMARY_CONSORTIUM_BANK: createMockBank('bank_a', {
    name: 'Euro-Atlantic Commercial Bank',
    tier: 'Tier 1',
    num_transactions: 250000,
    fraud_ratio: 0.011,
  }),

  STANDARD_SECURITY_STATUS: createMockSecurityStatus(),
};
