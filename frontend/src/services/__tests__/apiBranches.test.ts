import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  checkSystemHealth,
  predictTransaction,
  fetchCounterfactual,
  runFLSimulation,
} from '../api';
import { FLSimulationRequest, PredictPayload } from '../../types';

describe('API Service Comprehensive Branch Coverage Suite', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  describe('checkSystemHealth branches', () => {
    it('returns JSON status when health response is ok', async () => {
      const mockHealth = {
        status: 'healthy',
        service: 'cfi-backend',
        environment: 'production',
        redis_connected: true,
        version: '1.4.2',
      };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockHealth,
      });

      const res = await checkSystemHealth();
      expect(res.status).toBe('healthy');
      expect(res.redis_connected).toBe(true);
    });

    it('returns fallback offline status when response is not ok', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
      });

      const res = await checkSystemHealth();
      expect(res.status).toBe('offline');
      expect(res.redis_connected).toBe(false);
    });

    it('returns fallback offline status when fetch throws network exception', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network disconnected'));

      const res = await checkSystemHealth();
      expect(res.status).toBe('offline');
      expect(res.redis_connected).toBe(false);
    });
  });

  describe('predictTransaction branches', () => {
    it('returns prediction payload on 200 OK', async () => {
      const mockPredictResponse = {
        fraud_probability: 0.82,
        risk_score: 820.5,
        is_fraud_suspected: true,
        risk_level: 'HIGH',
        breakdown: { velocity: 0.8 },
        alert_details: null,
        policy_action: 'ALERT',
        triggered_rules: ['VELOCITY_SPIKE'],
      };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockPredictResponse,
      });

      const payload: PredictPayload = {
        transaction_amount: 15000,
        merchant_category: 'crypto_exchange',
        country_code: 'US',
        device_type: 'mobile',
        velocity: 25,
        hour_of_day: 14,
        merchant_risk_score: 0.85,
        customer_history_score: 0.7,
        chargeback_count: 2,
        account_age_days: 120,
        bank_id: 'bank_a',
      };

      const res = await predictTransaction(payload);
      expect(res.risk_score).toBe(820.5);
      expect(res.policy_action).toBe('ALERT');
    });

    it('throws descriptive error on non-ok HTTP response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        statusText: 'Bad Request',
      });

      const payload: PredictPayload = {
        transaction_amount: -50,
        merchant_category: 'invalid',
        country_code: 'XX',
        device_type: 'unknown',
        velocity: 0,
        hour_of_day: 0,
        merchant_risk_score: 0,
        customer_history_score: 0,
        chargeback_count: 0,
        account_age_days: 0,
        bank_id: 'bank_a',
      };

      await expect(predictTransaction(payload)).rejects.toThrow('Prediction API error: Bad Request');
    });
  });

  describe('fetchCounterfactual branches', () => {
    it('returns counterfactual report when API succeeds with 200 OK', async () => {
      const mockReport = {
        alert_id: 'ALT-101',
        original_score: 910.0,
        remediated_score: 250.0,
        is_cleared: true,
        changes: [],
        summary_text: 'REMEDIATED',
      };
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockReport,
      });

      const res = await fetchCounterfactual('ALT-101', 300.0);
      expect(res.alert_id).toBe('ALT-101');
      expect(res.remediated_score).toBe(250.0);
    });

    it('returns fallback remediation plan when API response is not ok', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      });

      const res = await fetchCounterfactual('ALT-404', 350.0);
      expect(res.alert_id).toBe('ALT-404');
      expect(res.is_cleared).toBe(true);
      expect(res.changes.length).toBe(3);
    });

    it('returns fallback remediation plan when fetch throws network error', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Connection timeout'));

      const res = await fetchCounterfactual('ALT-ERR');
      expect(res.alert_id).toBe('ALT-ERR');
      expect(res.original_score).toBe(780.0);
    });
  });

  describe('runFLSimulation branches', () => {
    it('returns rounds from API when response is ok with data.rounds', async () => {
      const mockRounds = [
        {
          round_number: 1,
          global_loss: 0.42,
          per_bank_loss: { bank_a: 0.4 },
          participating_bank_ids: ['bank_a'],
          dropped_bank_ids: [],
          aggregation_time_ms: 22,
        },
      ];
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ rounds: mockRounds }),
      });

      const req: FLSimulationRequest = {
        num_rounds: 1,
        local_epochs: 2,
        learning_rate: 0.01,
        algorithm: 'fed_avg',
        dp_epsilon: 4.0,
        dp_delta: 1e-5,
      };

      const rounds = await runFLSimulation(req);
      expect(rounds.length).toBe(1);
      expect(rounds[0]?.global_loss).toBe(0.42);
    });

    it('returns empty array when API response ok is missing rounds key', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({}),
      });

      const req: FLSimulationRequest = {
        num_rounds: 1,
        local_epochs: 2,
        learning_rate: 0.01,
        algorithm: 'krum',
        dp_epsilon: 0,
        dp_delta: 0,
      };

      const rounds = await runFLSimulation(req);
      expect(rounds).toEqual([]);
    });

    it('generates synthetic fallback rounds on API failure or offline mode', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Backend offline'));

      const req: FLSimulationRequest = {
        num_rounds: 3,
        local_epochs: 2,
        learning_rate: 0.01,
        algorithm: 'fed_avg_weighted',
        dp_epsilon: 4.0,
        dp_delta: 1e-5,
      };

      const rounds = await runFLSimulation(req);
      expect(rounds.length).toBe(3);
      expect(rounds[0]?.round_number).toBe(1);
      expect(rounds[2]?.round_number).toBe(3);
      expect(rounds[0]?.participating_bank_ids).toContain('bank_a');
      expect(rounds[0]?.global_loss).toBeGreaterThanOrEqual(0.04);
    });
  });
});
