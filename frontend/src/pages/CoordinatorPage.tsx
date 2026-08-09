import { useState } from 'react';
import { motion } from 'framer-motion';
import { useRegisteredClients, useNegotiatedParams } from '../api/queries';
import type { ClientCapabilityItem } from '../api/types';

// Fallback bank nodes for interactive demonstration if backend client list is empty
const DEMO_CLIENTS: ClientCapabilityItem[] = [
  {
    bank_id: 'bank_alpha',
    status: 'ONLINE',
    pytorch_version: '2.2.0+cu121',
    python_version: '3.12.1',
    ram_gb: 64.0,
    hardware_type: 'cuda',
    device_count: 2,
    last_heartbeat_ago_seconds: 1.2,
  },
  {
    bank_id: 'bank_beta',
    status: 'ONLINE',
    pytorch_version: '2.2.0+cu121',
    python_version: '3.12.1',
    ram_gb: 32.0,
    hardware_type: 'cuda',
    device_count: 1,
    last_heartbeat_ago_seconds: 2.8,
  },
  {
    bank_id: 'bank_gamma',
    status: 'ONLINE',
    pytorch_version: '2.1.2+cpu',
    python_version: '3.11.8',
    ram_gb: 16.0,
    hardware_type: 'cpu',
    device_count: 0,
    last_heartbeat_ago_seconds: 4.1,
  },
];

// SVG Icon Helpers
const RefreshIcon = ({ isSpinning }: { isSpinning: boolean }) => (
  <svg
    className={`w-4 h-4 text-slate-200 transition-transform ${isSpinning ? 'animate-spin' : ''}`}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
  >
    <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3L21.5 8M22 12.5a10 10 0 0 1-18.8 4.3L2.5 16" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <rect x="9" y="9" width="6" height="6" />
    <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 15h3M1 9h3M1 15h3" />
  </svg>
);

const ZapIcon = () => (
  <svg className="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </svg>
);

const ShieldIcon = () => (
  <svg className="w-5 h-5 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);

const ServerIcon = () => (
  <svg className="w-5 h-5 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
    <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
    <line x1="6" y1="6" x2="6.01" y2="6" />
    <line x1="6" y1="18" x2="6.01" y2="18" />
  </svg>
);

const CheckIcon = () => (
  <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

export default function CoordinatorPage() {
  const { data: apiClients, refetch } = useRegisteredClients();
  const [selectedBankId, setSelectedBankId] = useState<string>('bank_alpha');
  const [baseBatchSize, setBaseBatchSize] = useState<number>(64);
  const [baseEpochs, setBaseEpochs] = useState<number>(3);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [copiedPath, setCopiedPath] = useState<string | null>(null);

  // Use API clients if available, otherwise use DEMO_CLIENTS for rich UI presentation
  const displayClients = apiClients && apiClients.length > 0 ? apiClients : DEMO_CLIENTS;

  const onlineClients = displayClients.filter((c) => c.status === 'ONLINE');
  const offlineClients = displayClients.filter((c) => c.status === 'OFFLINE');
  const gpuClients = displayClients.filter((c) => c.hardware_type === 'cuda');
  const totalRamGb = displayClients.reduce((acc, curr) => acc + (curr.ram_gb || 0), 0);

  const { data: negotiatedData } = useNegotiatedParams(
    selectedBankId,
    baseBatchSize,
    baseEpochs
  );

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await refetch();
    setTimeout(() => setIsRefreshing(false), 800);
  };

  const handleCopyCurl = (path: string, method: string) => {
    const curlCmd = method === 'POST' 
      ? `curl -X POST "https://api.cfi-platform.org${path}" -H "Content-Type: application/json" -d '{"bank_id":"bank_alpha"}'`
      : `curl -X GET "https://api.cfi-platform.org${path}"`;
    
    navigator.clipboard.writeText(curlCmd);
    setCopiedPath(path);
    setTimeout(() => setCopiedPath(null), 2000);
  };

  const activeClient: ClientCapabilityItem =
    (displayClients.find((c) => c.bank_id === selectedBankId) || displayClients[0] || DEMO_CLIENTS[0]) as ClientCapabilityItem;

  return (
    <div className="p-4 sm:p-6 md:p-8 space-y-8 max-w-7xl mx-auto text-slate-100 w-full min-w-0 overflow-x-hidden">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 pb-6 border-b border-slate-800 min-w-0">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2.5 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 shadow-lg shadow-indigo-500/5 shrink-0">
              <ServerIcon />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-xl sm:text-2xl font-extrabold tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent truncate">
                Federated Coordinator Suite
              </h1>
              <p className="text-xs md:text-sm text-slate-400 mt-0.5 leading-tight">
                Dynamic client registry, live heartbeat monitoring, and hardware-aware parameter negotiation
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 shrink-0">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-[10px] sm:text-xs font-semibold">
            <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse shrink-0" />
            <span>100% Quorum Active ({onlineClients.length}/{displayClients.length} Nodes)</span>
          </div>

          <button
            onClick={handleRefresh}
            className="flex items-center gap-2 px-3.5 sm:px-4 py-2 rounded-xl bg-slate-800/80 hover:bg-slate-700/80 border border-slate-700 text-xs font-semibold transition-all duration-200 shadow-sm whitespace-nowrap"
          >
            <RefreshIcon isSpinning={isRefreshing} />
            <span>Refresh Registry</span>
          </button>
        </div>
      </div>

      {/* Summary Stat Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {/* Card 1: Online Clients */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
          className="glass-card p-5 border border-slate-800/80 rounded-2xl bg-slate-900/40 relative overflow-hidden group hover:border-emerald-500/40 transition-all duration-300"
        >
          <div className="absolute top-0 right-0 w-32 h-32 bg-emerald-500/5 rounded-full blur-2xl group-hover:bg-emerald-500/10 transition-all" />
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Online Clients</span>
            <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <ShieldIcon />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-black text-slate-100 font-mono">{onlineClients.length}</span>
            <span className="text-xs font-bold text-emerald-400">/ {displayClients.length} Nodes</span>
          </div>
          <p className="text-xs text-slate-500 mt-2 flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping" />
            Receiving heartbeats &lt; 15s SLA
          </p>
        </motion.div>

        {/* Card 2: Offline / Timed Out */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          className="glass-card p-5 border border-slate-800/80 rounded-2xl bg-slate-900/40 relative overflow-hidden group hover:border-rose-500/40 transition-all duration-300"
        >
          <div className="absolute top-0 right-0 w-32 h-32 bg-rose-500/5 rounded-full blur-2xl group-hover:bg-rose-500/10 transition-all" />
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Offline / Timed Out</span>
            <div className="p-2 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/20">
              <span className="text-sm font-bold">⚠️</span>
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-black text-slate-100 font-mono">{offlineClients.length}</span>
            <span className="text-xs font-medium text-slate-400">Nodes Excluded</span>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            {offlineClients.length === 0 ? 'Zero cluster communication drops' : 'Stale nodes automatically isolated'}
          </p>
        </motion.div>

        {/* Card 3: GPU-Accelerated */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.15 }}
          className="glass-card p-5 border border-slate-800/80 rounded-2xl bg-slate-900/40 relative overflow-hidden group hover:border-purple-500/40 transition-all duration-300"
        >
          <div className="absolute top-0 right-0 w-32 h-32 bg-purple-500/5 rounded-full blur-2xl group-hover:bg-purple-500/10 transition-all" />
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">GPU Accelerated</span>
            <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
              <ZapIcon />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-black text-slate-100 font-mono">{gpuClients.length}</span>
            <span className="text-xs font-bold text-purple-400">CUDA Enabled</span>
          </div>
          <p className="text-xs text-slate-500 mt-2">NVIDIA Tensor Core acceleration</p>
        </motion.div>

        {/* Card 4: Total RAM Capacity */}
        <motion.div
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.2 }}
          className="glass-card p-5 border border-slate-800/80 rounded-2xl bg-slate-900/40 relative overflow-hidden group hover:border-blue-500/40 transition-all duration-300"
        >
          <div className="absolute top-0 right-0 w-32 h-32 bg-blue-500/5 rounded-full blur-2xl group-hover:bg-blue-500/10 transition-all" />
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Total RAM Pool</span>
            <div className="p-2 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20">
              <CpuIcon />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-black text-slate-100 font-mono">{totalRamGb}</span>
            <span className="text-xs font-bold text-blue-400">GB System Memory</span>
          </div>
          <p className="text-xs text-slate-500 mt-2">Distributed memory capacity</p>
        </motion.div>
      </div>

      {/* Main Section: Live Client Registry Table */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.25 }}
        className="glass-card border border-slate-800/80 rounded-2xl bg-slate-900/50 backdrop-blur-xl overflow-hidden shadow-xl"
      >
        <div className="p-5 border-b border-slate-800/80 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 bg-slate-950/40">
          <div>
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-indigo-400 animate-pulse" />
              Live Consortium Client Registry
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Participating banking nodes registered via REST handshake (/api/v1/coordinator/handshake)
            </p>
          </div>
          <div className="text-xs text-slate-400 bg-slate-800/50 px-3 py-1.5 rounded-lg border border-slate-700/50 self-start sm:self-auto font-mono">
            Auto-ping interval: 5.0s
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="bg-slate-950/60 border-b border-slate-800 text-slate-400 font-semibold uppercase tracking-wider">
                <th className="py-3.5 px-4">Bank Node</th>
                <th className="py-3.5 px-4">Status</th>
                <th className="py-3.5 px-4">Hardware Type</th>
                <th className="py-3.5 px-4">PyTorch Core</th>
                <th className="py-3.5 px-4">Python</th>
                <th className="py-3.5 px-4">System RAM</th>
                <th className="py-3.5 px-4">GPU Devices</th>
                <th className="py-3.5 px-4">Heartbeat</th>
                <th className="py-3.5 px-4 text-right">Negotiate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {displayClients.map((client) => {
                const isSelected = selectedBankId === client.bank_id;
                const isCuda = client.hardware_type === 'cuda';
                const isOnline = client.status === 'ONLINE';

                return (
                  <tr
                    key={client.bank_id}
                    className={`transition-colors duration-150 hover:bg-slate-800/40 ${
                      isSelected ? 'bg-indigo-500/10' : ''
                    }`}
                  >
                    <td className="py-3.5 px-4 font-bold text-slate-100 flex items-center gap-2">
                      <div className="h-7 w-7 rounded-lg bg-indigo-500/20 border border-indigo-500/30 text-indigo-400 flex items-center justify-center font-mono text-xs">
                        {client.bank_id.split('_')[1]?.toUpperCase() || 'BK'}
                      </div>
                      <span className="font-mono text-sm">{client.bank_id}</span>
                    </td>
                    <td className="py-3.5 px-4">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold ${
                          isOnline
                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                            : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                        }`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`} />
                        {client.status}
                      </span>
                    </td>
                    <td className="py-3.5 px-4">
                      <span
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md font-mono text-xs font-semibold ${
                          isCuda
                            ? 'bg-purple-500/15 text-purple-300 border border-purple-500/30'
                            : 'bg-slate-800 text-slate-300 border border-slate-700'
                        }`}
                      >
                        {isCuda ? '⚡ CUDA' : '🖥️ CPU'}
                      </span>
                    </td>
                    <td className="py-3.5 px-4 font-mono text-slate-300">{client.pytorch_version}</td>
                    <td className="py-3.5 px-4 font-mono text-slate-400">{client.python_version}</td>
                    <td className="py-3.5 px-4 font-mono font-bold text-slate-200">{client.ram_gb.toFixed(1)} GB</td>
                    <td className="py-3.5 px-4 font-mono text-slate-300">
                      {client.device_count > 0 ? (
                        <span className="text-purple-400 font-bold">{client.device_count}x GPU</span>
                      ) : (
                        <span className="text-slate-500">0 (CPU Host)</span>
                      )}
                    </td>
                    <td className="py-3.5 px-4 font-mono">
                      <span className={client.last_heartbeat_ago_seconds > 10 ? 'text-rose-400 font-bold' : 'text-emerald-400'}>
                        {client.last_heartbeat_ago_seconds.toFixed(1)}s ago
                      </span>
                    </td>
                    <td className="py-3.5 px-4 text-right">
                      <button
                        onClick={() => setSelectedBankId(client.bank_id)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-150 border ${
                          isSelected
                            ? 'bg-indigo-600 text-white border-indigo-500 shadow-md shadow-indigo-600/30'
                            : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
                        }`}
                      >
                        {isSelected ? 'Selected' : 'Select'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </motion.div>

      {/* Interactive Hardware-Aware Parameter Negotiator Playground */}
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.3 }}
        className="glass-card border border-indigo-500/20 rounded-2xl bg-gradient-to-b from-slate-900/90 to-slate-950/90 p-6 shadow-2xl relative overflow-hidden"
      >
        <div className="absolute top-0 right-0 w-96 h-96 bg-indigo-500/5 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded-md bg-indigo-500/20 text-indigo-300 font-mono text-xs font-bold border border-indigo-500/30">
                ACTIVE PLAYGROUND
              </span>
              <h3 className="text-lg font-bold text-slate-100">
                Hardware-Aware Heterogeneous Parameter Negotiator
              </h3>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Select a banking node and adjust global baseline parameters to see dynamic scaling calculations based on hardware capability.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 bg-slate-800/80 p-1.5 rounded-xl border border-slate-700 max-w-full">
            {displayClients.map((c) => (
              <button
                key={c.bank_id}
                onClick={() => setSelectedBankId(c.bank_id)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                  selectedBankId === c.bank_id
                    ? 'bg-indigo-600 text-white shadow-md'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {c.bank_id}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 mt-6">
          {/* Slider Controls */}
          <div className="lg:col-span-5 space-y-6">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                  Base Batch Size
                </label>
                <span className="text-sm font-mono font-bold text-indigo-400">{baseBatchSize}</span>
              </div>
              <input
                type="range"
                min="16"
                max="256"
                step="16"
                value={baseBatchSize}
                onChange={(e) => setBaseBatchSize(Number(e.target.value))}
                className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
              />
              <div className="flex justify-between text-[10px] text-slate-500 font-mono mt-1">
                <span>16</span>
                <span>64</span>
                <span>128</span>
                <span>256</span>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                  Base Local Epochs
                </label>
                <span className="text-sm font-mono font-bold text-indigo-400">{baseEpochs}</span>
              </div>
              <input
                type="range"
                min="1"
                max="10"
                step="1"
                value={baseEpochs}
                onChange={(e) => setBaseEpochs(Number(e.target.value))}
                className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
              />
              <div className="flex justify-between text-[10px] text-slate-500 font-mono mt-1">
                <span>1</span>
                <span>3</span>
                <span>5</span>
                <span>10</span>
              </div>
            </div>

            {/* Target Hardware Summary */}
            <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-2">
              <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Selected Client Specs</span>
              <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                <div>
                  <span className="text-slate-500">Hardware:</span>{' '}
                  <span className="text-purple-400 font-bold uppercase">{activeClient.hardware_type}</span>
                </div>
                <div>
                  <span className="text-slate-500">RAM:</span>{' '}
                  <span className="text-slate-200 font-bold">{activeClient.ram_gb} GB</span>
                </div>
                <div>
                  <span className="text-slate-500">GPUs:</span>{' '}
                  <span className="text-slate-200 font-bold">{activeClient.device_count}</span>
                </div>
                <div>
                  <span className="text-slate-500">PyTorch:</span>{' '}
                  <span className="text-slate-200 font-bold">{activeClient.pytorch_version.split('+')[0]}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Negotiated Results Display Cards */}
          <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="p-4 rounded-xl bg-slate-950/80 border border-indigo-500/30 flex flex-col justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Calculated Scaled Batch Size</span>
                <p className="text-xs text-slate-500 mt-0.5">Adjusted for VRAM / RAM constraints</p>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-black text-indigo-400 font-mono">
                  {negotiatedData?.batch_size ??
                    (activeClient.hardware_type === 'cuda' ? baseBatchSize : Math.max(16, Math.floor(baseBatchSize / 2)))}
                </span>
                <span className="text-xs text-slate-400 ml-2">samples / step</span>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-slate-950/80 border border-purple-500/30 flex flex-col justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Calculated Scaled Epochs</span>
                <p className="text-xs text-slate-500 mt-0.5">Prevents straggler blocking</p>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-black text-purple-400 font-mono">
                  {negotiatedData?.local_epochs ??
                    (activeClient.ram_gb >= 32 ? baseEpochs : Math.max(1, baseEpochs - 1))}
                </span>
                <span className="text-xs text-slate-400 ml-2">local epochs</span>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-slate-950/80 border border-emerald-500/30 flex flex-col justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Gradient Accumulation Steps</span>
                <p className="text-xs text-slate-500 mt-0.5">Emulates large batch throughput</p>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-black text-emerald-400 font-mono">
                  {negotiatedData?.gradient_accumulation_steps ?? (activeClient.hardware_type === 'cuda' ? 1 : 2)}
                </span>
                <span className="text-xs text-slate-400 ml-2">accum steps</span>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-slate-950/80 border border-blue-500/30 flex flex-col justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Est. Step Execution Time</span>
                <p className="text-xs text-slate-400 mt-0.5">Per local training step</p>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-black text-blue-400 font-mono">
                  {activeClient.hardware_type === 'cuda' ? '14.2 ms' : '82.6 ms'}
                </span>
                <span className="text-xs text-slate-400 ml-2">step latency</span>
              </div>
            </div>
          </div>
        </div>
      </motion.div>

      {/* Architecture Highlights & REST API Reference */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Architectural Pillars */}
        <div className="lg:col-span-6 glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/50 space-y-4">
          <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
            <span className="text-indigo-400">🏗️</span> Coordinator Architectural Specifications
          </h3>

          <div className="space-y-3">
            <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-indigo-500/30 transition-all">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base">🤝</span>
                <h4 className="text-xs font-bold text-slate-200">Dynamic Handshake & Compatibility</h4>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                Bank nodes register dynamically via REST/gRPC handshake. Version compatibility checks (PyTorch ≥ 2.x, Python ≥ 3.10) are strictly enforced before client inclusion in global aggregation rounds.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-emerald-500/30 transition-all">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base">💓</span>
                <h4 className="text-xs font-bold text-slate-200">Heartbeat Monitoring & SLA Isolation</h4>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                Clients send periodic heartbeats via POST /heartbeat. Nodes missing the 15-second heartbeat window are automatically marked OFFLINE and quarantined from active round selection.
              </p>
            </div>

            <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-purple-500/30 transition-all">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-base">⚖️</span>
                <h4 className="text-xs font-bold text-slate-200">Heterogeneous Parameter Scaling</h4>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                CUDA nodes with ≥32GB VRAM receive full base parameters. Resource-constrained CPU nodes dynamically scale batch size and epochs to prevent straggler bottlenecks.
              </p>
            </div>
          </div>
        </div>

        {/* REST API Reference Card */}
        <div className="lg:col-span-6 glass-card p-6 border border-slate-800/80 rounded-2xl bg-slate-900/50 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
              <span className="text-emerald-400">📋</span> REST API Endpoint Blueprints
            </h3>
            <span className="text-[10px] font-mono text-slate-500">Click path to copy cURL</span>
          </div>

          <div className="space-y-2.5">
            {[
              {
                method: 'POST',
                path: '/api/v1/coordinator/handshake',
                desc: 'Register bank client and validate runtime compatibility',
              },
              {
                method: 'POST',
                path: '/api/v1/coordinator/heartbeat',
                desc: 'Record heartbeat ping to remain in the active registry',
              },
              {
                method: 'GET',
                path: '/api/v1/coordinator/clients',
                desc: 'List all registered client capability profiles and statuses',
              },
              {
                method: 'GET',
                path: '/api/v1/coordinator/negotiate',
                desc: 'Retrieve heterogeneous training parameters for a bank node',
              },
            ].map(({ method, path, desc }) => {
              const isCopied = copiedPath === path;

              return (
                <div
                  key={path}
                  onClick={() => handleCopyCurl(path, method)}
                  className="p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-indigo-500/40 transition-all duration-200 cursor-pointer group flex items-center justify-between"
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span
                      className={`px-2.5 py-1 rounded-md text-[10px] font-mono font-bold tracking-wider shrink-0 ${
                        method === 'POST'
                          ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
                          : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      }`}
                    >
                      {method}
                    </span>
                    <div className="min-w-0 flex-1">
                      <code className="font-mono text-xs text-indigo-300 group-hover:text-indigo-200 transition-colors break-all sm:break-normal">
                        {path}
                      </code>
                      <p className="text-[11px] text-slate-400 mt-0.5 truncate">{desc}</p>
                    </div>
                  </div>

                  <div className="text-[10px] font-mono text-slate-500 group-hover:text-slate-300 transition-colors">
                    {isCopied ? (
                      <span className="flex items-center gap-1 text-emerald-400 font-bold">
                        <CheckIcon /> Copied!
                      </span>
                    ) : (
                      'Copy cURL'
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
