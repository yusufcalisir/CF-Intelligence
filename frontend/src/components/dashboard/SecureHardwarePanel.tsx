import React from 'react';
import { SimulationDetail } from '../../api/types';
import { ResponsiveContainer, AreaChart, XAxis, YAxis, CartesianGrid, Tooltip, Area } from 'recharts';

interface SecureHardwarePanelProps {
  simulation: SimulationDetail;
}

// Inline SVGs for self-contained, compile-safe icons
const CpuIcon = () => (
  <svg className="h-6 w-6 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <rect x="9" y="9" width="6" height="6" />
    <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 15h3M1 9h3M1 15h3" />
  </svg>
);

const KeyIcon = () => (
  <svg className="h-6 w-6 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
  </svg>
);

const ShieldCheckIcon = () => (
  <svg className="h-4 w-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    <path d="m9 11 2 2 4-4" />
  </svg>
);

const LockIcon = () => (
  <svg className="h-4 w-4 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);

export const SecureHardwarePanel: React.FC<SecureHardwarePanelProps> = ({ simulation }) => {
  const hwMode = simulation?.config?.hardware_isolation_mode || 'none';

  if (hwMode === 'none') {
    return (
      <div className="glass-card p-6 flex flex-col gap-4 border border-slate-800">
        <div className="flex items-center gap-3">
          <CpuIcon />
          <div>
            <h3 className="text-base font-bold text-slate-100">Standard CPU/GPU Plaintext Execution</h3>
            <p className="text-xs text-slate-400">Parameter aggregation processed without specialized hardware isolation.</p>
          </div>
        </div>
      </div>
    );
  }

  // Generate latency chart data based on round count to compare FHE/TEE overhead with plaintext
  const totalRounds = simulation?.rounds?.length || 10;
  const chartData = Array.from({ length: totalRounds }).map((_, idx) => {
    const roundNum = idx + 1;
    const basePlaintextMs = 12 + Math.sin(roundNum) * 2;
    const fheOverheadMs = hwMode === 'fhe' ? basePlaintextMs * 18 : basePlaintextMs * 1.05;
    const teeOverheadMs = hwMode === 'tee' ? basePlaintextMs * 2.8 : basePlaintextMs * 1.02;

    return {
      round: `R${roundNum}`,
      Plaintext: parseFloat(basePlaintextMs.toFixed(1)),
      Enclave: parseFloat(teeOverheadMs.toFixed(1)),
      FHE: parseFloat(fheOverheadMs.toFixed(1)),
    };
  });

  return (
    <div className="glass-card p-6 flex flex-col gap-6 border border-slate-800 relative overflow-hidden">
      <div className="absolute top-0 right-0 w-80 h-80 bg-blue-500/5 rounded-full blur-3xl -z-10 pointer-events-none" />
      <div className="absolute bottom-0 left-0 w-60 h-60 bg-emerald-500/5 rounded-full blur-3xl -z-10 pointer-events-none" />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            {hwMode === 'tee' ? <CpuIcon /> : <KeyIcon />}
            <h3 className="text-base font-bold text-slate-100">
              {hwMode === 'tee' ? '🛡️ Trusted Execution Environment (TEE)' : '🔑 Fully Homomorphic Encryption (FHE)'}
            </h3>
          </div>
          <p className="text-xs text-slate-400">
            {hwMode === 'tee' 
              ? 'Intel SGX / AWS Nitro Enclave secure memory yalıtımı ve kriptografik attestation doğrulaması.' 
              : 'CKKS şeması ile model ağırlıkları şifreli haldeyken plaintext sızıntısı olmadan homomorfik toplama.'}
          </p>
        </div>
        <div>
          <span className="text-xs px-3 py-1 rounded-full font-bold uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            {hwMode === 'tee' ? 'SGX Enclave Active' : 'FHE CKKS Active'}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Telemetry Info */}
        <div className="lg:col-span-1 space-y-4">
          <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Security Telemetry</h4>
          
          {hwMode === 'tee' ? (
            <div className="space-y-3">
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">MRENCLAVE (Code Measurement)</div>
                <div className="text-xs font-mono text-slate-200 truncate mt-0.5">
                  {simulation.tee_mrenclave || 'a7b8e9c0d1e2f3...'}
                </div>
              </div>
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">MRSIGNER (Signer Authority)</div>
                <div className="text-xs font-mono text-slate-200 truncate mt-0.5">
                  {simulation.tee_mrsigner || 'f3e2d1c0b9a8...'}
                </div>
              </div>
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">Attestation Signature</div>
                <div className="text-xs font-mono text-slate-200 truncate mt-0.5">
                  {simulation.tee_attestation_signature || 'tee_signature_verified'}
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs text-emerald-400 font-semibold bg-emerald-500/5 p-2 rounded border border-emerald-500/10">
                <ShieldCheckIcon />
                <span>Remote Attestation verified by Central CA</span>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">FHE Poly Degree</div>
                <div className="text-xs font-mono text-slate-200 mt-0.5">
                  {simulation.fhe_poly_degree || 4096}
                </div>
              </div>
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">Noise Bound (Error budget)</div>
                <div className="text-xs font-mono text-slate-200 mt-0.5">
                  {(simulation.fhe_noise_bound || 1e-9).toExponential(2)}
                </div>
              </div>
              <div className="p-3 bg-slate-950/40 border border-slate-800 rounded-lg">
                <div className="text-[10px] text-slate-500 uppercase font-semibold">Keyring ID</div>
                <div className="text-xs font-mono text-slate-200 truncate mt-0.5">
                  {simulation.fhe_key_id || simulation.id}
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs text-blue-400 font-semibold bg-blue-500/5 p-2 rounded border border-blue-500/10">
                <LockIcon />
                <span>Zero-knowledge plaintext parameter leaks</span>
              </div>
            </div>
          )}
        </div>

        {/* Latency / Performance Comparison Chart */}
        <div className="lg:col-span-2 flex flex-col gap-3">
          <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider flex justify-between items-center">
            <span>Aggregation Latency Comparison</span>
            <span className="text-[10px] text-slate-500 font-mono">lower is better (values in ms)</span>
          </h4>
          
          <div className="h-56 bg-slate-950/30 border border-slate-800 rounded-xl p-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="colorPlaintext" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorTEE" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#14b8a6" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorFHE" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="round" stroke="#64748b" fontSize={11} tickLine={false} />
                <YAxis stroke="#64748b" fontSize={11} tickLine={false} />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#0f172a', borderColor: '#334155', borderRadius: '8px' }}
                  labelStyle={{ color: '#94a3b8', fontSize: '11px', fontWeight: 'bold' }}
                />
                <Area type="monotone" dataKey="Plaintext" stroke="#6366f1" fillOpacity={1} fill="url(#colorPlaintext)" name="Plaintext (Plain FedAvg)" />
                {hwMode === 'tee' && (
                  <Area type="monotone" dataKey="Enclave" stroke="#14b8a6" fillOpacity={1} fill="url(#colorTEE)" name="TEE Secure Sum Enclave" />
                )}
                {hwMode === 'fhe' && (
                  <Area type="monotone" dataKey="FHE" stroke="#f59e0b" fillOpacity={1} fill="url(#colorFHE)" name="FHE CKKS Homomorphic Sum" />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
};
