import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ShieldCheck, 
  AlertTriangle, 
  EyeOff, 
  Layers, 
  Zap, 
  Lock, 
  Share2,
  ChevronDown,
  ChevronUp,
  ArrowRight,
  Info
} from 'lucide-react';

export interface TopologyNode {
  id: string;
  name: string;
  bankId: 'bank_a' | 'bank_b' | 'bank_c';
  type: 'retail' | 'commercial' | 'cross_border' | 'mule' | 'merchant' | 'exit';
  x: number;
  y: number;
  role: string;
  volume?: string;
}

export interface TopologyEdge {
  id: string;
  source: string;
  target: string;
  amount: number;
  scenarioId?: string;
  isLaundering: boolean;
  hopIndex?: number;
  label?: string;
  customPath?: string;
  labelX?: number;
  labelY?: number;
}

interface CrossBankTopologyGraphProps {
  selectedScenario: string;
  onScenarioChange?: (scenarioId: string) => void;
  className?: string;
}

export interface ScenarioPreview {
  title: string;
  typology: string;
  description: string;
  isolatedRecall: number;
  federatedRecall: number;
  deltaUplift: number;
  vulnerability: string;
  participatingBanks: string[];
}

export const DEFAULT_PREVIEW: ScenarioPreview = {
  title: 'Scenario 3: Three-Bank Cyclic Ring (A -> B -> C -> A)',
  typology: 'CYCLIC_MULE_RING',
  description: 'Closed cyclic multi-hop ring spanning Bank Alpha -> Bank Beta -> Bank Gamma -> Bank Alpha.',
  isolatedRecall: 64.3,
  federatedRecall: 100.0,
  deltaUplift: 35.7,
  vulnerability: 'Each bank observes only 1 entry and 1 exit. The intermediate B->C leg is completely invisible to Bank Alpha.',
  participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
};

export const SCENARIO_PREVIEWS: Record<string, ScenarioPreview> = {
  SCENARIO_1: {
    title: 'Scenario 1: Single-Bank Localized Fraud',
    typology: 'LOCAL_SMURFING',
    description: 'Internal structuring and mule hopping confined strictly within Bank Alpha.',
    isolatedRecall: 100.0,
    federatedRecall: 100.0,
    deltaUplift: 0.0,
    vulnerability: 'Observable locally by Bank Alpha; external banks have zero exposure.',
    participatingBanks: ['bank_a'],
  },
  SCENARIO_2: {
    title: 'Scenario 2: Two-Bank Layering Chain',
    typology: 'CROSS_BANK_LAYERING',
    description: 'Rapid cross-institution transfer originating at Bank Alpha and layering into Bank Beta.',
    isolatedRecall: 65.0,
    federatedRecall: 100.0,
    deltaUplift: 35.0,
    vulnerability: 'Bank Alpha sees outgoing wire; Bank Beta sees incoming wire without origin context.',
    participatingBanks: ['bank_a', 'bank_b'],
  },
  SCENARIO_3: {
    title: 'Scenario 3: Three-Bank Cyclic Ring (A -> B -> C -> A)',
    typology: 'CYCLIC_MULE_RING',
    description: 'Closed cyclic multi-hop ring spanning Bank Alpha -> Bank Beta -> Bank Gamma -> Bank Alpha.',
    isolatedRecall: 64.3,
    federatedRecall: 100.0,
    deltaUplift: 35.7,
    vulnerability: 'Each bank observes only 1 entry and 1 exit. The intermediate B->C leg is completely invisible to Bank Alpha.',
    participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
  },
  SCENARIO_4: {
    title: 'Scenario 4: Behavior-Shifting Smurfing to Cash-Out',
    typology: 'BEHAVIOR_SHIFTING',
    description: 'Sub-threshold structuring at Bank A ($8.5k–$9.8k), consolidation at Bank B, and high-value wire ($180k) at Bank C.',
    isolatedRecall: 58.0,
    federatedRecall: 100.0,
    deltaUplift: 42.0,
    vulnerability: 'Bank Alpha sees small benign transfers; Bank Gamma sees large wire with zero local suspicion history.',
    participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
  },
  SCENARIO_5: {
    title: 'Scenario 5: Non-IID Archetype Arbitrage',
    typology: 'NON_IID_PROFILES',
    description: 'Retail (Bank A) to Commercial B2B (Bank B) to Cross-Border (Bank C) exploiting profile divergences.',
    isolatedRecall: 70.0,
    federatedRecall: 100.0,
    deltaUplift: 30.0,
    vulnerability: 'Local models overfit to their narrow business model and fail on cross-archetype laundering.',
    participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
  },
  SCENARIO_6: {
    title: 'Scenario 6: Extreme Sample Starvation at Bank Gamma',
    typology: 'SAMPLE_STARVATION',
    description: 'Bank Alpha and Beta have adequate training fraud, but Bank Gamma has only 2 positive incidents (0.05% prevalence).',
    isolatedRecall: 75.0,
    federatedRecall: 100.0,
    deltaUplift: 25.0,
    vulnerability: 'Bank Gamma local supervised model fails to converge due to severe positive sample starvation.',
    participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
  },
  SCENARIO_7: {
    title: 'Scenario 7: Zero-Positive Cold-Start Transfer',
    typology: 'ZERO_SHOT_TRANSFER',
    description: 'Bank Gamma has exactly ZERO positive fraud cases in historical logs (pure cold-start institution).',
    isolatedRecall: 0.0,
    federatedRecall: 100.0,
    deltaUplift: 100.0,
    vulnerability: 'Bank Gamma isolated classifier has 0% detection rate because it has never observed a positive fraud label.',
    participatingBanks: ['bank_a', 'bank_b', 'bank_c'],
  },
};

export default function CrossBankTopologyGraph({
  selectedScenario,
  onScenarioChange,
  className = '',
}: CrossBankTopologyGraphProps) {
  const [horizon, setHorizon] = useState<'global' | 'bank_a' | 'bank_b' | 'bank_c'>('global');
  const [activeEdgeHover, setActiveEdgeHover] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [isDossierCollapsed, setIsDossierCollapsed] = useState<boolean>(false);

  const scenarioData: ScenarioPreview = useMemo(() => {
    return SCENARIO_PREVIEWS[selectedScenario] ?? DEFAULT_PREVIEW;
  }, [selectedScenario]);

  // Topology node layout coordinates inside SVG coordinate space (960 x 520)
  // Symmetrical 3-Pod layout:
  // Pod Alpha: x: 25..305 (Center X = 165)
  // Pod Beta:  x: 340..620 (Center X = 480)
  // Pod Gamma: x: 655..935 (Center X = 795)
  const nodes: TopologyNode[] = useMemo(() => [
    // Bank Alpha Cluster (Retail - Left Pod)
    { id: 'a_hub', name: 'Alpha Core Gateway', bankId: 'bank_a', type: 'retail', x: 165, y: 130, role: 'Consortium Clearing Hub', volume: '$1.4M / 24h' },
    { id: 'a_mule1', name: 'Retail Mule A1', bankId: 'bank_a', type: 'mule', x: 110, y: 270, role: 'Origin Account', volume: '$42,500' },
    { id: 'a_mule2', name: 'Structuring Acc A2', bankId: 'bank_a', type: 'mule', x: 225, y: 390, role: 'Pooling Account', volume: '$89,200' },

    // Bank Beta Cluster (Commercial B2B - Center Pod)
    { id: 'b_hub', name: 'Beta Clearing Node', bankId: 'bank_b', type: 'commercial', x: 480, y: 130, role: 'Commercial Clearing Hub', volume: '$4.8M / 24h' },
    { id: 'b_layer', name: 'Corporate Mule B1', bankId: 'bank_b', type: 'mule', x: 425, y: 270, role: 'Layering Account', volume: '$126,000' },
    { id: 'b_pool', name: 'B2B Liquidity B2', bankId: 'bank_b', type: 'commercial', x: 540, y: 390, role: 'Escrow Account', volume: '$310,000' },

    // Bank Gamma Cluster (Cross-Border FX - Right Pod)
    { id: 'c_hub', name: 'Gamma Cross-Border', bankId: 'bank_c', type: 'cross_border', x: 795, y: 130, role: 'Correspondent FX Gateway', volume: '$3.2M / 24h' },
    { id: 'c_wire', name: 'Offshore Mule C1', bankId: 'bank_c', type: 'mule', x: 740, y: 270, role: 'Rapid Outward Wire', volume: '$215,000' },
    { id: 'c_exit', name: 'Fintech Exit C2', bankId: 'bank_c', type: 'exit', x: 855, y: 390, role: 'Digital Asset Cash-Out', volume: '$450,000' },
  ], []);

  // Multi-hop edges dynamically adjusted per scenario
  const edges: TopologyEdge[] = useMemo(() => {
    const baseEdges: TopologyEdge[] = [
      // Background inter-bank clearing bridges connecting the three consortium hubs
      { 
        id: 'bg_ab', 
        source: 'a_hub', 
        target: 'b_hub', 
        amount: 125000, 
        isLaundering: false, 
        label: 'SEPA Inst Bridge' 
      },
      { 
        id: 'bg_bc', 
        source: 'b_hub', 
        target: 'c_hub', 
        amount: 450000, 
        isLaundering: false, 
        label: 'TARGET2 RTGS' 
      },
      { 
        id: 'bg_ca', 
        source: 'c_hub', 
        target: 'a_hub', 
        amount: 89000, 
        isLaundering: false, 
        label: 'SWIFT / FX Corridor',
        customPath: 'M 795 105 Q 480 35 165 105',
        labelX: 480,
        labelY: 52
      },
    ];

    if (selectedScenario === 'SCENARIO_1') {
      return [
        ...baseEdges,
        { 
          id: 'sc1_1', 
          source: 'a_mule1', 
          target: 'a_mule2', 
          amount: 9450, 
          scenarioId: 'SCENARIO_1', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Smurfing ($9,450)' 
        },
        { 
          id: 'sc1_2', 
          source: 'a_mule2', 
          target: 'a_hub', 
          amount: 9380, 
          scenarioId: 'SCENARIO_1', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: Consolidation ($9,380)' 
        },
      ];
    } else if (selectedScenario === 'SCENARIO_2') {
      return [
        ...baseEdges,
        { 
          id: 'sc2_1', 
          source: 'a_mule1', 
          target: 'b_layer', 
          amount: 22000, 
          scenarioId: 'SCENARIO_2', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Wire A ➔ B ($22k)' 
        },
        { 
          id: 'sc2_2', 
          source: 'b_layer', 
          target: 'b_pool', 
          amount: 21500, 
          scenarioId: 'SCENARIO_2', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: Layering inside B ($21.5k)' 
        },
      ];
    } else if (selectedScenario === 'SCENARIO_3') {
      return [
        ...baseEdges,
        { 
          id: 'sc3_1', 
          source: 'a_mule2', 
          target: 'b_layer', 
          amount: 42000, 
          scenarioId: 'SCENARIO_3', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: A ➔ B ($42k)' 
        },
        { 
          id: 'sc3_2', 
          source: 'b_layer', 
          target: 'c_wire', 
          amount: 41200, 
          scenarioId: 'SCENARIO_3', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: B ➔ C ($41.2k)' 
        },
        { 
          id: 'sc3_3', 
          source: 'c_wire', 
          target: 'a_mule1', 
          amount: 39800, 
          scenarioId: 'SCENARIO_3', 
          isLaundering: true, 
          hopIndex: 2, 
          label: 'Hop 2: C ➔ A ($39.8k) [Cycle Closure]',
          customPath: 'M 740 280 Q 480 475 110 280',
          labelX: 480,
          labelY: 440
        },
      ];
    } else if (selectedScenario === 'SCENARIO_4') {
      return [
        ...baseEdges,
        { 
          id: 'sc4_1', 
          source: 'a_mule1', 
          target: 'b_pool', 
          amount: 9200, 
          scenarioId: 'SCENARIO_4', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0a: Smurf Wire 1 ($9.2k)' 
        },
        { 
          id: 'sc4_2', 
          source: 'a_mule2', 
          target: 'b_pool', 
          amount: 9600, 
          scenarioId: 'SCENARIO_4', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0b: Smurf Wire 2 ($9.6k)' 
        },
        { 
          id: 'sc4_3', 
          source: 'b_pool', 
          target: 'c_wire', 
          amount: 184000, 
          scenarioId: 'SCENARIO_4', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: Consolidation ($184k)' 
        },
        { 
          id: 'sc4_4', 
          source: 'c_wire', 
          target: 'c_exit', 
          amount: 182500, 
          scenarioId: 'SCENARIO_4', 
          isLaundering: true, 
          hopIndex: 2, 
          label: 'Hop 2: Crypto Cash-Out ($182.5k)' 
        },
      ];
    } else if (selectedScenario === 'SCENARIO_5') {
      return [
        ...baseEdges,
        { 
          id: 'sc5_1', 
          source: 'a_mule1', 
          target: 'b_pool', 
          amount: 78000, 
          scenarioId: 'SCENARIO_5', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Retail ➔ B2B Arbitrage ($78k)' 
        },
        { 
          id: 'sc5_2', 
          source: 'b_pool', 
          target: 'c_wire', 
          amount: 76500, 
          scenarioId: 'SCENARIO_5', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: FX Arbitrage Hop ($76.5k)' 
        },
        { 
          id: 'sc5_3', 
          source: 'c_wire', 
          target: 'c_exit', 
          amount: 75200, 
          scenarioId: 'SCENARIO_5', 
          isLaundering: true, 
          hopIndex: 2, 
          label: 'Hop 2: Offshore Exit ($75.2k)' 
        },
      ];
    } else if (selectedScenario === 'SCENARIO_6') {
      return [
        ...baseEdges,
        { 
          id: 'sc6_1', 
          source: 'a_mule2', 
          target: 'b_layer', 
          amount: 31500, 
          scenarioId: 'SCENARIO_6', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Structured Feeder ($31.5k)' 
        },
        { 
          id: 'sc6_2', 
          source: 'b_layer', 
          target: 'c_wire', 
          amount: 31000, 
          scenarioId: 'SCENARIO_6', 
          isLaundering: true, 
          hopIndex: 1, 
          label: 'Hop 1: Starvation Exploit ($31k)' 
        },
        { 
          id: 'sc6_3', 
          source: 'c_wire', 
          target: 'c_exit', 
          amount: 30400, 
          scenarioId: 'SCENARIO_6', 
          isLaundering: true, 
          hopIndex: 2, 
          label: 'Hop 2: Unflagged Cold Exit ($30.4k)' 
        },
      ];
    } else if (selectedScenario === 'SCENARIO_7') {
      return [
        ...baseEdges,
        { 
          id: 'sc7_1', 
          source: 'a_mule1', 
          target: 'c_exit', 
          amount: 34000, 
          scenarioId: 'SCENARIO_7', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Cold-Start Exploit on Gamma ($34k)',
          customPath: 'M 110 280 Q 480 475 855 390',
          labelX: 480,
          labelY: 450
        },
        { 
          id: 'sc7_2', 
          source: 'a_mule2', 
          target: 'c_wire', 
          amount: 28500, 
          scenarioId: 'SCENARIO_7', 
          isLaundering: true, 
          hopIndex: 0, 
          label: 'Hop 0: Parallel Mule Infiltration ($28.5k)',
          customPath: 'M 225 390 Q 480 435 740 270',
          labelX: 480,
          labelY: 405
        },
      ];
    }

    return baseEdges;
  }, [selectedScenario]);

  // Determine edge visibility under the selected Information Horizon
  const isEdgeVisibleInHorizon = (edge: TopologyEdge): boolean => {
    if (horizon === 'global') return true;
    const srcNode = nodes.find((n) => n.id === edge.source);
    const tgtNode = nodes.find((n) => n.id === edge.target);
    if (!srcNode || !tgtNode) return true;
    // Edge is visible to the bank if either source or target belongs to that bank
    return srcNode.bankId === horizon || tgtNode.bankId === horizon;
  };

  const isBlindSpotEdge = (edge: TopologyEdge): boolean => {
    return edge.isLaundering && !isEdgeVisibleInHorizon(edge);
  };

  const activeHorizonName = useMemo(() => {
    switch (horizon) {
      case 'bank_a': return 'Bank Alpha (Retail)';
      case 'bank_b': return 'Bank Beta (Commercial)';
      case 'bank_c': return 'Bank Gamma (Cross-Border)';
      default: return 'Global Oracle (Consortium)';
    }
  }, [horizon]);

  // Filter laundering hops for the Hop Execution Trail
  const launderingHops = useMemo(() => {
    return edges.filter(e => e.isLaundering).sort((a, b) => (a.hopIndex ?? 0) - (b.hopIndex ?? 0));
  }, [edges]);

  return (
    <div className={`flex flex-col gap-5 sm:gap-6 rounded-2xl border border-slate-800 bg-slate-900/90 p-3.5 sm:p-6 backdrop-blur-md shadow-2xl overflow-hidden w-full max-w-full ${className}`}>
      
      {/* Top Controls: Scenario Selector & Responsive Horizon Switcher */}
      <div className="flex flex-col gap-3.5 lg:flex-row lg:items-center lg:justify-between w-full">
        {/* Scenario Selector Pills - Mobile-friendly responsive 7-column grid without horizontal scrolling */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-2.5 w-full lg:w-auto">
          <div className="flex items-center justify-between sm:justify-start shrink-0">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
              <Layers className="h-3.5 w-3.5 text-cyan-400" /> Scenario:
            </span>
            <span className="text-[11px] font-mono text-cyan-400 font-medium sm:hidden">
              {selectedScenario.replace('SCENARIO_', 'S')} Selected
            </span>
          </div>
          <div className="grid grid-cols-7 sm:flex items-center gap-1 sm:gap-1.5 w-full sm:w-auto">
            {Object.keys(SCENARIO_PREVIEWS).map((scId) => {
              const isSelected = selectedScenario === scId;
              const scNum = scId.replace('SCENARIO_', 'S');
              return (
                <button
                  key={scId}
                  onClick={() => onScenarioChange?.(scId)}
                  className={`px-1 sm:px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-all duration-200 text-center flex items-center justify-center ${
                    isSelected
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 shadow-sm shadow-cyan-500/20 font-bold'
                      : 'bg-slate-800/60 text-slate-400 border border-slate-700/50 hover:bg-slate-800 hover:text-slate-200'
                  }`}
                  title={SCENARIO_PREVIEWS[scId]?.title ?? scId}
                >
                  <span className="lg:hidden">{scNum}</span>
                  <span className="hidden lg:inline">{scId.replace('_', ' ')}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Responsive Information Horizon Filter - Mobile-friendly layout preventing text clipping & overflow */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-1.5 sm:gap-2 rounded-xl bg-slate-950/90 p-2 sm:p-1 border border-slate-800 w-full lg:w-auto">
          <div className="flex items-center justify-between sm:justify-start px-0.5 sm:px-1 shrink-0">
            <span className="text-[11px] sm:text-xs font-mono text-slate-400 flex items-center gap-1.5">
              <Lock className="h-3 w-3 text-amber-400" /> Horizon:
            </span>
            <span className="text-[10px] font-mono text-amber-400/90 sm:hidden">
              {horizon === 'global' ? 'Global View' : horizon === 'bank_a' ? 'Alpha View' : horizon === 'bank_b' ? 'Beta View' : 'Gamma View'}
            </span>
          </div>
          <div className="grid grid-cols-4 sm:flex items-center gap-1 w-full sm:w-auto">
            {(['global', 'bank_a', 'bank_b', 'bank_c'] as const).map((hKey) => {
              const isActive = horizon === hKey;
              const shortLabels = {
                global: 'Global',
                bank_a: 'Alpha',
                bank_b: 'Beta',
                bank_c: 'Gamma',
              };
              const fullLabels = {
                global: 'Global Oracle',
                bank_a: 'Bank Alpha',
                bank_b: 'Bank Beta',
                bank_c: 'Bank Gamma',
              };
              const activeColorClass = {
                global: 'bg-violet-600 text-white font-semibold shadow-sm',
                bank_a: 'bg-sky-600 text-white font-semibold shadow-sm',
                bank_b: 'bg-indigo-600 text-white font-semibold shadow-sm',
                bank_c: 'bg-emerald-600 text-white font-semibold shadow-sm',
              }[hKey];

              return (
                <button
                  key={hKey}
                  onClick={() => setHorizon(hKey)}
                  className={`px-1.5 sm:px-2.5 py-1.5 sm:py-1 rounded-lg text-xs font-mono transition-all text-center flex items-center justify-center truncate ${
                    isActive
                      ? activeColorClass
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                  }`}
                >
                  <span className="lg:hidden">{shortLabels[hKey]}</span>
                  <span className="hidden lg:inline">{fullLabels[hKey]}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Information Horizon Active Alert Notification */}
      <AnimatePresence>
        {horizon !== 'global' && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            className="flex items-center gap-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 px-3.5 py-2 text-xs text-amber-300 backdrop-blur-md"
          >
            <EyeOff className="h-4 w-4 text-amber-400 shrink-0" />
            <div className="leading-snug">
              <strong>Isolated Horizon Mode ({activeHorizonName}):</strong> Multi-hop hops outside this institution's view are masked with{' '}
              <span className="font-semibold text-rose-300 font-mono">🔒 BLIND SPOT</span>. Isolated silos fail to reconstruct the full laundering topology.
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Unified SVG Network Canvas */}
      <div className="relative w-full rounded-2xl border border-slate-800/90 bg-gradient-to-b from-slate-950 via-[#0a0f1d] to-slate-950 overflow-hidden shadow-2xl">
        <svg 
          viewBox="0 0 960 520" 
          className="w-full h-auto max-h-[540px] block select-none"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            {/* Gradients */}
            <linearGradient id="fraudPulse" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#f43f5e" stopOpacity="0.95" />
              <stop offset="50%" stopColor="#fb7185" stopOpacity="1" />
              <stop offset="100%" stopColor="#fbbf24" stopOpacity="0.95" />
            </linearGradient>

            <linearGradient id="benignFlow" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.4" />
              <stop offset="100%" stopColor="#818cf8" stopOpacity="0.4" />
            </linearGradient>

            {/* Pod Radial Background Gradients */}
            <radialGradient id="alphaPodGrad" cx="50%" cy="40%" r="60%">
              <stop offset="0%" stopColor="#0369a1" stopOpacity="0.12" />
              <stop offset="100%" stopColor="#0f172a" stopOpacity="0.6" />
            </radialGradient>

            <radialGradient id="betaPodGrad" cx="50%" cy="40%" r="60%">
              <stop offset="0%" stopColor="#4338ca" stopOpacity="0.12" />
              <stop offset="100%" stopColor="#0f172a" stopOpacity="0.6" />
            </radialGradient>

            <radialGradient id="gammaPodGrad" cx="50%" cy="40%" r="60%">
              <stop offset="0%" stopColor="#047857" stopOpacity="0.12" />
              <stop offset="100%" stopColor="#0f172a" stopOpacity="0.6" />
            </radialGradient>

            {/* Subtle Cyber Grid Pattern */}
            <pattern id="cyber-grid" width="24" height="24" patternUnits="userSpaceOnUse">
              <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#1e293b" strokeWidth="0.5" strokeOpacity="0.3" />
            </pattern>

            {/* Markers */}
            <marker id="arrow-fraud" viewBox="0 0 10 10" refX="24" refY="5" markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
              <path d="M 0 1.5 L 9 5 L 0 8.5 z" fill="#f43f5e" />
            </marker>

            <marker id="arrow-benign" viewBox="0 0 10 10" refX="24" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 2 L 8 5 L 0 8 z" fill="#475569" />
            </marker>

            <marker id="arrow-blindspot" viewBox="0 0 10 10" refX="24" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 2 L 8 5 L 0 8 z" fill="#64748b" />
            </marker>

            {/* Glowing Drop-Shadow Filters */}
            <filter id="glow-fraud" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>

            <filter id="nodeShadow" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="#000000" floodOpacity="0.7" />
            </filter>
          </defs>

          {/* Background Cyber Grid */}
          <rect width="960" height="520" fill="url(#cyber-grid)" />

          {/* ======================================================== */}
          {/* Institutional Cluster Zones (SVG Pods - Unified Geometry) */}
          {/* ======================================================== */}

          {/* --- Pod 1: Bank Alpha (Retail) --- */}
          <g className={`transition-opacity duration-300 ${horizon !== 'global' && horizon !== 'bank_a' ? 'opacity-30' : 'opacity-100'}`}>
            <rect 
              x="25" 
              y="35" 
              width="280" 
              height="450" 
              rx="18" 
              fill="url(#alphaPodGrad)" 
              stroke="#0284c7" 
              strokeWidth={horizon === 'bank_a' ? "2" : "1.2"} 
              strokeDasharray={horizon === 'bank_a' ? undefined : "6 4"}
              strokeOpacity={horizon === 'bank_a' ? "0.9" : "0.4"}
            />
            {/* Header Badge */}
            <rect x="37" y="47" width="256" height="42" rx="10" fill="#082f49" fillOpacity="0.4" stroke="#0284c7" strokeWidth="0.8" strokeOpacity="0.3" />
            <circle cx="56" cy="68" r="10" fill="#0284c7" fillOpacity="0.2" stroke="#38bdf8" strokeWidth="1" />
            <path d="M 52 72 L 52 66 L 56 63 L 60 66 L 60 72 Z" fill="#38bdf8" />
            <text x="73" y="64" fill="#f8fafc" fontSize="12" fontWeight="700" fontFamily="monospace">BANK ALPHA</text>
            <text x="73" y="78" fill="#38bdf8" fontSize="9" fontWeight="600">RETAIL BANKING POD</text>
            <rect x="220" y="58" width="62" height="20" rx="5" fill="#0c4a6e" fillOpacity="0.6" stroke="#0284c7" strokeWidth="0.6" />
            <text x="251" y="72" fill="#7dd3fc" fontSize="8.5" fontWeight="600" fontFamily="monospace" textAnchor="middle">8.4k ACCTS</text>
          </g>

          {/* --- Pod 2: Bank Beta (Commercial B2B) --- */}
          <g className={`transition-opacity duration-300 ${horizon !== 'global' && horizon !== 'bank_b' ? 'opacity-30' : 'opacity-100'}`}>
            <rect 
              x="340" 
              y="35" 
              width="280" 
              height="450" 
              rx="18" 
              fill="url(#betaPodGrad)" 
              stroke="#6366f1" 
              strokeWidth={horizon === 'bank_b' ? "2" : "1.2"} 
              strokeDasharray={horizon === 'bank_b' ? undefined : "6 4"}
              strokeOpacity={horizon === 'bank_b' ? "0.9" : "0.4"}
            />
            {/* Header Badge */}
            <rect x="352" y="47" width="256" height="42" rx="10" fill="#1e1b4b" fillOpacity="0.4" stroke="#6366f1" strokeWidth="0.8" strokeOpacity="0.3" />
            <circle cx="371" cy="68" r="10" fill="#4338ca" fillOpacity="0.2" stroke="#818cf8" strokeWidth="1" />
            <path d="M 367 72 L 367 65 L 375 65 L 375 72 Z" fill="#818cf8" />
            <text x="388" y="64" fill="#f8fafc" fontSize="12" fontWeight="700" fontFamily="monospace">BANK BETA</text>
            <text x="388" y="78" fill="#818cf8" fontSize="9" fontWeight="600">COMMERCIAL B2B POD</text>
            <rect x="535" y="58" width="62" height="20" rx="5" fill="#312e81" fillOpacity="0.6" stroke="#6366f1" strokeWidth="0.6" />
            <text x="566" y="72" fill="#a5b4fc" fontSize="8.5" fontWeight="600" fontFamily="monospace" textAnchor="middle">4.2k ACCTS</text>
          </g>

          {/* --- Pod 3: Bank Gamma (Cross-Border FX) --- */}
          <g className={`transition-opacity duration-300 ${horizon !== 'global' && horizon !== 'bank_c' ? 'opacity-30' : 'opacity-100'}`}>
            <rect 
              x="655" 
              y="35" 
              width="280" 
              height="450" 
              rx="18" 
              fill="url(#gammaPodGrad)" 
              stroke="#059669" 
              strokeWidth={horizon === 'bank_c' ? "2" : "1.2"} 
              strokeDasharray={horizon === 'bank_c' ? undefined : "6 4"}
              strokeOpacity={horizon === 'bank_c' ? "0.9" : "0.4"}
            />
            {/* Header Badge */}
            <rect x="667" y="47" width="256" height="42" rx="10" fill="#064e3b" fillOpacity="0.4" stroke="#059669" strokeWidth="0.8" strokeOpacity="0.3" />
            <circle cx="686" cy="68" r="10" fill="#047857" fillOpacity="0.2" stroke="#34d399" strokeWidth="1" />
            <circle cx="686" cy="68" r="5" fill="none" stroke="#34d399" strokeWidth="1" />
            <text x="703" y="64" fill="#f8fafc" fontSize="12" fontWeight="700" fontFamily="monospace">BANK GAMMA</text>
            <text x="703" y="78" fill="#34d399" fontSize="9" fontWeight="600">CROSS-BORDER FX POD</text>
            <rect x="850" y="58" width="62" height="20" rx="5" fill="#065f46" fillOpacity="0.6" stroke="#059669" strokeWidth="0.6" />
            <text x="881" y="72" fill="#6ee7b7" fontSize="8.5" fontWeight="600" fontFamily="monospace" textAnchor="middle">1.9k ACCTS</text>
          </g>

          {/* ======================================================== */}
          {/* Render Inter-Bank Clearing Conduits & Laundering Edges */}
          {/* ======================================================== */}

          {edges.map((edge) => {
            const src = nodes.find((n) => n.id === edge.source);
            const tgt = nodes.find((n) => n.id === edge.target);
            if (!src || !tgt) return null;

            const isVisible = isEdgeVisibleInHorizon(edge);
            const isBlindSpot = isBlindSpotEdge(edge);
            const isHovered = activeEdgeHover === edge.id;

            // Calculate label coordinates
            const midX = edge.labelX ?? (src.x + tgt.x) / 2;
            const midY = edge.labelY ?? (src.y + tgt.y) / 2;
            const labelText = isBlindSpot 
              ? '🔒 BLIND SPOT' 
              : edge.label || `$${edge.amount.toLocaleString()}`;

            const labelWidth = isBlindSpot 
              ? 96 
              : Math.max(76, labelText.length * 6.5 + 16);

            return (
              <g 
                key={edge.id} 
                className="transition-all duration-300"
                onMouseEnter={() => setActiveEdgeHover(edge.id)}
                onMouseLeave={() => setActiveEdgeHover(null)}
              >
                {/* Edge Line or Curved Path */}
                {edge.customPath ? (
                  <path
                    d={edge.customPath}
                    fill="none"
                    stroke={
                      isBlindSpot
                        ? '#64748b'
                        : edge.isLaundering
                        ? 'url(#fraudPulse)'
                        : 'url(#benignFlow)'
                    }
                    strokeWidth={isHovered ? 4.5 : edge.isLaundering ? 3.2 : 1.6}
                    strokeDasharray={isBlindSpot ? '5,5' : edge.isLaundering ? '8,5' : '4,4'}
                    strokeOpacity={isBlindSpot ? 0.45 : isVisible ? 1.0 : 0.2}
                    markerEnd={
                      isBlindSpot 
                        ? 'url(#arrow-blindspot)' 
                        : edge.isLaundering 
                        ? 'url(#arrow-fraud)' 
                        : 'url(#arrow-benign)'
                    }
                    filter={edge.isLaundering && !isBlindSpot ? 'url(#glow-fraud)' : undefined}
                    className="cursor-pointer transition-all duration-200"
                  />
                ) : (
                  <line
                    x1={src.x}
                    y1={src.y}
                    x2={tgt.x}
                    y2={tgt.y}
                    stroke={
                      isBlindSpot
                        ? '#64748b'
                        : edge.isLaundering
                        ? 'url(#fraudPulse)'
                        : 'url(#benignFlow)'
                    }
                    strokeWidth={isHovered ? 4.5 : edge.isLaundering ? 3.2 : 1.6}
                    strokeDasharray={isBlindSpot ? '5,5' : edge.isLaundering ? '8,5' : '4,4'}
                    strokeOpacity={isBlindSpot ? 0.45 : isVisible ? 1.0 : 0.2}
                    markerEnd={
                      isBlindSpot 
                        ? 'url(#arrow-blindspot)' 
                        : edge.isLaundering 
                        ? 'url(#arrow-fraud)' 
                        : 'url(#arrow-benign)'
                    }
                    filter={edge.isLaundering && !isBlindSpot ? 'url(#glow-fraud)' : undefined}
                    className="cursor-pointer transition-all duration-200"
                  />
                )}

                {/* Edge Label Pill Chip */}
                {(edge.isLaundering || edge.label) && (
                  <g transform={`translate(${midX}, ${midY})`} className="cursor-pointer pointer-events-none">
                    <rect
                      x={-labelWidth / 2}
                      y={-10.5}
                      width={labelWidth}
                      height={21}
                      rx={10.5}
                      fill={isBlindSpot ? "#0f172a" : edge.isLaundering ? "#18080f" : "#0a0e1a"}
                      stroke={
                        isBlindSpot 
                          ? "#475569" 
                          : edge.isLaundering 
                          ? "#f43f5e" 
                          : "#334155"
                      }
                      strokeWidth={edge.isLaundering && !isBlindSpot ? 1.4 : 1}
                      filter="url(#nodeShadow)"
                    />
                    <text
                      y={3.5}
                      fill={
                        isBlindSpot 
                          ? "#94a3b8" 
                          : edge.isLaundering 
                          ? "#fecdd3" 
                          : "#cbd5e1"
                      }
                      fontSize={isBlindSpot ? 9.5 : 9}
                      fontWeight="700"
                      fontFamily="monospace"
                      textAnchor="middle"
                    >
                      {labelText}
                    </text>
                  </g>
                )}
              </g>
            );
          })}

          {/* ======================================================== */}
          {/* Render Topology Nodes (Consortium Gateways & Mules) */}
          {/* ======================================================== */}

          {nodes.map((node) => {
            const isBankHub = node.id.endsWith('_hub');
            const isDimmed = horizon !== 'global' && node.bankId !== horizon;
            const isSelected = selectedNode?.id === node.id;

            const hubColor = {
              bank_a: '#38bdf8',
              bank_b: '#818cf8',
              bank_c: '#34d399',
            }[node.bankId];

            return (
              <g
                key={node.id}
                transform={`translate(${node.x}, ${node.y})`}
                onClick={() => setSelectedNode(isSelected ? null : node)}
                className={`cursor-pointer transition-all duration-300 ${
                  isDimmed ? 'opacity-35' : 'opacity-100'
                }`}
                filter="url(#nodeShadow)"
              >
                {/* Bank Core Gateways */}
                {isBankHub ? (
                  <>
                    {/* Concentric Pulse Ring */}
                    <circle
                      r={27}
                      fill="none"
                      stroke={hubColor}
                      strokeWidth={1}
                      strokeOpacity={0.35}
                      strokeDasharray="4 3"
                    />
                    {/* Core Hub Body */}
                    <circle
                      r={20}
                      fill="#0f172a"
                      stroke={hubColor}
                      strokeWidth={isSelected ? 3.5 : 2.5}
                    />
                    {/* Hub Icon Indicator */}
                    <circle r={7} fill={hubColor} fillOpacity={0.85} />
                    <circle r={3} fill="#ffffff" />
                  </>
                ) : (
                  <>
                    {/* Account Node Core */}
                    <circle
                      r={14}
                      fill={
                        node.type === 'mule' 
                          ? '#881337' 
                          : node.type === 'exit' 
                          ? '#78350f' 
                          : '#1e1b4b'
                      }
                      stroke={
                        node.type === 'mule' 
                          ? '#fb7185' 
                          : node.type === 'exit' 
                          ? '#f59e0b' 
                          : '#818cf8'
                      }
                      strokeWidth={isSelected ? 3 : 1.8}
                    />
                    <circle
                      r={5}
                      fill={
                        node.type === 'mule' 
                          ? '#f43f5e' 
                          : node.type === 'exit' 
                          ? '#fbbf24' 
                          : '#a5b4fc'
                      }
                    />
                  </>
                )}

                {/* Node Title Background Chip for high contrast */}
                <g transform={`translate(0, ${isBankHub ? 32 : 25})`} className="pointer-events-none select-none">
                  <rect
                    x={-(node.name.length * 3.3 + 12)}
                    y={-9}
                    width={node.name.length * 6.6 + 24}
                    height={18}
                    rx={6}
                    fill="#030712"
                    fillOpacity="0.85"
                    stroke={isSelected ? "#38bdf8" : "#1e293b"}
                    strokeWidth={isSelected ? 1 : 0.6}
                  />
                  <text
                    y={3.5}
                    fill="#f1f5f9"
                    fontSize={isBankHub ? 10.5 : 9.5}
                    fontWeight={isBankHub ? "700" : "600"}
                    textAnchor="middle"
                  >
                    {node.name}
                  </text>
                </g>

                {/* Role / Type Subtitle */}
                <text
                  y={isBankHub ? 52 : 44}
                  fill={isBankHub ? hubColor : "#94a3b8"}
                  fontSize={8.5}
                  fontFamily="monospace"
                  textAnchor="middle"
                  className="pointer-events-none select-none font-medium"
                >
                  {node.role}
                </text>
              </g>
            );
          })}
        </svg>

        {/* Desktop Scenario Overlay Card (Floating with Collapse/Expand) */}
        <div className="hidden sm:block absolute bottom-3.5 left-3.5 max-w-sm lg:max-w-md rounded-xl border border-slate-800/90 bg-slate-950/90 p-4 backdrop-blur-md shadow-2xl transition-all">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <span className="text-xs font-mono font-semibold text-cyan-400 uppercase tracking-wider">
              {scenarioData.typology}
            </span>
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-mono rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 px-1.5 py-0.5 font-bold">
                +{scenarioData.deltaUplift.toFixed(1)}% Uplift
              </span>
              <button 
                onClick={() => setIsDossierCollapsed(!isDossierCollapsed)}
                className="text-slate-400 hover:text-slate-200 p-0.5 rounded hover:bg-slate-800 transition-colors"
                title={isDossierCollapsed ? "Expand Details" : "Collapse Details"}
              >
                {isDossierCollapsed ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          <h4 className="text-sm font-bold text-slate-100 mb-1">{scenarioData.title}</h4>

          {!isDossierCollapsed && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
            >
              <p className="text-xs text-slate-300 leading-relaxed mb-3">{scenarioData.description}</p>
              <div className="text-[11px] text-amber-300/90 bg-amber-950/20 border border-amber-500/20 rounded-lg p-2.5 flex items-start gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-0.5" />
                <span>
                  <strong>Silo Vulnerability:</strong> {scenarioData.vulnerability}
                </span>
              </div>
            </motion.div>
          )}
        </div>

        {/* Selected Node Inspector Flyout */}
        <AnimatePresence>
          {selectedNode && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="absolute top-3.5 right-3.5 max-w-xs rounded-xl border border-cyan-500/40 bg-slate-950/95 p-3.5 text-xs backdrop-blur-md shadow-2xl"
            >
              <div className="flex items-center justify-between pb-1.5 border-b border-slate-800 mb-2">
                <div className="flex items-center gap-1.5 text-cyan-400 font-bold">
                  <Info className="h-3.5 w-3.5" /> Node Inspection
                </div>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="text-slate-400 hover:text-white text-xs px-1"
                >
                  ✕
                </button>
              </div>
              <div className="space-y-1.5 font-mono">
                <div><span className="text-slate-400">Node:</span> <span className="text-slate-100 font-bold">{selectedNode.name}</span></div>
                <div><span className="text-slate-400">Institution:</span> <span className="text-cyan-300">{selectedNode.bankId.replace('_', ' ').toUpperCase()}</span></div>
                <div><span className="text-slate-400">Classification:</span> <span className="text-amber-300">{selectedNode.role}</span></div>
                <div><span className="text-slate-400">Est. 24h Volume:</span> <span className="text-emerald-400 font-semibold">{selectedNode.volume}</span></div>
                <div>
                  <span className="text-slate-400">Horizon Visibility:</span>{' '}
                  {horizon === 'global' || selectedNode.bankId === horizon ? (
                    <span className="text-emerald-400 font-semibold">DIRECT (UNMASKED)</span>
                  ) : (
                    <span className="text-rose-400 font-semibold">MASKED / BLIND SPOT</span>
                  )}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Mobile-Only Dedicated Scenario Dossier Card (Never overlaps the graph!) */}
      <div className="sm:hidden rounded-xl border border-slate-800 bg-slate-950/80 p-4 shadow-xl">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-xs font-mono font-semibold text-cyan-400 uppercase tracking-wider">
            {scenarioData.typology}
          </span>
          <span className="text-[11px] font-mono rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 px-2 py-0.5 font-bold">
            +{scenarioData.deltaUplift.toFixed(1)}% Uplift
          </span>
        </div>
        <h4 className="text-sm font-bold text-slate-100 mb-1.5">{scenarioData.title}</h4>
        <p className="text-xs text-slate-300 leading-relaxed mb-3">{scenarioData.description}</p>
        <div className="text-xs text-amber-300/90 bg-amber-950/20 border border-amber-500/20 rounded-lg p-2.5 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
          <span>
            <strong>Silo Vulnerability:</strong> {scenarioData.vulnerability}
          </span>
        </div>
      </div>

      {/* Multi-Hop Execution Trail (Hop-by-hop breakdown) */}
      {launderingHops.length > 0 && (
        <div className="rounded-xl border border-slate-800/80 bg-slate-950/50 p-3 sm:p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
              <Share2 className="h-3.5 w-3.5 text-cyan-400" /> Multi-Hop Laundering Execution Trail:
            </span>
            <span className="text-[11px] font-mono text-slate-400">
              {launderingHops.length} Laundering Hops
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {launderingHops.map((hop) => {
              const srcNode = nodes.find(n => n.id === hop.source);
              const tgtNode = nodes.find(n => n.id === hop.target);
              const isBlindSpot = isBlindSpotEdge(hop);

              return (
                <div 
                  key={hop.id} 
                  className={`rounded-lg border p-2.5 text-xs transition-all ${
                    isBlindSpot
                      ? 'border-slate-700/60 bg-slate-900/40 text-slate-400'
                      : 'border-rose-500/30 bg-rose-950/20 text-slate-200'
                  }`}
                >
                  <div className="flex items-center justify-between font-mono font-bold text-[11px] mb-1">
                    <span className={isBlindSpot ? 'text-slate-400' : 'text-rose-400'}>
                      HOP {hop.hopIndex ?? 0}
                    </span>
                    <span className={isBlindSpot ? 'text-slate-400 line-through' : 'text-amber-300'}>
                      ${hop.amount.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-slate-300 font-medium">
                    <span className="truncate">{srcNode?.name}</span>
                    <ArrowRight className="h-3 w-3 text-slate-500 shrink-0" />
                    <span className="truncate">{tgtNode?.name}</span>
                  </div>
                  {isBlindSpot ? (
                    <div className="text-[10px] text-rose-400 font-mono font-semibold mt-1 flex items-center gap-1">
                      <EyeOff className="h-3 w-3" /> Blind Spot to {activeHorizonName}
                    </div>
                  ) : (
                    <div className="text-[10px] text-emerald-400/90 font-mono mt-1">
                      ✓ Observable in this Horizon
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Metrics HUD: Isolated vs Federated Uplift */}
      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 sm:p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1 truncate">
            <ShieldCheck className="h-4 w-4 text-rose-400 shrink-0" /> Isolated Silo Recall
          </span>
          <div className="text-xl sm:text-2xl font-bold font-mono text-rose-400">
            {scenarioData.isolatedRecall.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400 block truncate">Single-institution view</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 sm:p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1 truncate">
            <Zap className="h-4 w-4 text-cyan-400 shrink-0" /> Federated Consensus
          </span>
          <div className="text-xl sm:text-2xl font-bold font-mono text-cyan-400">
            {scenarioData.federatedRecall.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400 block truncate">Collaborative model</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 sm:p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1 truncate">
            <Share2 className="h-4 w-4 text-emerald-400 shrink-0" /> Collaborative Uplift (Δ)
          </span>
          <div className="text-xl sm:text-2xl font-bold font-mono text-emerald-400">
            +{scenarioData.deltaUplift.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400 block truncate">Gain over isolated silos</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 sm:p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1 truncate">
            <Lock className="h-4 w-4 text-indigo-400 shrink-0" /> Information Horizon
          </span>
          <div className="text-base sm:text-2xl font-bold font-mono text-indigo-400 whitespace-nowrap">
            ZERO RAW PII
          </div>
          <span className="text-[11px] text-slate-400 block truncate">Zero raw PII or cross-bank edge leakage</span>
        </div>
      </div>
    </div>
  );
}
