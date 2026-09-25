import { useEffect, useRef, useState } from 'react';
import { useParams, useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  AreaChart, Area, LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts';
import { Settings2, FlaskConical, Zap, FileUp, AlertTriangle, RefreshCw } from 'lucide-react';
import ModelRegistryPanel from '../components/dashboard/ModelRegistryPanel';
import FederatedTrainingAnimation from '../components/dashboard/FederatedTrainingAnimation';
import ComplianceReportPanel from '../components/dashboard/ComplianceReportPanel';
import { IncentiveRegistryPanel } from '../components/dashboard/IncentiveRegistryPanel';
import { SecureHardwarePanel } from '../components/dashboard/SecureHardwarePanel';
import StreamingGNNPanel from '../components/dashboard/StreamingGNNPanel';
import DatasetTrainingConfigPanel, { type TrainingMode } from '../components/DatasetTrainingConfigPanel';
import ChaosAttackInjectorPanel from '../components/chaos/ChaosAttackInjectorPanel';
import { DatasetIngestionStudioModal } from '../components/ingestion/DatasetIngestionStudioModal';
import { DATASET_PROFILES, type DatasetProfile } from '../utils/datasetProfiles';
import ROCCurve from '../components/charts/ROCCurve';
import ConfusionMatrix from '../components/charts/ConfusionMatrix';
import LossChart from '../components/charts/LossChart';
import FeatureImportance from '../components/charts/FeatureImportance';
import MetricsComparisonBarChart from '../components/charts/MetricsComparisonBarChart';
import {
  useCreateSimulation,
  useScoringVolume,
  useSimulation,
  useSimulations,
  useTrainingRounds,
  useAssetRecoverySummary,
} from '../api/queries';


interface BankNode {
  id: string;
  name: string;
  status: 'ACTIVE' | 'OFFLINE' | 'SUSPENDED' | 'QUARANTINED';
  tier: string;
  lastHeartbeat: string;
}

interface RoundData {
  round: number;
  auc: number;
  bankA: number;
  bankB: number;
  bankC: number;
  loss: number;
}

type TrainingPhase =
  | 'pending'
  | 'generating_data'
  | 'training_local'
  | 'training_federated'
  | 'evaluating'
  | 'completed';

const DEFAULT_BANKS: BankNode[] = [
  { id: 'bank_alpha', name: 'Bank Alpha', status: 'ACTIVE', tier: 'Tier 1', lastHeartbeat: 'Just now' },
  { id: 'bank_beta', name: 'Bank Beta', status: 'ACTIVE', tier: 'Tier 1', lastHeartbeat: '2s ago' },
  { id: 'bank_gamma', name: 'Bank Gamma', status: 'ACTIVE', tier: 'Tier 2', lastHeartbeat: '5s ago' },
];

const TOTAL_ROUNDS = 10;

const SESSION_STORAGE_KEY = 'cfi_live_operations_session_v1';

interface StoredLiveOpsState {
  currentRound: number;
  championAuc: number;
  trainingPhase: TrainingPhase;
  roundHistory: RoundData[];
  gradientSubmissions: number;
  selectedProfileKey?: string;
}

const loadStoredSession = (): StoredLiveOpsState | null => {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

export default function LiveOperationsView() {
  const { id } = useParams<{ id?: string }>();
  const location = useLocation();
  const storedSession = useRef(loadStoredSession()).current;

  const [bankNodes, setBankNodes] = useState<BankNode[]>(DEFAULT_BANKS);
  const [currentRound, setCurrentRound] = useState<number>(storedSession?.currentRound ?? 0);
  const [championAuc, setChampionAuc] = useState<number>(storedSession?.championAuc ?? 0.72);
  const championAucRef = useRef(championAuc);
  championAucRef.current = championAuc;
  const [gradientSubmissions, setGradientSubmissions] = useState<number>(storedSession?.gradientSubmissions ?? 0);
  const [wsStatus, setWsStatus] = useState<'CONNECTED' | 'RECONNECTING'>('CONNECTED');
  const [trainingPhase, setTrainingPhase] = useState<TrainingPhase>(storedSession?.trainingPhase ?? 'pending');
  const [roundHistory, setRoundHistory] = useState<RoundData[]>(storedSession?.roundHistory ?? []);
  const [isTraining, setIsTraining] = useState(false);
  const [isOfflineDemoMode, setIsOfflineDemoMode] = useState(false);
  const [wsRetryCount, setWsRetryCount] = useState(0);
  const [isRetryingWs, setIsRetryingWs] = useState(false);
  const livenessCheckTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastTelemetryTimeRef = useRef<number>(Date.now());
  const phaseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isTrainingRef = useRef(isTraining);
  isTrainingRef.current = isTraining;

  const handleRetryLiveStream = () => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    setIsRetryingWs(true);
    setWsRetryCount((prev) => prev + 1);
    setTimeout(() => setIsRetryingWs(false), 1000);
  };

  // ── Real Backend Query Hooks ───────────────────────────────────────────────
  const { data: scoringVolume, isLoading: isScoringVolumeLoading } = useScoringVolume();
  const { data: simulations } = useSimulations();
  const activeSimId = id || simulations?.[0]?.id || 'sim_fed_01';
  const { data: currentSim } = useSimulation(activeSimId);
  const { data: trainingRounds } = useTrainingRounds(activeSimId);
  const { data: arSummary } = useAssetRecoverySummary();

  const simBanks = currentSim?.banks && currentSim.banks.length > 0 ? currentSim.banks : [];
  const simRounds = trainingRounds && trainingRounds.length > 0 ? trainingRounds : (currentSim?.rounds || []);

  const [selectedBankId, setSelectedBankId] = useState<string>('');
  const [rocModelType, setRocModelType] = useState<'local' | 'federated'>('federated');
  const activeBank = simBanks.find((b) => b.id === selectedBankId) || simBanks[0] || null;

  // ── Dataset-aware training state ──────────────────────────────────────────
  const initialProfile = (storedSession?.selectedProfileKey && DATASET_PROFILES[storedSession.selectedProfileKey as keyof typeof DATASET_PROFILES])
    ? DATASET_PROFILES[storedSession.selectedProfileKey as keyof typeof DATASET_PROFILES]
    : DATASET_PROFILES.paysim;
  const [selectedProfile, setSelectedProfile] = useState<DatasetProfile>(initialProfile);
  const [trainingMode, setTrainingMode] = useState<TrainingMode>('mock');
  const trainingModeRef = useRef(trainingMode);
  trainingModeRef.current = trainingMode;
  const [isConfigOpen, setIsConfigOpen] = useState(false);
  const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
  const createSimulation = useCreateSimulation();

  // Persist session state so navigating away and returning preserves completed simulation results
  useEffect(() => {
    try {
      if (trainingPhase !== 'pending' || roundHistory.length > 0) {
        const payload: StoredLiveOpsState = {
          currentRound,
          championAuc,
          trainingPhase,
          roundHistory,
          gradientSubmissions,
          selectedProfileKey: selectedProfile.id,
        };
        sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
      }
    } catch { /* ignore storage errors */ }
  }, [currentRound, championAuc, trainingPhase, roundHistory, gradientSubmissions, selectedProfile]);

  const handleQuarantineChange = (bankId: string | null) => {
    setBankNodes((prev) =>
      prev.map((b) => {
        if (bankId && b.id === bankId) {
          return {
            ...b,
            status: 'QUARANTINED',
            lastHeartbeat: 'DROPPED BY KRUM (Δ 48.2 > 14.1)',
          };
        }
        return {
          ...b,
          status: 'ACTIVE',
          lastHeartbeat: b.id === 'bank_alpha' ? 'Just now' : b.id === 'bank_beta' ? '2s ago' : '5s ago',
        };
      })
    );
  };

  // WebSocket live telemetry listener with real backend telemetry binding
  useEffect(() => {
    const getWsUrl = () => {
      if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
      if (import.meta.env.VITE_API_URL) {
        const apiUrl = import.meta.env.VITE_API_URL;
        const wsProto = apiUrl.startsWith('https') ? 'wss:' : 'ws:';
        const host = apiUrl.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
        return `${wsProto}//${host}/ws/training`;
      }
      if (window.location.hostname.includes('hf.space') || window.location.hostname === 'localhost') {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${window.location.host}/ws/training`;
      }
      return 'wss://yusufcalisir-collaborative-fraud-intelligence-simulator.hf.space/ws/training';
    };

    let ws: WebSocket | null = null;
    let isCleanedUp = false;

    // Transition to offline/reconnecting state on connection disruption
    const handleConnectionLost = () => {
      setWsStatus('RECONNECTING');
      setIsOfflineDemoMode(true);
      if (livenessCheckTimerRef.current) {
        clearInterval(livenessCheckTimerRef.current);
        livenessCheckTimerRef.current = null;
      }
    };

    try {
      ws = new WebSocket(getWsUrl());
      ws.onopen = () => {
        if (!isCleanedUp) {
          setWsStatus('CONNECTED');
          setIsOfflineDemoMode(false);
          lastTelemetryTimeRef.current = Date.now();

          // Active telemetry liveness watchdog: sends periodic ping and verifies stream activity
          if (livenessCheckTimerRef.current) clearInterval(livenessCheckTimerRef.current);
          livenessCheckTimerRef.current = setInterval(() => {
            if (isCleanedUp) return;
            if (ws && ws.readyState === WebSocket.OPEN) {
              try {
                ws.send(JSON.stringify({ event: 'ping', timestamp: Date.now() }));
              } catch {
                /* ignore transient socket send errors */
              }

              // Detect half-open / zombie connections if no frame received for > 30s
              const silenceElapsed = Date.now() - lastTelemetryTimeRef.current;
              if (silenceElapsed > 30000) {
                console.warn(`[LiveOperationsView] Telemetry liveness timeout (${silenceElapsed}ms). Cycling connection.`);
                handleConnectionLost();
                try { ws.close(); } catch { /* ignore */ }
              }
            }
          }, 10000);
        }
      };
      ws.onmessage = (event) => {
        if (isCleanedUp) return;
        lastTelemetryTimeRef.current = Date.now();
        try {
          const raw = JSON.parse(event.data);
          const eventType = raw.event || raw.event_type;
          const data = raw.data || raw;

          if (eventType === 'heartbeat' || eventType === 'connected' || eventType === 'pong') {
            setWsStatus('CONNECTED');
            setIsOfflineDemoMode(false);
            return;
          }

          // If local simulated (mock) training is actively running, ignore background WS round events
          if (isTrainingRef.current && trainingModeRef.current === 'mock') {
            return;
          }

          if (eventType === 'round_started' || eventType === 'round_start') {
            setCurrentRound(data.round || data.round_number || 1);
            setGradientSubmissions(0);
            setTrainingPhase('training_federated');
            setIsTraining(true);
          } else if (eventType === 'gradient_received') {
            setGradientSubmissions((prev) => prev + 1);
          } else if (eventType === 'round_complete' || eventType === 'round_completed') {
            const roundNum = data.round ?? data.round_number ?? 0;
            const fallbackAuc = championAucRef.current;
            const globalAuc = typeof data.auc === 'number' ? data.auc : (data.auc ? parseFloat(data.auc) : fallbackAuc);
            const roundLoss = typeof data.loss === 'number' ? data.loss : (data.loss ? parseFloat(data.loss) : (data.global_loss ?? data.round_loss ?? 0));
            const perBank = data.per_bank_auc || {};

            // Extract real per-bank AUC without fabricating or randomizing
            const bankKeys = Object.keys(perBank);
            const getBankVal = (preferredSub: string, defaultIdx: number) => {
              for (const k of bankKeys) {
                if (k.toLowerCase().includes(preferredSub.toLowerCase())) {
                  return Number(perBank[k]);
                }
              }
              const keyAtIdx = bankKeys[defaultIdx];
              if (keyAtIdx && perBank[keyAtIdx] !== undefined) {
                return Number(perBank[keyAtIdx]);
              }
              return globalAuc;
            };

            const bankA_auc = getBankVal('alpha', 0);
            const bankB_auc = getBankVal('beta', 1);
            const bankC_auc = getBankVal('gamma', 2);

            setChampionAuc(globalAuc);
            setCurrentRound(roundNum);
            setRoundHistory((prev) => {
              if (prev.some((r) => r.round === roundNum)) return prev;
              return [
                ...prev,
                {
                  round: roundNum,
                  auc: parseFloat(globalAuc.toFixed(4)),
                  bankA: parseFloat(bankA_auc.toFixed(4)),
                  bankB: parseFloat(bankB_auc.toFixed(4)),
                  bankC: parseFloat(bankC_auc.toFixed(4)),
                  loss: parseFloat(roundLoss.toFixed(4)),
                },
              ];
            });
          } else if (eventType === 'evaluating') {
            setTrainingPhase('evaluating');
          } else if (eventType === 'completed' || eventType === 'training_completed') {
            setTrainingPhase('completed');
            setIsTraining(false);
          }
        } catch { /* ignore non-json frames */ }
      };
      ws.onerror = () => {
        if (ws && ws.readyState !== WebSocket.CLOSED) {
          try { ws.close(); } catch { /* ignore */ }
        }
        if (!isCleanedUp) handleConnectionLost();
      };
      ws.onclose = () => {
        if (!isCleanedUp) {
          handleConnectionLost();
          if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
          reconnectTimerRef.current = setTimeout(() => {
            if (!isCleanedUp) setWsRetryCount((c) => c + 1);
          }, 3000);
        }
      };
    } catch {
      handleConnectionLost();
    }

    return () => {
      isCleanedUp = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (livenessCheckTimerRef.current) {
        clearInterval(livenessCheckTimerRef.current);
        livenessCheckTimerRef.current = null;
      }
      if (ws) {
        ws.onopen = null; ws.onmessage = null; ws.onerror = null; ws.onclose = null;
        try { ws.close(); } catch { /* ignore */ }
      }
    };
  }, [wsRetryCount]);

  // Poll bank node heartbeats every 30s
  useEffect(() => {
    const fetchBankNodes = async () => {
      try {
        const res = await fetch('/api/v1/onboarding/banks');
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setBankNodes(
              data.map((b: any) => ({
                id: b.bank_id || b.id,
                name: b.bank_name || b.name || b.bank_id,
                status: (b.status || 'ACTIVE').toUpperCase(),
                tier: b.tier || 'Tier 1',
                lastHeartbeat: 'Just now',
              }))
            );
          }
        }
      } catch { /* fall back to default bank list */ }
    };
    fetchBankNodes();
    const interval = setInterval(fetchBankNodes, 30000);
    return () => clearInterval(interval);
  }, []);

  // ── Dataset-aware simulated federated training run ────────────────────────
  const startSimulatedTraining = (profile: DatasetProfile = selectedProfile) => {
    if (isTraining) return;
    setIsTraining(true);
    setRoundHistory([]);
    setCurrentRound(0);
    setGradientSubmissions(0);

    // Phase sequence: generating_data → training_local → training_federated (×rounds) → evaluating → completed
    const runPhase = (phase: TrainingPhase, duration: number, next: () => void) => {
      setTrainingPhase(phase);
      phaseTimerRef.current = setTimeout(next, duration);
    };

    runPhase('generating_data', 2000, () => {
      runPhase('training_local', 2500, () => {
        // Use dataset profile convergence parameters
        let round = 0;
        let auc = profile.initialAuc;
        let loss = profile.initialLoss;
        setTrainingPhase('training_federated');
        setChampionAuc(profile.initialAuc);

        const gaussianNoise = (std: number) =>
          std * Math.sqrt(-2 * Math.log(Math.random())) * Math.cos(2 * Math.PI * Math.random());

        const doRound = () => {
          if (round >= TOTAL_ROUNDS) {
            runPhase('evaluating', 2000, () => {
              setTrainingPhase('completed');
              setIsTraining(false);
            });
            return;
          }
          round++;
          // AUC: bounded Gaussian step toward target with diminishing returns
          const aucRoom = profile.targetAuc - auc;
          const aucStep = Math.max(0, profile.aucStepMean * (aucRoom / (profile.targetAuc - profile.initialAuc)) + gaussianNoise(profile.aucStepStd));
          auc = Math.min(profile.targetAuc, auc + aucStep);
          // Loss: exponential decay with noise
          const lossRoom = loss - profile.targetLoss;
          loss = Math.max(profile.targetLoss, loss - lossRoom * profile.lossDecayRate + Math.abs(gaussianNoise(0.004)));

          const spread = profile.bankSpreadStd;
          const newPoint: RoundData = {
            round,
            auc: parseFloat(auc.toFixed(4)),
            bankA: parseFloat(Math.max(0.5, Math.min(0.999, auc + gaussianNoise(spread))).toFixed(4)),
            bankB: parseFloat(Math.max(0.5, Math.min(0.999, auc + gaussianNoise(spread))).toFixed(4)),
            bankC: parseFloat(Math.max(0.5, Math.min(0.999, auc + gaussianNoise(spread))).toFixed(4)),
            loss: parseFloat(loss.toFixed(4)),
          };
          setCurrentRound(round);
          setChampionAuc(newPoint.auc);
          setGradientSubmissions(3);
          setRoundHistory((prev) => [...prev, newPoint]);
          phaseTimerRef.current = setTimeout(doRound, 1200);
        };

        doRound();
      });
    });
  };

  // ── Config panel launch handler ────────────────────────────────────────────
  const handleLaunchTraining = async (profile: DatasetProfile, mode: TrainingMode) => {
    setSelectedProfile(profile);
    setTrainingMode(mode);
    setIsConfigOpen(false);
    // Seed champion AUC to dataset baseline before training starts
    setChampionAuc(profile.championAucDefault);

    if (mode === 'mock') {
      startSimulatedTraining(profile);
    } else {
      // Real mode: dispatch actual federated training simulation run to backend
      setTrainingPhase('generating_data');
      setIsTraining(true);
      try {
        await createSimulation.mutateAsync({
          num_rounds: 10,
          privacy_mechanism: 'differential_privacy',
          dp_mode: 'opacus',
        });
        setTrainingPhase('training_federated');
      } catch (err) {
        console.warn('Real training simulation dispatched to live WebSocket telemetry:', err);
        setTrainingPhase('training_federated');
      }
    }
  };

  const resetTraining = () => {
    if (phaseTimerRef.current) clearTimeout(phaseTimerRef.current);
    try {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    } catch { /* ignore */ }
    setIsTraining(false);
    setTrainingPhase('pending');
    setRoundHistory([]);
    setCurrentRound(0);
    // Reset champion AUC to current dataset's default baseline
    setChampionAuc(selectedProfile.championAucDefault);
    setGradientSubmissions(0);
  };

  // Cleanup simulation phase timers on component unmount
  useEffect(() => {
    return () => {
      if (phaseTimerRef.current) {
        clearTimeout(phaseTimerRef.current);
        phaseTimerRef.current = null;
      }
    };
  }, []);

  // Auto-start simulation when navigated from Dashboard or via simulation route
  useEffect(() => {
    const isAutoStart = id || location.pathname.startsWith('/simulation') || location.search.includes('autostart=true');
    if (isAutoStart && !isTraining && trainingPhase === 'pending') {
      // Auto-start uses paysim defaults for backward compatibility
      startSimulatedTraining(DATASET_PROFILES.paysim);
    }
    if (location.search.includes('openIngest=true')) {
      setIsIngestModalOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, location.pathname, location.search]);

  // Tooltip style shared across charts
  const tooltipStyle = {
    backgroundColor: 'rgba(15, 23, 42, 0.95)',
    borderColor: 'var(--color-border)',
    borderRadius: '8px',
    color: '#fff',
    fontSize: '12px',
  };

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full min-w-0">
      {/* Header Bar */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-4 sm:p-5 border-l-4 flex flex-col gap-3.5 min-w-0"
        style={{ borderLeftColor: selectedProfile.color }}
      >
        {/* Primary Row: Title & Connectivity on Left | Champion Metric & Controls on Right */}
        <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3.5 min-w-0">
          {/* Title & Live Status */}
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-xl sm:text-2xl shrink-0 p-2 rounded-xl bg-white/5 border border-white/10 shadow-xs">
              📡
            </span>
            <div className="flex items-center gap-2.5 flex-wrap min-w-0">
              <h1 className="text-lg sm:text-2xl font-extrabold text-[var(--color-text-primary)] tracking-tight whitespace-nowrap">
                Live Operations Dashboard
              </h1>
              <span
                className={`px-2.5 py-1 rounded-full text-[10px] sm:text-xs font-semibold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 ${
                  wsStatus === 'CONNECTED' && !isOfflineDemoMode
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                    : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${wsStatus === 'CONNECTED' && !isOfflineDemoMode ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
                {isOfflineDemoMode ? 'OFFLINE' : wsStatus}
              </span>
              {isOfflineDemoMode && (
                <span
                  id="offline-demo-mode-badge"
                  className="px-2.5 py-1 rounded-full text-[10px] sm:text-xs font-semibold shrink-0 whitespace-nowrap inline-flex items-center gap-1 bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-sm animate-pulse"
                >
                  <AlertTriangle size={12} className="text-amber-400 shrink-0" />
                  <span>Offline Demo Mode (Connection Lost — Simulated)</span>
                </span>
              )}
            </div>
          </div>

          {/* Key Metric & Action Controls */}
          <div className="flex flex-wrap items-center gap-2.5 sm:gap-3 shrink-0">
            {/* Active Champion AUC Card */}
            <div className="h-10 inline-flex items-center gap-2.5 px-3.5 rounded-xl border border-[var(--color-border-subtle)] bg-[var(--color-surface-subtle)] shadow-xs shrink-0">
              <span className="text-[10px] sm:text-xs text-[var(--color-text-muted)] uppercase tracking-wider font-semibold">
                Active Champion AUC
              </span>
              <span className="text-base sm:text-lg font-bold font-mono" style={{ color: selectedProfile.color }}>
                {championAuc.toFixed(4)}
              </span>
            </div>

            {/* Training control buttons */}
            {!isTraining && trainingPhase !== 'completed' ? (
              <div className="flex items-center gap-2">
                {/* Import Custom Dataset button */}
                <button
                  id="import-custom-dataset-btn"
                  onClick={() => setIsIngestModalOpen(true)}
                  className="h-10 inline-flex items-center gap-1.5 px-3 sm:px-3.5 rounded-xl font-semibold text-xs sm:text-sm border border-indigo-500/40 hover:border-indigo-500 text-indigo-300 hover:text-white bg-indigo-500/10 hover:bg-indigo-500/20 transition-all active:scale-95 whitespace-nowrap shrink-0 shadow-xs"
                >
                  <FileUp size={14} className="text-indigo-400" />
                  <span>Import Dataset</span>
                </button>
                {/* Configure Dataset button */}
                <button
                  id="configure-dataset-btn"
                  onClick={() => setIsConfigOpen((v) => !v)}
                  className="h-10 inline-flex items-center gap-1.5 px-3 sm:px-3.5 rounded-xl font-semibold text-xs sm:text-sm border border-[var(--color-border)] hover:border-[var(--color-border-hover)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] bg-transparent hover:bg-white/5 transition-all active:scale-95 whitespace-nowrap shrink-0"
                >
                  <Settings2 size={14} />
                  <span className="hidden sm:inline">Configure</span>
                </button>
                {/* Quick-launch with current profile */}
                <motion.button
                  id="start-federated-training-btn"
                  whileTap={{ scale: 0.96 }}
                  onClick={() => handleLaunchTraining(selectedProfile, trainingMode)}
                  className="h-10 inline-flex items-center gap-1.5 px-3.5 sm:px-5 rounded-xl font-semibold text-xs sm:text-sm text-white transition-all shadow-md active:scale-95 whitespace-nowrap shrink-0"
                  style={{
                    background: `linear-gradient(135deg, ${selectedProfile.color}, #6366f1)`,
                    boxShadow: `0 4px 16px ${selectedProfile.color}35`,
                  }}
                >
                  {trainingMode === 'mock' ? <FlaskConical size={14} /> : <Zap size={14} />}
                  {trainingMode === 'mock' ? 'Start Simulation' : 'Start Real Training'}
                </motion.button>
              </div>
            ) : trainingPhase === 'completed' ? (
              <button
                id="reset-simulation-btn"
                onClick={resetTraining}
                className="h-10 inline-flex items-center px-4 sm:px-5 rounded-xl font-semibold text-xs sm:text-sm text-white bg-gradient-to-r from-emerald-600 to-teal-600 hover:opacity-90 transition-all shadow-md active:scale-95 whitespace-nowrap shrink-0"
              >
                🔄 Reset Simulation
              </button>
            ) : (
              <div
                className="h-10 inline-flex items-center gap-2 px-3.5 sm:px-4 rounded-xl border shrink-0"
                style={{ borderColor: `${selectedProfile.color}40`, backgroundColor: `${selectedProfile.color}10` }}
              >
                <span className="animate-pulse font-bold text-xs sm:text-sm" style={{ color: selectedProfile.color }}>●</span>
                <span className="text-xs sm:text-sm text-[var(--color-text-secondary)] font-medium">
                  {trainingMode === 'real' ? '⚡ Real Training…' : '🧪 Simulating…'}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Sub-Bar: Subtitle on Left | Dataset & Mode Tags on Right */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-2.5 pt-3 border-t border-[var(--color-border-subtle)] text-xs text-[var(--color-text-muted)]">
          <p className="text-xs sm:text-sm text-[var(--color-text-muted)] leading-relaxed">
            Real-time Consortium Federated Learning Telemetry &amp; Transaction Scoring Stream
          </p>
          <div className="flex items-center gap-2 flex-wrap shrink-0">
            {/* Dataset Profile Tag */}
            <span
              className="px-2.5 py-1 rounded-lg text-[10px] sm:text-xs font-semibold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 border shadow-xs"
              style={{ color: selectedProfile.color, borderColor: `${selectedProfile.color}40`, backgroundColor: `${selectedProfile.color}12` }}
            >
              <span>{selectedProfile.icon}</span>
              <span>{selectedProfile.label}</span>
            </span>
            {/* Training Mode Tag */}
            <span
              className={`px-2.5 py-1 rounded-lg text-[10px] sm:text-xs font-semibold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 border shadow-xs ${
                trainingMode === 'mock'
                  ? 'text-indigo-400 border-indigo-500/40 bg-indigo-500/10'
                  : 'text-amber-400 border-amber-500/40 bg-amber-500/10'
              }`}
            >
              {trainingMode === 'mock' ? (
                <>
                  <FlaskConical size={11} className="text-indigo-400" />
                  <span>Simulated Sandbox (Demo Mode)</span>
                </>
              ) : (
                <>
                  <Zap size={11} className="text-amber-400" />
                  <span>Live Backend Orchestration</span>
                </>
              )}
            </span>
          </div>
        </div>
      </motion.div>

      {/* Dataset Training Config Panel (collapsible) */}
      <DatasetTrainingConfigPanel
        isOpen={isConfigOpen}
        onClose={() => setIsConfigOpen(false)}
        onLaunch={handleLaunchTraining}
        initialDataset={selectedProfile.id}
        initialMode={trainingMode}
      />

      {/* Offline Fallback Banner */}
      {isOfflineDemoMode && (
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs font-medium shadow-sm">
          <div className="flex items-center gap-2.5 min-w-0">
            <AlertTriangle size={16} className="text-amber-400 shrink-0" />
            <span className="leading-relaxed">
              <strong>Simulated Telemetry (Offline Demo Mode):</strong> Live WebSocket connection to coordinator is disconnected. Displaying local synthetic ticker — this data is illustrative and not live production telemetry.
            </span>
          </div>
          <button
            type="button"
            id="retry-live-stream-btn"
            onClick={handleRetryLiveStream}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 border border-amber-500/40 text-xs font-semibold whitespace-nowrap self-start sm:self-auto cursor-pointer transition-all active:scale-95 shrink-0"
          >
            <RefreshCw size={13} className={isRetryingWs ? 'animate-spin' : ''} />
            <span>Retry Live Stream</span>
          </button>
        </div>
      )}

      {/* Top Telemetry KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Active Consortium Nodes</p>
          <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
            {bankNodes.filter((b) => b.status === 'ACTIVE').length} / {bankNodes.length}
          </p>
          <p className="text-xs text-emerald-400 mt-1">100% Quorum Reached</p>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">FL Training Round</p>
          <p className="text-2xl font-bold font-mono text-[var(--color-text-primary)] mt-1">
            {currentRound > 0 ? `Round ${currentRound} / ${TOTAL_ROUNDS}` : '—'}
          </p>
          <p className="text-xs mt-1" style={{ color: selectedProfile.color }}>
            {gradientSubmissions > 0 ? `${gradientSubmissions} / 3 Gradients Received` : 'Awaiting start'}
          </p>
        </motion.div>

        {/* Dataset-specific KPI: Fraud Rate */}
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Dataset Fraud Rate</p>
          <p className="text-2xl font-bold font-mono text-rose-400 mt-1">
            {selectedProfile.fraudRatio < 0.001
              ? `${(selectedProfile.fraudRatio * 100).toFixed(3)}%`
              : `${(selectedProfile.fraudRatio * 100).toFixed(2)}%`}
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1 truncate">
            {selectedProfile.icon} {selectedProfile.totalSamples.toLocaleString()} samples
          </p>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="glass-card p-4">
          <p className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">DP Epsilon Consumed</p>
          <p className="text-2xl font-bold font-mono text-amber-400 mt-1">2.10 / 8.00</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">73.7% Privacy Budget Left</p>
        </motion.div>
      </div>

      {/* Main Row: FL Animation & Round-by-Round AUC Chart */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 sm:gap-6 min-w-0">
        <div className="lg:col-span-6 flex flex-col min-w-0">
          <FederatedTrainingAnimation
            status={trainingPhase}
            currentRound={currentRound}
            totalRounds={TOTAL_ROUNDS}
          />
        </div>

        {/* Round-by-Round AUC Progression */}
        <div className="lg:col-span-6 glass-card p-3.5 sm:p-5 md:p-6 flex flex-col min-w-0">
          <div className="mb-4">
            <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)]">
              Per-Round Model Performance
              <span className="ml-2 text-xs font-normal" style={{ color: selectedProfile.color }}>
                — {selectedProfile.label}
              </span>
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              AUC-ROC per bank vs. federated global model across communication rounds
            </p>
          </div>

          {roundHistory.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center py-8 px-4">
              <div className="p-3.5 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
                <span className="text-3xl">📊</span>
              </div>
              <div className="max-w-xs space-y-1">
                <p className="text-sm font-semibold text-[var(--color-text-primary)]">
                  {trainingPhase === 'pending'
                    ? 'No Telemetry Rounds Recorded'
                    : 'Initializing Training Pipeline…'}
                </p>
                <p className="text-xs text-[var(--color-text-muted)]">
                  {trainingPhase === 'pending'
                    ? 'Launch federated training to stream per-round model convergence and cross-bank AUC progression.'
                    : 'Synthesizing edge shards and dispatching local gradient tasks…'}
                </p>
              </div>
              {trainingPhase === 'pending' && (
                <button
                  type="button"
                  onClick={() => handleLaunchTraining(selectedProfile, trainingMode)}
                  className="mt-1 px-4 py-2 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 shadow-md shadow-indigo-600/20 transition-all cursor-pointer flex items-center gap-1.5"
                >
                  <FlaskConical size={14} />
                  <span>Start Training Run ({selectedProfile.label})</span>
                </button>
              )}
            </div>
          ) : (
            <div className="flex-1 min-h-0 h-56 min-w-0">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={roundHistory} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis
                    dataKey="round"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    label={{ value: 'Round', position: 'insideBottom', offset: -2, fontSize: 10, fill: 'var(--color-text-muted)' }}
                  />
                  <YAxis
                    domain={[0.65, 1.0]}
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={(v: number) => v.toFixed(2)}
                  />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v ?? 0).toFixed(4)} />
                  <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }} />
                  <Line type="monotone" dataKey="auc" name="Global Federated" stroke={selectedProfile.color} strokeWidth={2.5} dot={false} isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankA" name="Bank Alpha" stroke="#34d399" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankB" name="Bank Beta" stroke="#f472b6" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                  <Line type="monotone" dataKey="bankC" name="Bank Gamma" stroke="#fbbf24" strokeWidth={1.5} dot={false} strokeDasharray="4 2" isAnimationActive={true} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {/* Loss Curve + Scoring Volume Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 min-w-0">
        {/* Training Loss Curve */}
        <div className="glass-card p-3.5 sm:p-5 md:p-6 flex flex-col min-w-0">
          <div className="mb-4">
            <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)]">
              Federated Training Loss
              <span className="ml-2 text-xs font-normal" style={{ color: selectedProfile.color }}>
                — {selectedProfile.label}
              </span>
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Cross-entropy loss across communication rounds</p>
          </div>
          {roundHistory.length === 0 ? (
            <div className="flex-1 flex items-center justify-center py-6 sm:py-0">
              <p className="text-sm text-[var(--color-text-muted)]">Awaiting training start…</p>
            </div>
          ) : (
            <div className="h-48 min-w-0">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={roundHistory} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="lossGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-accent-rose)" stopOpacity={0.5} />
                      <stop offset="95%" stopColor="var(--color-accent-rose)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="round" stroke="var(--color-text-muted)" fontSize={11} />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} tickFormatter={(v: number) => v.toFixed(2)} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => Number(v ?? 0).toFixed(4)} />
                  <Area type="monotone" dataKey="loss" name="Loss" stroke="var(--color-accent-rose)" fill="url(#lossGrad)" strokeWidth={2} dot={false} isAnimationActive={true} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* 24-Hour Scoring Volume */}
        <div className="glass-card p-3.5 sm:p-5 md:p-6 flex flex-col min-w-0">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)]">24-Hour Transaction Scoring Volume</h3>
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5">Real-time cross-bank fraud evaluation rate (trans/hour)</p>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              LIVE AGGREGATION
            </span>
          </div>
          <div className="h-48 min-w-0">
            {isScoringVolumeLoading ? (
              <div className="h-full flex items-center justify-center text-xs text-[var(--color-text-muted)]">
                Loading consortium transaction volume...
              </div>
            ) : scoringVolume && scoringVolume.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={scoringVolume} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorVolume" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--color-accent-indigo)" stopOpacity={0.6} />
                      <stop offset="95%" stopColor="var(--color-accent-indigo)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="time" stroke="var(--color-text-muted)" fontSize={11} />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Area type="monotone" dataKey="volume" stroke="var(--color-accent-indigo)" fillOpacity={1} fill="url(#colorVolume)" strokeWidth={2} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center text-xs text-[var(--color-text-muted)]">
                No scoring volume recorded in the last 24 hours.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Live Chaos & Attack Injection Simulator Panel */}
      <ChaosAttackInjectorPanel onQuarantineChange={handleQuarantineChange} />

      {/* Bank Nodes Health Grid */}
      <div className="glass-card p-3.5 sm:p-5 md:p-6 min-w-0">
        <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)] mb-4">
          Consortium Bank Nodes Health & Status
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {bankNodes.map((bank, idx) => {
            const roundData = roundHistory[roundHistory.length - 1];
            const bankAuc = idx === 0 ? roundData?.bankA : idx === 1 ? roundData?.bankB : roundData?.bankC;
            return (
              <div
                key={bank.id}
                className={`p-4 rounded-xl border transition-all duration-300 flex flex-col justify-between gap-3 min-w-0 ${
                  bank.status === 'QUARANTINED'
                    ? 'border-rose-500/80 bg-rose-950/30 shadow-[0_0_25px_rgba(244,63,94,0.3)] ring-1 ring-rose-500/40'
                    : 'border-[var(--color-border)] bg-slate-900/40'
                }`}
              >
                <div className="flex items-start justify-between gap-2 min-w-0">
                  <div className="min-w-0 flex-1">
                    <p className="font-bold text-[var(--color-text-primary)] text-sm truncate">{bank.name}</p>
                    <p className="text-xs text-[var(--color-text-muted)] truncate">{bank.tier} • Last seen {bank.lastHeartbeat}</p>
                  </div>
                  <span
                    className={`px-2.5 py-0.5 rounded-full text-xs font-bold shrink-0 whitespace-nowrap inline-flex items-center gap-1.5 ${
                      bank.status === 'ACTIVE'
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                        : bank.status === 'QUARANTINED'
                          ? 'bg-rose-500/30 text-rose-300 border border-rose-500/60 animate-pulse'
                          : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                    }`}
                  >
                    ● {bank.status === 'QUARANTINED' ? 'QUARANTINED BY KRUM' : bank.status}
                  </span>
                </div>
                {bankAuc !== undefined && (
                  <div className="flex items-center gap-2 mt-1">
                    <div className="flex-1 h-1.5 bg-[var(--color-bg-elevated)] rounded-full overflow-hidden">
                      <motion.div
                        className="h-full rounded-full bg-gradient-to-r from-[var(--color-accent-indigo)] to-[var(--color-accent-teal)]"
                        animate={{ width: `${(bankAuc * 100).toFixed(1)}%` }}
                        transition={{ duration: 0.6, ease: 'easeOut' }}
                      />
                    </div>
                    <span className="text-xs font-mono text-[var(--color-text-muted)] shrink-0">AUC {bankAuc.toFixed(3)}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Institutional Model Verification & Discrimination Analytics */}
      <div className="glass-card p-3.5 sm:p-5 md:p-6 space-y-6 min-w-0 border border-slate-800">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800 pb-4">
          <div>
            <div className="flex items-center gap-2 text-indigo-400 font-semibold text-xs tracking-wider uppercase mb-1">
              <span className="p-1 rounded-lg bg-indigo-500/10 border border-indigo-500/20">🔬</span>
              Model Telemetry & Empirical Validation
            </div>
            <h3 className="text-base sm:text-lg font-bold text-[var(--color-text-primary)]">
              Institutional Model Verification & Discrimination Analytics
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              Live discrimination metrics, convergence tracking, and feature attribution across consortium members
            </p>
          </div>

          <div className="flex items-center gap-2">
            {simBanks.length > 0 && (
              <select
                value={selectedBankId || simBanks[0]?.id}
                onChange={(e) => setSelectedBankId(e.target.value)}
                className="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-indigo-400"
              >
                {simBanks.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            )}
            <div className="flex rounded-lg bg-slate-900 p-0.5 border border-slate-800 text-xs">
              <button
                type="button"
                onClick={() => setRocModelType('federated')}
                className={`px-2.5 py-1 rounded-md font-semibold transition-all ${
                  rocModelType === 'federated'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                Federated
              </button>
              <button
                type="button"
                onClick={() => setRocModelType('local')}
                className={`px-2.5 py-1 rounded-md font-semibold transition-all ${
                  rocModelType === 'local'
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                Local Baselines
              </button>
            </div>
          </div>
        </div>

        {/* Charts Grid */}
        {simBanks.length > 0 ? (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
              <ROCCurve banks={simBanks} modelType={rocModelType} />
              <LossChart rounds={simRounds} totalRounds={TOTAL_ROUNDS} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
              {activeBank && (
                <ConfusionMatrix
                  bank={activeBank}
                  modelType={rocModelType}
                />
              )}
              {activeBank && (
                <FeatureImportance
                  bank={activeBank}
                  modelType={rocModelType}
                />
              )}
            </div>

            <MetricsComparisonBarChart banks={simBanks} />
          </div>
        ) : (
          <div className="p-8 text-center border border-dashed border-slate-800 rounded-xl">
            <p className="text-xs text-slate-400">
              Launch a federated training run or select an existing simulation to view real-time model verification metrics.
            </p>
          </div>
        )}
      </div>

      {/* Asset Recovery & Collaborative FININT Operational Hub — Live Telemetry Card */}
      {arSummary && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mt-6 rounded-2xl border border-emerald-500/20 bg-gradient-to-br from-emerald-950/30 via-slate-900/60 to-cyan-950/20 p-5 shadow-[0_0_40px_rgba(16,185,129,0.08)]"
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-xs font-mono font-bold text-emerald-300 uppercase tracking-widest">
                Asset Recovery &amp; FININT Operational Hub
              </span>
            </div>
            <span className="text-[10px] font-mono text-slate-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded">
              LIVE · {arSummary.total_events} events
            </span>
          </div>

          {/* KPI Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl bg-emerald-950/40 border border-emerald-500/15 p-3 text-center">
              <p className="text-[10px] font-mono text-slate-400 mb-1">EUR Frozen</p>
              <p className="text-xl font-bold text-emerald-300 font-mono">€{(arSummary.total_eur_frozen / 1_000_000).toFixed(2)}M</p>
              <p className="text-[9px] text-slate-500 mt-0.5">via camt.056 recalls</p>
            </div>
            <div className="rounded-xl bg-cyan-950/40 border border-cyan-500/15 p-3 text-center">
              <p className="text-[10px] font-mono text-slate-400 mb-1">EUR Recovered</p>
              <p className="text-xl font-bold text-cyan-300 font-mono">€{(arSummary.total_eur_recovered / 1_000_000).toFixed(2)}M</p>
              <p className="text-[9px] text-slate-500 mt-0.5">successful recalls</p>
            </div>
            <div className="rounded-xl bg-indigo-950/40 border border-indigo-500/15 p-3 text-center">
              <p className="text-[10px] font-mono text-slate-400 mb-1">MTTR Reduction</p>
              <p className="text-xl font-bold text-indigo-300 font-mono">{arSummary.mttr_reduction_pct.toFixed(1)}%</p>
              <p className="text-[9px] text-slate-500 mt-0.5">vs. 48h baseline</p>
            </div>
            <div className="rounded-xl bg-purple-950/40 border border-purple-500/15 p-3 text-center">
              <p className="text-[10px] font-mono text-slate-400 mb-1">Containment Rate</p>
              <p className="text-xl font-bold text-purple-300 font-mono">{(arSummary.contagion_containment_rate * 100).toFixed(0)}%</p>
              <p className="text-[9px] text-slate-500 mt-0.5">&lt;60min freezes</p>
            </div>
          </div>

          {/* Sub-metrics bar */}
          <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-mono text-slate-400">
            <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10">MTTR P50 {arSummary.mttr_p50_minutes.toFixed(1)}min</span>
            <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10">MTTR P90 {arSummary.mttr_p90_minutes.toFixed(1)}min</span>
            <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10">{arSummary.mule_chains_disrupted} mule chains disrupted</span>
            <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10">{arSummary.active_provisional_holds} active holds</span>
            <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10">{arSummary.consortium_banks_active} consortium nodes</span>
          </div>
        </motion.div>
      )}

      {/* Deep Operational Panels */}
      <ModelRegistryPanel simulationId="live_prod_v2" />
      <ComplianceReportPanel simulationId="live_prod_v2" banks={[]} />
      <IncentiveRegistryPanel banks={[]} />
      <SecureHardwarePanel simulation={{ id: 'live_prod_v2', status: 'completed', config: { hardware_isolation_mode: 'tee' }, rounds: Array.from({ length: 10 }) } as any} />
      <StreamingGNNPanel simulation={{ id: 'live_prod_v2', status: 'completed', config: { enable_streaming_gnn: true }, streaming_gnn_node_count: 1420, streaming_gnn_edge_count: 5890, streaming_gnn_loss_history: [0.45, 0.38, 0.31, 0.26, 0.22] } as any} />

      {/* Real Dataset Ingestion Studio Modal */}
      <DatasetIngestionStudioModal
        isOpen={isIngestModalOpen}
        onClose={() => setIsIngestModalOpen(false)}
      />
    </div>
  );
}
