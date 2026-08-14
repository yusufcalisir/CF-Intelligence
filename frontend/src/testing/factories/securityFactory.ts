import type { SecurityStatus, ABACEvalResponse, AuditChainVerifyResponse, BusinessRule } from '../../api/types';

/**
 * Creates a mock SecurityStatus response.
 */
export function createMockSecurityStatus(overrides: Partial<SecurityStatus> = {}): SecurityStatus {
  return {
    mtls: {
      enabled: true,
      ca_cn: 'Consortium Root CA',
      tls_version: 'TLSv1.3',
      peer_verification: 'STRICT',
      sample_cert: { cn: 'bank_a.consortium.local', sans: ['bank_a.local'], valid_until: '2027-01-01T00:00:00Z' },
    },
    oidc: {
      enabled: true,
      issuer: 'https://vault.consortium.local/v1/identity/oidc',
      client_id: 'cfi-portal',
      supported_algorithms: ['RS256', 'ES256'],
      claims_extracted: ['sub', 'roles', 'clearance'],
    },
    abac: {
      enabled: true,
      active_rules_count: 18,
      enforced_policies: ['POL-CASE-ACCESS', 'POL-TIME-WINDOW'],
    },
    vault: {
      enabled: true,
      vault_url: 'https://vault.consortium.local',
      mount_point: 'transit',
      sample_secret_source: 'transit/keys/aes256',
    },
    audit_chain: {
      enabled: true,
      total_events: 1250,
      chain_valid: true,
      last_hash: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
      hashing_algorithm: 'SHA-256',
    },
    ...overrides,
  };
}

/**
 * Creates a mock ABACEvalResponse.
 */
export function createMockAbacEvaluation(overrides: Partial<ABACEvalResponse> = {}): ABACEvalResponse {
  return {
    allowed: true,
    policy_name: 'POL-CASE-ACCESS',
    reason: 'Subject satisfies clearance level.',
    evaluated_at: '2026-08-14T10:00:00Z',
    ...overrides,
  };
}

/**
 * Creates a mock AuditChainVerifyResponse.
 */
export function createMockAuditChainVerification(overrides: Partial<AuditChainVerifyResponse> = {}): AuditChainVerifyResponse {
  return {
    is_valid: true,
    total_records: 1250,
    broken_index: null,
    tamper_reason: null,
    genesis_hash: '0000000000000000000000000000000000000000000000000000000000000000',
    last_hash: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
    verified_at: '2026-08-14T10:00:00Z',
    ...overrides,
  };
}

/**
 * Creates a mock Business Rule with AST condition.
 */
export function createMockBusinessRule(id: string = 'RULE-001-STRUCTURING', overrides: Partial<BusinessRule> = {}): BusinessRule {
  return {
    id,
    rule_name: overrides.rule_name || 'High Risk Structuring & Rapid Velocity Rule',
    is_active: overrides.is_active ?? true,
    action: overrides.action || 'flag_for_review',
    created_at: '2026-08-14T08:00:00Z',
    updated_at: undefined,
    condition: overrides.condition || {
      and: [
        { field: 'amount', operator: 'gte', value: 8000 },
        { field: 'velocity', operator: 'gt', value: 10 },
      ],
    },
    ...overrides,
  };
}
