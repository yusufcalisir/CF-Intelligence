import { describe, it, expect } from 'vitest';
import {
  createMockAlert,
  createMockAlertList,
  createMockAlertExplainability,
  createMockDecisionReplay,
  createMockGNNAttribution,
  createMockCase,
  createMockCaseList,
  createMockEvidence,
  createMockBank,
  createMockBankDistributions,
  createMockSimulationSummary,
  createMockSimulationDetail,
  createMockTrainingRounds,
  createMockSecurityStatus,
  createMockAbacEvaluation,
  createMockAuditChainVerification,
  createMockBusinessRule,
  createMockDriftAnalysis,
  createMockCalibrationReport,
} from '../factories';
import { CANONICAL_FIXTURES } from '../fixtures/canonicalFixtures';

describe('Centralized Frontend Test Data Factory Suite', () => {
  describe('Alert Factories', () => {
    it('creates alert with default values and allows overriding fields', () => {
      const alert = createMockAlert({ bank_id: 'bank_c', severity: 'medium' });
      expect(alert.bank_id).toBe('bank_c');
      expect(alert.severity).toBe('medium');
      expect(alert.risk_score).toBe(350);
      expect(alert.reason_codes.length).toBeGreaterThan(0);
      expect(alert.top_features).toHaveLength(3);
    });

    it('generates a batch of distinct mock alerts', () => {
      const alerts = createMockAlertList(5);
      expect(alerts).toHaveLength(5);
      expect(alerts[0]?.id).toBe('ALT-BATCH-001');
      expect(alerts[4]?.id).toBe('ALT-BATCH-005');
    });

    it('creates alert explainability, replay, and GNN attribution structures', () => {
      const explain = createMockAlertExplainability('ALT-001');
      expect(explain.alert_id).toBe('ALT-001');
      expect(explain.shap_values.length).toBe(4);

      const replay = createMockDecisionReplay('ALT-001');
      expect(replay.model_version).toContain('champion');
      expect(replay.policy_action).toBe('BLOCK_AND_FLAG');

      const gnn = createMockGNNAttribution('ALT-001');
      expect(gnn.subgraph_nodes.length).toBe(3);
      expect(gnn.subgraph_edges.length).toBe(2);
    });
  });

  describe('Case & Evidence Factories', () => {
    it('creates case with active status and calculates is_open dynamically', () => {
      const openCase = createMockCase({ status: 'investigating' });
      expect(openCase.is_open).toBe(true);
      expect(openCase.closed_at).toBeNull();

      const closedCase = createMockCase({ status: 'closed_confirmed' });
      expect(closedCase.is_open).toBe(false);
      expect(closedCase.closed_at).not.toBeNull();
    });

    it('creates case summary list for pagination and feed testing', () => {
      const summaries = createMockCaseList(4);
      expect(summaries).toHaveLength(4);
      expect(summaries[0]?.priority).toBe('critical');
    });

    it('creates cryptographic evidence ledger items with 64-char hash', () => {
      const evidence = createMockEvidence('CASE-001');
      expect(evidence.case_id).toBe('CASE-001');
      expect(evidence.content_hash).toHaveLength(64);
      expect(evidence.evidence_type).toBe('ledger_proof');
    });
  });

  describe('Bank, Simulation & Security Factories', () => {
    it('creates bank distributions and consortium nodes', () => {
      const bank = createMockBank('bank_b');
      expect(bank.id).toBe('bank_b');
      expect(bank.tier).toBe('Tier 2');

      const dist = createMockBankDistributions();
      expect(dist.banks.bank_a?.merchant_risk.categories).toHaveLength(4);
    });

    it('creates simulation summaries and detailed round metrics', () => {
      const summary = createMockSimulationSummary({ status: 'running', current_round: 3 });
      expect(summary.status).toBe('running');
      expect(summary.current_round).toBe(3);

      const detail = createMockSimulationDetail();
      expect(detail.config.aggregation_method).toBe('krum');

      const rounds = createMockTrainingRounds(6);
      expect(rounds).toHaveLength(6);
      expect(rounds[5]?.round_number).toBe(6);
    });

    it('creates security posture, ABAC evaluation, and rules with AST condition', () => {
      const sec = createMockSecurityStatus();
      expect(sec.mtls.enabled).toBe(true);
      expect(sec.abac.enabled).toBe(true);

      const abac = createMockAbacEvaluation();
      expect(abac.allowed).toBe(true);

      const audit = createMockAuditChainVerification();
      expect(audit.is_valid).toBe(true);

      const rule = createMockBusinessRule('RULE-101');
      expect(rule.condition.and).toHaveLength(2);
    });

    it('creates drift and calibration metrics', () => {
      const drift = createMockDriftAnalysis({ max_psi: 0.18 });
      expect(drift.max_psi).toBe(0.18);

      const calib = createMockCalibrationReport();
      expect(calib.expected_calibration_error).toBe(0.018);
      expect(calib.bins.length).toBe(2);
    });
  });

  describe('Canonical Fixtures Registry', () => {
    it('verifies standard canonical fixtures integrity', () => {
      expect(CANONICAL_FIXTURES.CRITICAL_STRUCTURING_ALERT.severity).toBe('critical');
      expect(CANONICAL_FIXTURES.STRUCTURING_INVESTIGATION_CASE.priority).toBe('critical');
      expect(CANONICAL_FIXTURES.CLOSED_CONFIRMED_CASE.is_open).toBe(false);
      expect(CANONICAL_FIXTURES.PRIMARY_CONSORTIUM_BANK.id).toBe('bank_a');
      expect(CANONICAL_FIXTURES.AST_RULE_STRUCTURING.condition.and).toHaveLength(3);
    });
  });
});
