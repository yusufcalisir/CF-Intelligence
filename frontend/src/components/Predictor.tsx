import React, { useState } from 'react';
import { Activity, Send } from 'lucide-react';
import { PredictPayload, PredictResponse } from '../types';
import { predictTransaction } from '../services/api';

export const Predictor: React.FC = () => {
  const [formData, setFormData] = useState<PredictPayload>({
    transaction_amount: 15000.0,
    merchant_category: 'crypto',
    country_code: 'NG',
    device_type: 'mobile_app',
    velocity: 28.0,
    hour_of_day: 3,
    merchant_risk_score: 0.95,
    customer_history_score: 0.10,
    chargeback_count: 5,
    account_age_days: 12,
    bank_id: 'bank_a',
  });

  const [result, setResult] = useState<PredictResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await predictTransaction(formData);
      setResult(res);
    } catch {
      // Standalone simulation response fallback
      const mockScore = formData.transaction_amount > 10000 ? 880.0 : 210.0;
      setResult({
        fraud_probability: mockScore / 1000,
        risk_score: mockScore,
        is_fraud_suspected: mockScore >= 600,
        risk_level: mockScore >= 600 ? 'CRITICAL' : 'LOW',
        breakdown: { base_model: 0.85, gnn_embedding: 0.92 },
        alert_details: {
          alert_id: 'ALT-' + Math.random().toString(36).substring(7).toUpperCase(),
          severity: mockScore >= 600 ? 'CRITICAL' : 'LOW',
          reason_codes: ['HIGH-AMOUNT', 'HIGH-RISK-JURISDICTION', 'VELOCITY-SPIKE'],
          explanation: 'Transaction flagged due to anomalous velocity and high-risk jurisdiction.',
          top_features: [
            { feature: 'country_code', contribution: 0.42 },
            { feature: 'transaction_amount', contribution: 0.35 },
            { feature: 'velocity', contribution: 0.23 },
          ],
        },
        policy_action: mockScore >= 600 ? 'BLOCK_AND_FLAG' : 'ALLOW',
        triggered_rules: ['RULE-001-HIGH-RISK-COUNTRY', 'RULE-004-VELOCITY-EXCEEDED'],
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="glass-card p-4 rounded-xl flex items-center justify-between gap-4 border border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <Activity className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-sm text-slate-100">Real-Time Risk Scoring Engine</h2>
            <p className="text-xs text-slate-400">Evaluate Single ISO 20022 Financial Transaction Payload</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Form Controls */}
        <form onSubmit={handleSubmit} className="lg:col-span-7 glass-card rounded-xl p-5 border border-slate-800 space-y-4">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Transaction Attributes
          </h3>

          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <label className="text-slate-300 font-medium">Transaction Amount ($)</label>
              <input
                type="number"
                value={formData.transaction_amount}
                onChange={(e) => setFormData({ ...formData, transaction_amount: Number(e.target.value) })}
                className="w-full mt-1 bg-slate-900 border border-slate-700/80 rounded-lg p-2 text-slate-200 outline-none focus:border-cyan-400 font-mono"
              />
            </div>

            <div>
              <label className="text-slate-300 font-medium">Merchant Category</label>
              <select
                value={formData.merchant_category}
                onChange={(e) => setFormData({ ...formData, merchant_category: e.target.value })}
                className="w-full mt-1 bg-slate-900 border border-slate-700/80 rounded-lg p-2 text-slate-200 outline-none focus:border-cyan-400"
              >
                <option value="crypto">Crypto Exchange</option>
                <option value="wire_transfer">Wire Transfer</option>
                <option value="gambling">Online Gambling</option>
                <option value="retail">Retail Grocery</option>
              </select>
            </div>

            <div>
              <label className="text-slate-300 font-medium">Country Jurisdiction</label>
              <select
                value={formData.country_code}
                onChange={(e) => setFormData({ ...formData, country_code: e.target.value })}
                className="w-full mt-1 bg-slate-900 border border-slate-700/80 rounded-lg p-2 text-slate-200 outline-none focus:border-cyan-400"
              >
                <option value="NG">Nigeria (NG - High Risk)</option>
                <option value="RU">Russia (RU - High Risk)</option>
                <option value="US">United States (US)</option>
                <option value="DE">Germany (DE)</option>
              </select>
            </div>

            <div>
              <label className="text-slate-300 font-medium">Hourly Velocity</label>
              <input
                type="number"
                value={formData.velocity}
                onChange={(e) => setFormData({ ...formData, velocity: Number(e.target.value) })}
                className="w-full mt-1 bg-slate-900 border border-slate-700/80 rounded-lg p-2 text-slate-200 outline-none focus:border-cyan-400 font-mono"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded-lg bg-gradient-to-r from-cyan-500 to-purple-600 font-medium text-xs text-white shadow-lg shadow-cyan-500/20 hover:opacity-90 transition-all flex items-center justify-center gap-2"
          >
            <Send className="h-3.5 w-3.5" />
            <span>{loading ? 'Evaluating Payload...' : 'Evaluate Transaction Risk'}</span>
          </button>
        </form>

        {/* Prediction Results Inspector */}
        <div className="lg:col-span-5 glass-card rounded-xl p-5 border border-slate-800 space-y-4">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Evaluation Result
          </h3>

          {result ? (
            <div className="space-y-4 text-xs">
              <div className="flex items-center justify-between bg-slate-900/80 p-3.5 rounded-lg border border-slate-800">
                <div>
                  <span className="text-slate-400">Risk Score</span>
                  <p className={`font-mono text-2xl font-bold ${result.is_fraud_suspected ? 'text-rose-500' : 'text-emerald-400'}`}>
                    {result.risk_score.toFixed(1)} / 1000
                  </p>
                </div>
                <span className={`px-3 py-1 rounded-full font-bold uppercase text-[11px] ${
                  result.is_fraud_suspected ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                }`}>
                  {result.policy_action}
                </span>
              </div>

              {result.alert_details && (
                <div className="bg-slate-900/80 p-3 rounded-lg border border-slate-800 space-y-2">
                  <span className="text-slate-400 font-medium">SHAP Feature Contributions</span>
                  {(result.alert_details.top_features || []).map((f, i) => (
                    <div key={i} className="flex justify-between items-center text-[11px]">
                      <span className="text-slate-300 font-mono">{f.feature}</span>
                      <span className="text-cyan-400 font-mono">+{(f.contribution * 100).toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-12 text-slate-500 text-xs">
              Submit transaction payload to view GNN risk score and SHAP feature attributions.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
