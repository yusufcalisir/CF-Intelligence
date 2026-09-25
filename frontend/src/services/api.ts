import {
  CounterfactualReport,
  CounterfactualSimulationParams,
  FLRoundResult,
  FLSimulationRequest,
  PredictPayload,
  PredictResponse,
  SystemHealthStatus,
} from '../types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export async function checkSystemHealth(): Promise<SystemHealthStatus> {
  try {
    const res = await fetch('/health');
    if (!res.ok) throw new Error('Health check failed');
    return await res.json();
  } catch {
    return {
      status: 'offline',
      service: 'cfi-backend',
      environment: 'development',
      redis_connected: false,
      version: '1.4.2',
    };
  }
}

export async function predictTransaction(payload: PredictPayload): Promise<PredictResponse> {
  const res = await fetch(`${BASE_URL}/predict`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`Prediction API error: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchCounterfactual(
  alertIdOrParams: string | CounterfactualSimulationParams = 'alt_1001',
  targetScore: number = 350.0
): Promise<CounterfactualReport> {
  const query = new URLSearchParams();

  if (typeof alertIdOrParams === 'object') {
    query.set('alert_id', alertIdOrParams.alert_id || 'alt_1001');
    query.set('target_score', String(alertIdOrParams.target_score ?? targetScore));
    if (alertIdOrParams.amount !== undefined) query.set('amount', String(alertIdOrParams.amount));
    if (alertIdOrParams.velocity !== undefined) query.set('velocity', String(alertIdOrParams.velocity));
    if (alertIdOrParams.merchant_risk !== undefined) query.set('merchant_risk', String(alertIdOrParams.merchant_risk));
  } else {
    query.set('alert_id', alertIdOrParams || 'alt_1001');
    query.set('target_score', String(targetScore));
  }

  const res = await fetch(`${BASE_URL}/explainability/counterfactuals?${query.toString()}`);
  if (!res.ok) {
    throw new Error(`Counterfactual API error (${res.status}): ${res.statusText}`);
  }

  const data = await res.json();
  return {
    alert_id: data.alert_id,
    original_score: data.original_score,
    remediated_score: data.remediated_score,
    is_cleared: data.is_cleared,
    changes: (data.changes || []).map((c: any) => ({
      feature: c.feature,
      original_value: c.original_value,
      suggested_value: c.suggested_value ?? c.remediated_value,
      delta: c.delta ?? 0,
      description: c.description ?? c.delta_explanation ?? '',
    })),
    summary_text: data.summary_text,
  };
}

export async function runFLSimulation(
  simReq: FLSimulationRequest
): Promise<FLRoundResult[]> {
  const methodMap: Record<string, string> = {
    FED_AVG: 'fed_avg',
    FED_ADAM: 'fed_avg_weighted',
    KRUM: 'krum',
    BULYAN: 'bulyan',
    SCAFFOLD: 'fed_avg',
    FED_ASYNC: 'fed_avg',
  };
  const aggregation_method = methodMap[simReq.algorithm] || simReq.algorithm || 'fed_avg_weighted';

  const res = await fetch(`${BASE_URL}/simulations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      num_rounds: simReq.num_rounds,
      local_epochs: simReq.local_epochs || 2,
      learning_rate: simReq.learning_rate || 0.01,
      privacy_mechanism: simReq.dp_epsilon ? 'differential_privacy' : 'none',
      dp_epsilon: simReq.dp_epsilon || 1.0,
      dp_delta: simReq.dp_delta || 1e-5,
      aggregation_method,
    }),
  });

  if (!res.ok) {
    throw new Error(`Federated learning simulation request failed: ${res.status} ${res.statusText}`);
  }

  const data = await res.json();
  if (Array.isArray(data.rounds)) {
    return data.rounds;
  }

  const simulationId = data.id;
  if (!simulationId) {
    return [];
  }

  // Poll simulation until rounds are populated or completed
  const maxAttempts = 30;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 800));
    const statusRes = await fetch(`${BASE_URL}/simulations/${simulationId}`);
    if (statusRes.ok) {
      const statusData = await statusRes.json();
      if (Array.isArray(statusData.rounds) && statusData.rounds.length > 0) {
        if (statusData.status === 'completed' || statusData.rounds.length >= simReq.num_rounds) {
          return statusData.rounds.map((r: any) => ({
            round_number: r.round_number,
            global_loss: r.global_loss,
            per_bank_loss: r.per_bank_loss || {
              bank_a: r.global_loss,
              bank_b: r.global_loss,
              bank_c: r.global_loss,
            },
            participating_bank_ids: r.participating_banks || ['bank_a', 'bank_b', 'bank_c'],
            dropped_bank_ids: r.dropped_banks || [],
            aggregation_time_ms: r.duration_ms || 25,
          }));
        }
      }
      if (statusData.status === 'failed') {
        throw new Error(statusData.error_message || 'Federated learning simulation execution failed');
      }
    }
  }

  throw new Error('Simulation timed out waiting for round completion');
}

