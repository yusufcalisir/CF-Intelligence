import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import {
  useBanks,
  useBankDistributions,
  useSimulations,
  useSimulation,
  useTrainingRounds,
  useAlerts,
  useAlertExplainability,
  useAlertDecisionReplay,
  useCases,
  useCaseEvidence,
  useGraphStats,
  useSecurityStatus,
  useDriftAnalysis,
  useCalibrationReport,
} from '../queries';
import { apiClient } from '../client';
import type {
  BankInfo,
  BankDistributions,
  SimulationSummary,
  SimulationDetail,
  TrainingRound,
  Alert,
  ExplainabilityReport,
  DecisionReplayReport,
  CaseSummary,
  Evidence,
  GraphStats,
  SecurityStatus,
  DriftAnalysisReport,
  CalibrationReport,
} from '../types';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
};

describe('Frontend API Contract & Schema Invariant Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // ── 1. Bank Infrastructure & Non-IID Distribution Contract ──────────────────
  describe('Bank Infrastructure & Distributions Contract', () => {
    it('useBanks satisfies BankInfo[] contract invariants', async () => {
      const mockBanks: BankInfo[] = [
        {
          id: 'bank_a',
          name: 'Global Tier-1 Commercial Bank',
          tier: 'Tier 1',
          description: 'Tier-1 cross-bank node',
          default_fraud_ratio: 0.015,
          default_transactions: 100000,
          fraud_pattern: 'Structuring / Smurfing',
          characteristics: ['High cross-border volume', 'Strict AML scoring'],
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockBanks });

      const { result } = renderHook(() => useBanks(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const bank = result.current.data?.[0];
      expect(bank).toBeDefined();
      expect(bank?.id).toBe('bank_a');
      expect(bank?.tier).toBe('Tier 1');
      expect(bank?.characteristics).toBeInstanceOf(Array);
      expect(typeof bank?.default_fraud_ratio).toBe('number');
    });

    it('useBankDistributions satisfies BankDistributions contract invariants', async () => {
      const mockDist: BankDistributions = {
        banks: {
          bank_a: {
            amount_histogram: { bins: [0, 50, 100], counts: [1000, 500], fraud_counts: [10, 5] },
            hourly_fraud_rate: { hours: [0, 1, 2], total: [100, 200, 300], fraud: [1, 2, 3] },
            merchant_risk: { categories: ['crypto'], fraud_rates: [0.05], counts: [100] },
          },
        },
        divergence_summary: {
          amount_ks_statistic: { bank_a: 0.12 },
          overall_non_iid_score: 0.78,
        },
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockDist });

      const { result } = renderHook(() => useBankDistributions(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const data = result.current.data;
      expect(data).toBeDefined();
      expect(data?.divergence_summary.overall_non_iid_score).toBe(0.78);
      expect(data?.banks.bank_a?.amount_histogram.counts).toHaveLength(2);
    });
  });

  // ── 2. Federated Learning Simulations & Rounds Contract ──────────────────────
  describe('Federated Learning Simulations Contract', () => {
    it('useSimulations satisfies SimulationSummary[] contract invariants', async () => {
      const mockSummaries: SimulationSummary[] = [
        {
          id: 'sim_fed_01',
          created_at: '2026-08-14T10:00:00Z',
          status: 'completed',
          current_round: 10,
          total_rounds: 10,
          progress_pct: 100,
          completed_at: '2026-08-14T10:10:00Z',
          duration_seconds: 600,
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockSummaries });

      const { result } = renderHook(() => useSimulations(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const sim = result.current.data?.[0];
      expect(sim).toBeDefined();
      expect(sim?.id).toBe('sim_fed_01');
      expect(sim?.status).toBe('completed');
      expect(sim?.progress_pct).toBe(100);
    });

    it('useSimulation satisfies SimulationDetail contract invariants', async () => {
      const mockDetail: SimulationDetail = {
        id: 'sim_fed_01',
        status: 'completed',
        current_round: 10,
        total_rounds: 10,
        progress_pct: 100,
        created_at: '2026-08-14T10:00:00Z',
        started_at: '2026-08-14T10:00:05Z',
        completed_at: '2026-08-14T10:10:00Z',
        duration_seconds: 600,
        error_message: null,
        config: {
          num_rounds: 10,
          local_epochs: 3,
          learning_rate: 0.001,
          batch_size: 64,
          min_clients_per_round: 2,
          enable_latency_simulation: false,
          latency_min_ms: 50,
          latency_max_ms: 500,
          enable_dropout_simulation: false,
          dropout_probability: 0.2,
          enable_reconnect_simulation: true,
          privacy_mechanism: 'differential_privacy',
          dp_epsilon: 1.0,
          dp_delta: 1e-5,
          dp_max_grad_norm: 1.0,
          bank_a_transactions: 50000,
          bank_b_transactions: 30000,
          bank_c_transactions: 20000,
          aggregation_method: 'fed_avg_weighted',
          enable_poisoning_simulation: false,
          poisoning_bank_id: 'bank_c',
          poisoning_scale: 1.0,
          fl_engine_type: 'custom',
        },
        banks: [
          {
            id: 'bank_a',
            name: 'Bank A',
            tier: 'Tier 1',
            fraud_ratio: 0.02,
            num_transactions: 50000,
            status: 'active',
            local_metrics: null,
            federated_metrics: {
              accuracy: 0.96,
              precision: 0.91,
              recall: 0.88,
              f1_score: 0.89,
              auc_roc: 0.94,
              loss: 0.12,
              confusion_matrix: [[980, 20], [12, 88]],
              roc_fpr: [0, 0.02, 1],
              roc_tpr: [0, 0.88, 1],
              roc_thresholds: [1, 0.5, 0],
              feature_importance: { amount: 0.45, velocity: 0.35 },
            },
            improvement: { auc_roc: 0.05 },
            data_profile: {
              bank_name: 'Bank A',
              num_transactions: 50000,
              fraud_ratio: 0.02,
              mean_transaction_amount: 145.2,
              std_transaction_amount: 320.5,
              top_merchant_categories: ['grocery', 'crypto'],
              top_countries: ['US', 'CA'],
              mean_account_age_days: 420,
              mean_velocity: 2.1,
            },
          },
        ],
        rounds: [],
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockDetail });

      const { result } = renderHook(() => useSimulation('sim_fed_01'), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const sim = result.current.data;
      expect(sim).toBeDefined();
      expect(sim?.id).toBe('sim_fed_01');
      expect(sim?.config.privacy_mechanism).toBe('differential_privacy');
      expect(sim?.banks).toHaveLength(1);
      expect(sim?.banks[0]?.federated_metrics?.auc_roc).toBe(0.94);
    });

    it('useTrainingRounds satisfies TrainingRound[] contract invariants', async () => {
      const mockRounds: TrainingRound[] = [
        {
          round_number: 1,
          total_rounds: 10,
          duration_ms: 1250,
          global_loss: 0.45,
          participating_banks: ['bank_a', 'bank_b', 'bank_c'],
          dropped_banks: [],
          privacy_budget: 0.15,
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockRounds });

      const { result } = renderHook(() => useTrainingRounds('sim_fed_01'), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const round = result.current.data?.[0];
      expect(round).toBeDefined();
      expect(round?.round_number).toBe(1);
      expect(round?.participating_banks).toContain('bank_a');
      expect(round?.global_loss).toBe(0.45);
    });
  });

  // ── 3. AML Alerts, Explainability & Replay Contract ──────────────────────────
  describe('AML Alerts & Explainability Contract', () => {
    it('useAlerts satisfies Alert[] contract invariants', async () => {
      const mockAlerts: Alert[] = [
        {
          id: 'ALT-9901',
          bank_id: 'bank_a',
          transaction_id: 'TXN-881122',
          risk_score: 92.5,
          severity: 'critical',
          status: 'NEW',
          reason_codes: ['HIGH_VELOCITY', 'SUSPICIOUS_MERCHANT'],
          confidence: 0.94,
          involved_entity_ids: ['ENT-001', 'ENT-002'],
          created_at: '2026-08-14T12:00:00Z',
          top_features: [{ feature: 'velocity_1h', contribution: 0.85 }],
          risk_factors: ['Cross-bank multi-account burst'],
          model_confidence: 0.95,
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockAlerts });

      const { result } = renderHook(() => useAlerts(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const alert = result.current.data?.[0];
      expect(alert).toBeDefined();
      expect(alert?.id).toBe('ALT-9901');
      expect(alert?.severity).toBe('critical');
      expect(alert?.reason_codes).toContain('HIGH_VELOCITY');
    });

    it('useAlertExplainability satisfies ExplainabilityReport contract invariants', async () => {
      const mockExp: ExplainabilityReport = {
        alert_id: 'ALT-9901',
        top_features: [{ feature: 'amount', contribution: 0.42 }],
        risk_factors: ['High velocity', 'Rapid fund dispersal'],
        historical_evidence: ['Prior SAR filing on 2025-11-12'],
        model_confidence: 0.96,
        risk_score_breakdown: [
          {
            signal_name: 'GNN Risk',
            weight: 0.4,
            raw_value: 88.0,
            normalized_score: 88.0,
            explanation: 'High graph cluster centrality',
            contribution: 35.2,
          },
        ],
        explanation_text: 'High probability of smurfing network detected.',
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockExp });

      const { result } = renderHook(() => useAlertExplainability('ALT-9901'), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const exp = result.current.data;
      expect(exp).toBeDefined();
      expect(exp?.alert_id).toBe('ALT-9901');
      expect(exp?.model_confidence).toBe(0.96);
      expect(exp?.top_features).toHaveLength(1);
    });

    it('useAlertDecisionReplay satisfies DecisionReplayReport contract invariants', async () => {
      const mockReplay: DecisionReplayReport = {
        alert_id: 'ALT-9901',
        transaction_id: 'TXN-881122',
        timestamp: '2026-08-14T12:05:00Z',
        model_version: 'fed_global_v2',
        model_auc: 0.94,
        features_snapshot: { velocity: 8.5, amount: 9500 },
        graph_snapshot: { cluster_score: 85 },
        policy_rules_evaluated: [
          {
            rule_code: 'RULE-01',
            signal_name: 'High Velocity',
            weight: 0.35,
            raw_value: 8.5,
            normalized_score: 90,
            contribution: 31.5,
            triggered: true,
          },
        ],
        reconstructed_risk_score: 92.5,
        reproduced_severity: 'critical',
        audit_matched: true,
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockReplay });

      const { result } = renderHook(() => useAlertDecisionReplay('ALT-9901'), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const replay = result.current.data;
      expect(replay).toBeDefined();
      expect(replay?.audit_matched).toBe(true);
      expect(replay?.reconstructed_risk_score).toBe(92.5);
    });
  });

  // ── 4. Case Management & Cryptographic Evidence Contract ─────────────────────
  describe('Case Management & Evidence Contract', () => {
    it('useCases satisfies CaseSummary[] contract invariants', async () => {
      const mockCases: CaseSummary[] = [
        {
          id: 'CASE-001',
          title: 'Cross-Bank Mule Structuring Syndicate',
          status: 'in_progress',
          priority: 'p1_critical',
          assigned_to: 'lead_investigator_01',
          alert_count: 5,
          created_at: '2026-08-14T08:00:00Z',
          is_open: true,
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockCases });

      const { result } = renderHook(() => useCases(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const c = result.current.data?.[0];
      expect(c).toBeDefined();
      expect(c?.id).toBe('CASE-001');
      expect(c?.priority).toBe('p1_critical');
      expect(c?.alert_count).toBe(5);
    });

    it('useCaseEvidence satisfies Evidence[] contract invariants', async () => {
      const mockEvidence: Evidence[] = [
        {
          id: 'EV-001',
          case_id: 'CASE-001',
          evidence_type: 'ledger_proof',
          title: 'Signed SWIFT Transfer MT103',
          file_path: 'evidence/CASE-001/mt103.json',
          content_hash: '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
          uploaded_by: 'investigator_01',
          uploaded_at: '2026-08-14T08:30:00Z',
        },
      ];
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockEvidence });

      const { result } = renderHook(() => useCaseEvidence('CASE-001'), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const ev = result.current.data?.[0];
      expect(ev).toBeDefined();
      expect(ev?.id).toBe('EV-001');
      expect(ev?.content_hash).toHaveLength(64); // Valid SHA-256 hex length
    });
  });

  // ── 5. Identity Graph, Entities & Security Contract ──────────────────────────
  describe('Identity Graph & Cryptographic Security Contract', () => {
    it('useGraphStats satisfies GraphStats contract invariants', async () => {
      const mockGraphStats: GraphStats = {
        total_nodes: 1450,
        total_edges: 3200,
        nodes_by_type: { customer: 900, account: 450, merchant: 100 },
        nodes_by_risk: { CRITICAL: 45, HIGH: 120, MEDIUM: 300, LOW: 985 },
        cluster_count: 38,
        database_backend: 'Neo4j / NetworkX InMemory',
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockGraphStats });

      const { result } = renderHook(() => useGraphStats(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const stats = result.current.data;
      expect(stats).toBeDefined();
      expect(stats?.total_nodes).toBe(1450);
      expect(stats?.cluster_count).toBe(38);
    });

    it('useSecurityStatus satisfies SecurityStatus contract invariants', async () => {
      const mockSec: SecurityStatus = {
        mtls: {
          enabled: true,
          ca_cn: 'CFI Root CA',
          tls_version: '1.3',
          peer_verification: 'STRICT',
          sample_cert: { cn: 'gateway.internal', sans: ['localhost'], valid_until: '2027-08-14' },
        },
        oidc: {
          enabled: true,
          issuer: 'https://auth.cfi.internal',
          client_id: 'cfi-app',
          supported_algorithms: ['RS256'],
          claims_extracted: ['sub', 'roles'],
        },
        abac: {
          enabled: true,
          active_rules_count: 6,
          enforced_policies: ['RULE-TENANT-ISOLATION'],
        },
        vault: {
          enabled: true,
          vault_url: 'https://vault.internal:8200',
          mount_point: 'secret',
          sample_secret_source: 'HashiCorp Vault HSM',
        },
        audit_chain: {
          enabled: true,
          total_events: 154,
          chain_valid: true,
          last_hash: '38ad042b2cd006c84be6348db5ed4b6a81f6f3d3c5ba5431caf1897698190fdb',
          hashing_algorithm: 'SHA-256 Chain',
        },
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockSec });

      const { result } = renderHook(() => useSecurityStatus(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const sec = result.current.data;
      expect(sec).toBeDefined();
      expect(sec?.mtls.enabled).toBe(true);
      expect(sec?.abac.enabled).toBe(true);
      expect(sec?.audit_chain.chain_valid).toBe(true);
    });
  });

  // ── 6. Real-Time Drift & Calibration Monitoring Contract ─────────────────────
  describe('Drift & Calibration Monitoring Contract', () => {
    it('useDriftAnalysis satisfies DriftAnalysisReport contract invariants', async () => {
      const mockDrift: DriftAnalysisReport = {
        overall_status: 'HEALTHY',
        max_psi: 0.08,
        mean_ks_p_value: 0.45,
        concept_drift_psi: 0.05,
        auto_retrain_triggered: false,
        evaluated_at: '2026-08-14T12:00:00Z',
        feature_drifts: [
          {
            feature_name: 'transaction_amount',
            ks_statistic: 0.04,
            ks_p_value: 0.52,
            wasserstein_distance: 0.12,
            psi: 0.03,
            status: 'NO_DRIFT',
          },
        ],
        calibration: {
          brier_score: 0.045,
          expected_calibration_error: 0.032,
          max_calibration_error: 0.055,
          is_well_calibrated: true,
          evaluated_at: '2026-08-14T12:00:00Z',
          bins: [],
        },
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockDrift });

      const { result } = renderHook(() => useDriftAnalysis(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const drift = result.current.data;
      expect(drift).toBeDefined();
      expect(drift?.overall_status).toBe('HEALTHY');
      expect(drift?.feature_drifts[0]?.status).toBe('NO_DRIFT');
    });

    it('useCalibrationReport satisfies CalibrationReport contract invariants', async () => {
      const mockCalib: CalibrationReport = {
        brier_score: 0.042,
        expected_calibration_error: 0.028,
        max_calibration_error: 0.049,
        is_well_calibrated: true,
        evaluated_at: '2026-08-14T12:00:00Z',
        bins: [
          {
            bin_index: 1,
            prob_min: 0.0,
            prob_max: 0.1,
            mean_predicted_prob: 0.05,
            empirical_fraud_ratio: 0.048,
            sample_count: 500,
          },
        ],
      };
      vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: mockCalib });

      const { result } = renderHook(() => useCalibrationReport(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      const calib = result.current.data;
      expect(calib).toBeDefined();
      expect(calib?.is_well_calibrated).toBe(true);
      expect(calib?.bins).toHaveLength(1);
    });
  });
});
