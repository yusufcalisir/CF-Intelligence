import type { BankResult, BankDistributions } from '../../api/types';

/**
 * Creates a mock BankResult record.
 */
export function createMockBank(id: string = 'bank_a', overrides: Partial<BankResult> = {}): BankResult {
  const names: Record<string, string> = {
    bank_a: 'Global Retail Bank A',
    bank_b: 'Continental Merchant Bank B',
    bank_c: 'Apex Investment Bank C',
  };

  return {
    id,
    name: overrides.name || names[id] || `Consortium Institution (${id.toUpperCase()})`,
    tier: overrides.tier || (id === 'bank_a' ? 'Tier 1' : 'Tier 2'),
    num_transactions: overrides.num_transactions ?? 150000,
    fraud_ratio: overrides.fraud_ratio ?? 0.012,
    status: overrides.status || 'online',
    local_metrics: null,
    federated_metrics: null,
    improvement: null,
    data_profile: null,
    ...overrides,
  };
}

/**
 * Creates mock BankDistributions.
 */
export function createMockBankDistributions(overrides: Partial<BankDistributions> = {}): BankDistributions {
  return {
    banks: {
      bank_a: {
        amount_histogram: { bins: [0, 500, 2000, 10000], counts: [5000, 2000, 500], fraud_counts: [50, 40, 20] },
        hourly_fraud_rate: { hours: [0, 6, 12, 18], total: [1000, 1200, 2500, 1800], fraud: [12, 8, 30, 25] },
        merchant_risk: {
          categories: ['crypto', 'wire_transfer', 'ecommerce', 'gaming'],
          fraud_rates: [0.082, 0.045, 0.012, 0.028],
          counts: [1200, 3500, 12000, 4500],
        },
      },
    },
    divergence_summary: {
      amount_ks_statistic: { 'bank_a-bank_b': 0.04 },
      overall_non_iid_score: 0.12,
    },
    ...overrides,
  };
}
