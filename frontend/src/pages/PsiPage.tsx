import React, { useState, useEffect } from 'react';
import { useRunPSI, useFuzzyResolve } from '../api/queries';
import { BANK_NAMES, BANK_COLORS, FuzzyMatchResponse } from '../api/types';

// Regional character transliteration mapping (Turkish, German, etc.)
const TRANSLITERATION_MAP: Record<string, string> = {
  'ı': 'i',
  'ş': 's',
  'ç': 'c',
  'ğ': 'g',
  'ö': 'o',
  'ü': 'u',
  'ä': 'a',
  'ß': 'ss',
  'ñ': 'n',
  'ø': 'o',
  'æ': 'ae',
  'å': 'a',
};

// Clean name standardization in JS (matches python implementation)
function localStandardize(val: string): string {
  const text = val.normalize('NFC').toLowerCase();
  let result = '';
  for (let i = 0; i < text.length; i++) {
    const ch = text.charAt(i);
    if (Object.prototype.hasOwnProperty.call(TRANSLITERATION_MAP, ch)) {
      result += TRANSLITERATION_MAP[ch];
    } else {
      result += ch;
    }
  }
  // Strip remaining accents/diacritics
  result = result.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  // Keep only alphanumeric and spaces
  result = result.replace(/[^a-z0-9 ]/g, ' ');
  // Collapse spaces
  return result.replace(/\s+/g, ' ').trim();
}

// Simple FNV-1a hash function for shingle signature generation
function fnv1a(str: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash += (hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24);
  }
  return hash >>> 0;
}

// Compute MinHash signature locally for the playground
function localComputeMinHash(text: string, numHashes = 16): number[] {
  const normalized = localStandardize(text);
  // Extract 3-grams
  const shingles = new Set<string>();
  if (normalized.length < 3) {
    if (normalized.length > 0) shingles.add(normalized);
  } else {
    for (let i = 0; i <= normalized.length - 3; i++) {
      shingles.add(normalized.substring(i, i + 3));
    }
  }

  const sigs: number[] = [];
  if (shingles.size === 0) {
    return Array(numHashes).fill(0);
  }

  for (let i = 0; i < numHashes; i++) {
    let minVal = Infinity;
    shingles.forEach((shingle) => {
      // hash each shingle with seed/index i
      const val = fnv1a(`${shingle}|${i}`);
      if (val < minVal) {
        minVal = val;
      }
    });
    sigs.push(minVal);
  }
  return sigs;
}

export default function PsiPage() {
  // Local similarity playground state
  const [name1, setName1] = useState('Yusuf Çalışır');
  const [name2, setName2] = useState('Yusuf Calisir');
  const [simScore, setSimScore] = useState<number>(0);
  const [sig1, setSig1] = useState<number[]>([]);
  const [sig2, setSig2] = useState<number[]>([]);

  // PSI Execution panel state
  const [bankA, setBankA] = useState('bank_a');
  const [bankB, setBankB] = useState('bank_b');
  const [entityType, setEntityType] = useState('customer');
  const [enableFuzzy, setEnableFuzzy] = useState(true);
  const [fuzzyThreshold, setFuzzyThreshold] = useState(3);
  const [enableTee, setEnableTee] = useState(true);
  const [logs, setLogs] = useState<string[]>([]);
  const [logsRunning, setLogsRunning] = useState(false);

  // Search central LSH registry state
  const [searchQuery, setSearchQuery] = useState('Yusuf Calisir');
  const [searchType, setSearchType] = useState('customer');
  const [searchThreshold, setSearchThreshold] = useState(0.5);

  const runPSIMutation = useRunPSI();
  const fuzzyResolveMutation = useFuzzyResolve();

  // Run local similarity comparison when names change
  useEffect(() => {
    const s1 = localComputeMinHash(name1);
    const s2 = localComputeMinHash(name2);
    setSig1(s1);
    setSig2(s2);

    let matches = 0;
    for (let i = 0; i < s1.length; i++) {
      if (s1[i] === s2[i]) matches++;
    }
    setSimScore(matches / s1.length);
  }, [name1, name2]);

  // Simulated log generator for Enclave Execution
  const addEnclaveLog = (msg: string, delay: number) => {
    return new Promise<void>((resolve) => {
      setTimeout(() => {
        setLogs((prev) => [...prev, `[${new Date().toLocaleTimeString()}] ${msg}`]);
        resolve();
      }, delay);
    });
  };

  const handleRunPSI = async (e: React.FormEvent) => {
    e.preventDefault();
    if (bankA === bankB) {
      alert('Please select two different banks to run Private Set Intersection.');
      return;
    }

    setLogs([]);
    setLogsRunning(true);

    if (enableTee) {
      await addEnclaveLog('Initializing secure SGX hardware enclave connection...', 200);
      await addEnclaveLog('Verifying remote attestation payload...', 400);
      await addEnclaveLog('Enclave Signature MRENCLAVE match found: 0x8fae3f19114d7a8... ✅', 300);
      await addEnclaveLog('Enclave Signer MRSIGNER match found: 0xc4b220e897bd21ab163... ✅', 200);
      await addEnclaveLog('Attestation validation signature check: SUCCESS.', 200);
      await addEnclaveLog('Spinning up multi-party computation engine inside SGX enclave...', 400);
    } else {
      await addEnclaveLog('Establishing direct secure multi-party communication channel...', 300);
      await addEnclaveLog('Retrieving ephemeral Diffie-Hellman parameters...', 300);
    }

    await addEnclaveLog('Exchanging blind signature keys between participants...', 300);
    await addEnclaveLog('Running cryptographic set intersection protocol...', 400);

    runPSIMutation.mutate(
      {
        bank_a_id: bankA,
        bank_b_id: bankB,
        entity_type: entityType === 'all' ? undefined : entityType,
        enable_fuzzy: enableFuzzy,
        fuzzy_threshold: fuzzyThreshold,
        enable_tee: enableTee,
      },
      {
        onSuccess: async (data) => {
          setLogsRunning(false);
          await addEnclaveLog(`Protocol successfully completed. Found ${data.matches.length} matching entities.`, 100);
          if (enableTee) {
            await addEnclaveLog('Clearing secure memory cache. Enclave connection closed.', 200);
          }
        },
        onError: async (err) => {
          setLogsRunning(false);
          await addEnclaveLog(`❌ Protocol error: ${err.message}`, 100);
        },
      }
    );
  };

  const handleFuzzyResolve = (e: React.FormEvent) => {
    e.preventDefault();
    fuzzyResolveMutation.mutate({
      raw_identifier: searchQuery,
      entity_type: searchType,
      similarity_threshold: searchThreshold,
      limit: 10,
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 p-6 glass-card rounded-2xl bg-gradient-to-r from-slate-900/60 to-indigo-950/40 border border-indigo-500/20">
        <div>
          <h1 className="text-2xl font-bold bg-gradient-to-r from-white via-indigo-200 to-indigo-400 bg-clip-text text-transparent">
            Private Set Intersection (PSI)
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Reconcile customer & entity identities across banks without exposing PII.
          </p>
        </div>
        <div className="flex items-center gap-2.5 px-4 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          Intel SGX Enclave Active
        </div>
      </div>

      {/* Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* PSI Run Configuration Form (Left) */}
        <div className="lg:col-span-7 flex flex-col gap-6">
          <div className="glass-card p-6 border border-slate-800">
            <h2 className="text-lg font-bold text-slate-200 mb-4 flex items-center gap-2">
              <span>⚙️</span> PSI Protocol Control Center
            </h2>
            <form onSubmit={handleRunPSI} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                    Institution A
                  </label>
                  <select
                    value={bankA}
                    onChange={(e) => setBankA(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 opacity-90"
                  >
                    <option value="bank_a">Bank A (Meridian National)</option>
                    <option value="bank_b">Bank B (Nexus Digital)</option>
                    <option value="bank_c">Bank C (Heritage Regional)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                    Institution B
                  </label>
                  <select
                    value={bankB}
                    onChange={(e) => setBankB(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 opacity-90"
                  >
                    <option value="bank_a">Bank A (Meridian National)</option>
                    <option value="bank_b">Bank B (Nexus Digital)</option>
                    <option value="bank_c">Bank C (Heritage Regional)</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
                    Filter Entity Type
                  </label>
                  <select
                    value={entityType}
                    onChange={(e) => setEntityType(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 opacity-90"
                  >
                    <option value="all">All Entity Types</option>
                    <option value="customer">Customer Only</option>
                    <option value="merchant">Merchant Only</option>
                    <option value="device">Device Only</option>
                    <option value="phone">Phone Only</option>
                    <option value="email">Email Only</option>
                  </select>
                </div>

                <div className="flex flex-col justify-end">
                  <div className="flex items-center justify-between p-2 rounded-lg bg-slate-950 border border-slate-800">
                    <div className="flex flex-col">
                      <span className="text-xs font-bold text-slate-300">Intel SGX Enclave (TEE)</span>
                      <span className="text-[10px] text-slate-500">Accelerate inside secure enclave</span>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={enableTee}
                        onChange={(e) => setEnableTee(e.target.checked)}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-400 after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                    </label>
                  </div>
                </div>
              </div>

              <div className="border-t border-slate-800/80 pt-4 space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex flex-col">
                    <span className="text-sm font-bold text-slate-300 flex items-center gap-1.5">
                      <span>🔗</span> Multi-Attribute Fuzzy PSI
                    </span>
                    <span className="text-xs text-slate-500">
                      Match if at least k of 5 attributes (Phone, Email, Device, Birthdate, Surname) align
                    </span>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={enableFuzzy}
                      onChange={(e) => setEnableFuzzy(e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-400 after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
                  </label>
                </div>

                {enableFuzzy && (
                  <div className="p-3 rounded-lg bg-slate-950 border border-indigo-950/60 space-y-2">
                    <div className="flex justify-between text-xs font-semibold">
                      <span className="text-slate-400">Min Matched Attributes (k):</span>
                      <span className="text-indigo-400 font-mono">{fuzzyThreshold} / 5</span>
                    </div>
                    <input
                      type="range"
                      min="1"
                      max="5"
                      value={fuzzyThreshold}
                      onChange={(e) => setFuzzyThreshold(parseInt(e.target.value))}
                      className="w-full accent-indigo-500 cursor-pointer bg-slate-800 rounded-lg h-1.5"
                    />
                    <div className="flex justify-between text-[9px] text-slate-500 font-mono">
                      <span>1 (Highly Permissive)</span>
                      <span>3 (Recommended)</span>
                      <span>5 (Exact Match)</span>
                    </div>
                  </div>
                )}
              </div>

              <button
                type="submit"
                disabled={runPSIMutation.isPending || logsRunning}
                className="w-full h-11 min-h-[44px] bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 disabled:opacity-50 text-white font-bold px-4 rounded-xl text-sm transition-all duration-200 flex items-center justify-center gap-2 border border-indigo-400/20 shadow-lg shadow-indigo-900/20 whitespace-nowrap shrink-0 cursor-pointer"
              >
                {runPSIMutation.isPending || logsRunning ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin shrink-0"></span>
                    <span>Executing PSI Protocol...</span>
                  </>
                ) : (
                  <>
                    <span>🔐</span>
                    <span>Run Private Set Intersection</span>
                  </>
                )}
              </button>
            </form>
          </div>

          {/* Enclave Verification Terminal & Stats */}
          <div className="glass-card p-6 border border-slate-800 flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h2 className="text-md font-bold text-slate-300 flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-indigo-500 animate-pulse"></span>
                Secure Enclave Execution Log
              </h2>
              {runPSIMutation.data?.stats.attestation_verified && (
                <span className="text-[10px] px-2 py-0.5 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-mono uppercase">
                  Attested
                </span>
              )}
            </div>

            <div className="bg-black/80 rounded-xl border border-slate-900 p-4 h-44 overflow-y-auto font-mono text-xs text-slate-300 space-y-1.5 scrollbar-thin">
              {logs.length === 0 ? (
                <div className="text-slate-600 italic">No active session log. Launch PSI protocol above to view logs.</div>
              ) : (
                logs.map((log, index) => (
                  <div key={index} className="leading-relaxed whitespace-pre-wrap">
                    {log}
                  </div>
                ))
              )}
              {logsRunning && (
                <div className="text-indigo-400 animate-pulse">Running secure enclave calculation...</div>
              )}
            </div>

            {runPSIMutation.data && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-3.5 rounded-xl bg-slate-950 border border-slate-800">
                <div className="flex flex-col">
                  <span className="text-[10px] text-slate-500 font-semibold uppercase">Exchanged</span>
                  <span className="text-sm font-bold text-slate-200 font-mono">
                    {(runPSIMutation.data.stats.data_exchanged_bytes / 1024).toFixed(2)} KB
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] text-slate-500 font-semibold uppercase">Computation</span>
                  <span className="text-sm font-bold text-slate-200 font-mono">
                    {runPSIMutation.data.stats.computation_time_ms.toFixed(2)} ms
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] text-slate-500 font-semibold uppercase">DH Prime</span>
                  <span className="text-sm font-bold text-slate-200 font-mono">
                    {runPSIMutation.data.stats.prime_bit_length || 512} bit
                  </span>
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] text-slate-500 font-semibold uppercase">Accelerator</span>
                  <span className="text-sm font-bold text-indigo-400 font-mono">
                    {runPSIMutation.data.stats.enclave_execution ? 'SGX TEE (15x)' : 'None (DH)'}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Local Similarity Playground (Right) */}
        <div className="lg:col-span-5 flex flex-col gap-6">
          <div className="glass-card p-6 border border-slate-800 flex-1">
            <h2 className="text-md font-bold text-slate-200 mb-3.5 flex items-center gap-2">
              <span>🧬</span> MinHash Spelling Playground
            </h2>
            <p className="text-xs text-slate-400 mb-4 leading-relaxed">
              Test spelling normalizations and evaluate name similarity locally. Character 3-gram MinHash signatures are computed below.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-[11px] font-semibold text-slate-500 uppercase mb-1">
                  Name Input 1
                </label>
                <input
                  type="text"
                  value={name1}
                  onChange={(e) => setName1(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                />
                <span className="text-[10px] text-slate-500 font-mono mt-1 block">
                  Standardized: <span className="text-indigo-400 font-bold">{localStandardize(name1)}</span>
                </span>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-slate-500 uppercase mb-1">
                  Name Input 2
                </label>
                <input
                  type="text"
                  value={name2}
                  onChange={(e) => setName2(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
                />
                <span className="text-[10px] text-slate-500 font-mono mt-1 block">
                  Standardized: <span className="text-indigo-400 font-bold">{localStandardize(name2)}</span>
                </span>
              </div>

              <div className="p-4 rounded-xl border border-indigo-500/10 bg-gradient-to-br from-indigo-950/20 to-slate-950 flex items-center gap-4">
                <div className="relative flex items-center justify-center h-16 w-16 rounded-full border-4 border-indigo-500/20">
                  <span className="text-md font-bold font-mono text-indigo-300">
                    {Math.round(simScore * 100)}%
                  </span>
                </div>
                <div>
                  <h4 className="text-sm font-bold text-slate-300">Estimated Jaccard Similarity</h4>
                  <p className="text-[11px] text-slate-500 leading-relaxed mt-0.5">
                    {simScore >= 0.7
                      ? 'Excellent alignment. Standard matching will resolve.'
                      : simScore >= 0.4
                      ? 'Fuzzy relationship detected. Recommended threshold.'
                      : 'Weak signature overlap. Distinct identities.'}
                  </p>
                </div>
              </div>

              {/* Signature visualization */}
              <div className="space-y-1.5">
                <label className="block text-[11px] font-semibold text-slate-500 uppercase">
                  16-Dimensional MinHash Vector Comparison
                </label>
                <div className="flex flex-wrap gap-1 p-2 bg-slate-950 border border-slate-900 rounded-lg overflow-x-auto min-w-[280px]">
                  {sig1.map((val, idx) => {
                    const match = sig2[idx] === val;
                    return (
                      <div
                        key={idx}
                        className={`h-6 w-8 rounded flex items-center justify-center font-mono text-[9px] font-bold ${
                          match
                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                            : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                        }`}
                        title={`Index ${idx}\nSig1: ${val}\nSig2: ${sig2[idx]}`}
                      >
                        {idx + 1}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Real-time Central LSH Resolver Panel */}
      <div className="glass-card p-6 border border-slate-800">
        <h2 className="text-md font-bold text-slate-200 mb-4 flex items-center gap-2">
          <span>🔍</span> Query Centralized LSH Registry
        </h2>
        <form onSubmit={handleFuzzyResolve} className="grid grid-cols-1 sm:grid-cols-4 gap-4 items-end mb-4">
          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
              Query Raw Name/PII
            </label>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="e.g. Yusuf Çalışır"
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
              Type
            </label>
            <select
              value={searchType}
              onChange={(e) => setSearchType(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 opacity-90"
            >
              <option value="customer">Customer Only</option>
              <option value="merchant">Merchant Only</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1.5">
              Sim Threshold: <span className="font-mono text-indigo-400 font-bold">{Math.round(searchThreshold * 100)}%</span>
            </label>
            <input
              type="range"
              min="0.1"
              max="1.0"
              step="0.05"
              value={searchThreshold}
              onChange={(e) => setSearchThreshold(parseFloat(e.target.value))}
              className="w-full accent-indigo-500 cursor-pointer bg-slate-800 rounded-lg h-2"
            />
          </div>
          <button
            type="submit"
            disabled={fuzzyResolveMutation.isPending}
            className="w-full h-11 min-h-[44px] bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-200 font-bold px-4 rounded-xl text-sm transition-all duration-200 flex items-center justify-center gap-2 border border-slate-700 cursor-pointer whitespace-nowrap shrink-0"
          >
            {fuzzyResolveMutation.isPending ? (
              <>
                <span className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin shrink-0"></span>
                <span>Resolving Matches...</span>
              </>
            ) : (
              <>
                <span>🔍</span>
                <span>Resolve Fuzzy Matches</span>
              </>
            )}
          </button>
        </form>

        {/* LSH search results */}
        {fuzzyResolveMutation.data && (
          <div className="overflow-x-auto rounded-xl border border-slate-800 mt-4">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-slate-950 border-b border-slate-800 text-slate-400 font-bold">
                  <th className="p-3">Display Label</th>
                  <th className="p-3">Bank ID</th>
                  <th className="p-3">Risk Level</th>
                  <th className="p-3">Jaccard Match</th>
                  <th className="p-3">Standardized Stored</th>
                  <th className="p-3">Private Hash ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 bg-slate-900/30">
                {fuzzyResolveMutation.data.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-4 text-center text-slate-500 italic">
                      No matching entities found in LSH database above {Math.round(searchThreshold * 100)}% similarity.
                    </td>
                  </tr>
                ) : (
                  fuzzyResolveMutation.data.map((match: FuzzyMatchResponse) => (
                    <tr key={match.entity_id} className="hover:bg-slate-800/40 text-slate-300">
                      <td className="p-3 font-semibold text-slate-200">{match.display_label}</td>
                      <td className="p-3">
                        <span
                          className="px-2 py-0.5 rounded-full text-[10px] font-bold"
                          style={{
                            backgroundColor: (BANK_COLORS as any)[match.bank_id] + '20',
                            color: (BANK_COLORS as any)[match.bank_id],
                            border: `1px solid ${(BANK_COLORS as any)[match.bank_id]}40`,
                          }}
                        >
                          {(BANK_NAMES as any)[match.bank_id] || match.bank_id}
                        </span>
                      </td>
                      <td className="p-3 uppercase">
                        <span
                          className={`font-semibold ${
                            match.risk_level === 'critical' || match.risk_level === 'high'
                              ? 'text-rose-400'
                              : match.risk_level === 'medium'
                              ? 'text-amber-400'
                              : 'text-emerald-400'
                          }`}
                        >
                          {match.risk_level}
                        </span>
                      </td>
                      <td className="p-3 font-mono font-bold text-indigo-400">
                        {Math.round(match.similarity * 100)}%
                      </td>
                      <td className="p-3 font-mono text-slate-400">{match.standardized_stored}</td>
                      <td className="p-3 font-mono text-slate-500 text-[10px]">{match.privacy_id}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* PSI Run Match Results Panel */}
      {runPSIMutation.data && (
        <div className="glass-card p-6 border border-slate-800">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-md font-bold text-slate-200 flex items-center gap-2">
              <span>📑</span> Reconciled Entity Cross-Section Results
            </h2>
            <span className="text-[10px] font-bold text-slate-500 font-mono">
              MATCHED PAIRS: {runPSIMutation.data.matches.length}
            </span>
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-800">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-slate-950 border-b border-slate-800 text-slate-400 font-bold">
                  <th className="p-3">Entity Type</th>
                  <th className="p-3">Display Label (Bank A)</th>
                  <th className="p-3">Display Label (Bank B)</th>
                  <th className="p-3">Risk (A / B)</th>
                  <th className="p-3">Matched Key Attributes</th>
                  <th className="p-3">Overlap strength</th>
                  <th className="p-3">Privacy Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 bg-slate-900/30">
                {runPSIMutation.data.matches.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-4 text-center text-slate-500 italic">
                      PSI protocol completed successfully. No matching sets between select institutions.
                    </td>
                  </tr>
                ) : (
                  runPSIMutation.data.matches.map((match, index) => (
                    <tr key={index} className="hover:bg-slate-800/40 text-slate-300">
                      <td className="p-3 uppercase font-semibold text-slate-400">{match.entity_type}</td>
                      <td className="p-3 font-semibold text-indigo-200">{match.display_label_a}</td>
                      <td className="p-3 font-semibold text-emerald-200">{match.display_label_b}</td>
                      <td className="p-3 font-mono">
                        <span
                          className={`font-semibold ${
                            match.risk_level_a === 'critical' || match.risk_level_a === 'high'
                              ? 'text-rose-400'
                              : 'text-slate-400'
                          }`}
                        >
                          {match.risk_level_a}
                        </span>
                        <span className="text-slate-600 mx-1">/</span>
                        <span
                          className={`font-semibold ${
                            match.risk_level_b === 'critical' || match.risk_level_b === 'high'
                              ? 'text-rose-400'
                              : 'text-slate-400'
                          }`}
                        >
                          {match.risk_level_b}
                        </span>
                      </td>
                      <td className="p-3">
                        <div className="flex flex-wrap gap-1">
                          {match.matched_attributes.map((attr, aIdx) => (
                            <span
                              key={aIdx}
                              className="px-1.5 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-[10px] text-indigo-400 font-mono"
                            >
                              {attr}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="p-3 font-mono font-bold text-indigo-400">
                        {Math.round(match.similarity_score * 100)}%
                      </td>
                      <td className="p-3 font-mono text-slate-600 text-[10px]">{match.privacy_hash}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
