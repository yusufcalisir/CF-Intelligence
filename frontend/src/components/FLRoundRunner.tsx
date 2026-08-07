import React, { useState } from 'react';
import { Cpu, Play, Terminal, CheckCircle } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend } from 'recharts';
import { FLRoundResult } from '../types';
import { runFLSimulation } from '../services/api';

export const FLRoundRunner: React.FC = () => {
  const [algorithm, setAlgorithm] = useState('FED_ADAM');
  const [numRounds, setNumRounds] = useState(10);
  const [epsilon, setEpsilon] = useState(2.0);
  const [isTraining, setIsTraining] = useState(false);
  const [results, setResults] = useState<FLRoundResult[]>([]);
  const [currentRound, setCurrentRound] = useState(0);

  const handleStartFLSimulation = async () => {
    setIsTraining(true);
    setResults([]);
    setCurrentRound(0);

    const roundsData = await runFLSimulation({
      num_rounds: numRounds,
      local_epochs: 2,
      learning_rate: 0.01,
      algorithm,
      dp_epsilon: epsilon,
      dp_delta: 1e-5,
    });

    for (let i = 0; i < roundsData.length; i++) {
      await new Promise((r) => setTimeout(r, 400));
      const currentItem = roundsData[i];
      if (currentItem) {
        setResults((prev) => [...prev, currentItem]);
      }
      setCurrentRound(i + 1);
    }

    setIsTraining(false);
  };

  const chartData = results.map((r) => ({
    round: `R${r.round_number}`,
    GlobalLoss: r.global_loss,
    BankA: r.per_bank_loss.bank_a || r.global_loss,
    BankB: r.per_bank_loss.bank_b || r.global_loss,
    BankC: r.per_bank_loss.bank_c || r.global_loss,
  }));

  return (
    <div className="space-y-6">
      {/* Control Banner */}
      <div className="glass-card p-4 rounded-xl flex items-center justify-between gap-4 border border-slate-800">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Cpu className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-sm text-slate-100">Live Federated Learning Orchestrator</h2>
            <p className="text-xs text-slate-400">Ray Parallel Simulation & Multi-Bank Parameter Aggregation</p>
          </div>
        </div>

        <button
          onClick={handleStartFLSimulation}
          disabled={isTraining}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-xs text-white transition-all ${
            isTraining
              ? 'bg-slate-800 text-slate-500 cursor-not-allowed'
              : 'bg-gradient-to-r from-cyan-500 to-purple-600 shadow-lg shadow-cyan-500/20 hover:opacity-90'
          }`}
        >
          <Play className="h-4 w-4 fill-white" />
          <span>{isTraining ? `Training Round ${currentRound}/${numRounds}...` : 'Start FL Training Simulation'}</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* FL Configuration Sidebar */}
        <div className="lg:col-span-4 glass-card rounded-xl p-5 border border-slate-800 space-y-5">
          <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2">
            Simulation Parameters
          </h3>

          {/* Algorithm Selector */}
          <div className="space-y-1.5 text-xs">
            <label className="text-slate-300 font-medium">Aggregation Strategy</label>
            <select
              value={algorithm}
              onChange={(e) => setAlgorithm(e.target.value)}
              className="w-full bg-slate-900 border border-slate-700/80 rounded-lg p-2 text-slate-200 outline-none focus:border-cyan-400"
            >
              <option value="FED_AVG">FedAvg (McMahan et al.)</option>
              <option value="FED_ADAM">FedAdam (Reddi et al. Server Momentum)</option>
              <option value="KRUM">Krum (Byzantine Robust)</option>
              <option value="BULYAN">Bulyan (Collusion Resistant)</option>
              <option value="SCAFFOLD">SCAFFOLD (Drift Reduction)</option>
              <option value="FED_ASYNC">FedAsync (Staleness Attenuation)</option>
            </select>
          </div>

          {/* Number of Rounds */}
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <label className="text-slate-300 font-medium">Training Rounds</label>
              <span className="font-mono text-cyan-400 font-bold">{numRounds} Rounds</span>
            </div>
            <input
              type="range"
              min="3"
              max="20"
              value={numRounds}
              onChange={(e) => setNumRounds(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          {/* DP Epsilon Budget */}
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <label className="text-slate-300 font-medium">DP Privacy Budget (&epsilon;)</label>
              <span className="font-mono text-cyan-400 font-bold">&epsilon; = {epsilon.toFixed(1)}</span>
            </div>
            <input
              type="range"
              min="0.5"
              max="10.0"
              step="0.5"
              value={epsilon}
              onChange={(e) => setEpsilon(Number(e.target.value))}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>

          <div className="bg-slate-900/80 p-3 rounded-lg border border-slate-800 text-xs space-y-2">
            <div className="flex justify-between text-slate-400">
              <span>Participating Nodes:</span>
              <span className="text-slate-200 font-mono">3 Banks (A, B, C)</span>
            </div>
            <div className="flex justify-between text-slate-400">
              <span>Security Protocol:</span>
              <span className="text-cyan-400 font-mono font-medium">SecAgg Pairwise Masks</span>
            </div>
          </div>
        </div>

        {/* Live Convergence Chart & Terminal Feed */}
        <div className="lg:col-span-8 space-y-6">
          {/* Real-Time Convergence Chart */}
          <div className="glass-card rounded-xl p-5 border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-xs text-slate-200 uppercase tracking-wider">
                Cross-Bank Loss Convergence Curves
              </h3>
              {results.length > 0 && (
                <span className="text-xs font-mono text-emerald-400 flex items-center gap-1">
                  <CheckCircle className="h-3.5 w-3.5" /> Global Loss: {results[results.length - 1]?.global_loss}
                </span>
              )}
            </div>

            <div className="h-64 w-full">
              {chartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                    <XAxis dataKey="round" stroke="#64748B" fontSize={11} />
                    <YAxis stroke="#64748B" fontSize={11} domain={[0, 0.7]} />
                    <Tooltip contentStyle={{ backgroundColor: '#0F172A', borderColor: '#334155', borderRadius: '8px', fontSize: '11px' }} />
                    <Legend wrapperStyle={{ fontSize: '11px' }} />
                    <Line type="monotone" dataKey="GlobalLoss" stroke="#00F2FE" strokeWidth={3} dot={{ r: 4 }} />
                    <Line type="monotone" dataKey="BankA" stroke="#3B82F6" strokeWidth={1.5} strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="BankB" stroke="#A855F7" strokeWidth={1.5} strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="BankC" stroke="#EC4899" strokeWidth={1.5} strokeDasharray="4 4" />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-500 text-xs">
                  Click &quot;Start FL Training Simulation&quot; to render live loss convergence curves.
                </div>
              )}
            </div>
          </div>

          {/* Byzantine Security & Quarantine Feed */}
          <div className="glass-card rounded-xl p-4 border border-slate-800 space-y-2 font-mono text-xs">
            <div className="flex items-center gap-2 text-slate-300 pb-2 border-b border-slate-800">
              <Terminal className="h-4 w-4 text-cyan-400" />
              <span className="font-semibold text-xs">Byzantine Defense & Execution Log</span>
            </div>
            <div className="bg-slate-950 p-3 rounded-lg h-28 overflow-y-auto space-y-1 text-[11px] text-slate-400">
              {results.map((r, i) => (
                <div key={i} className="flex justify-between">
                  <span className="text-cyan-400">[Round {r.round_number}] Aggregated 3 bank models using {algorithm}</span>
                  <span className="text-slate-500">{r.aggregation_time_ms}ms</span>
                </div>
              ))}
              {results.length === 0 && <span className="text-slate-600">Waiting for simulation trigger...</span>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
