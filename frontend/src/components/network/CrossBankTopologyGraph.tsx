import { useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Building2, 
  ShieldCheck, 
  AlertTriangle, 
  EyeOff, 
  Layers, 
  Zap, 
  Lock, 
  Share2
} from 'lucide-react';

export interface TopologyNode {
  id: string;
  name: string;
  bankId: 'bank_a' | 'bank_b' | 'bank_c';
  type: 'retail' | 'commercial' | 'cross_border' | 'mule' | 'merchant' | 'exit';
  x: number;
  y: number;
  role: string;
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

  const scenarioData: ScenarioPreview = useMemo(() => {
    return SCENARIO_PREVIEWS[selectedScenario] ?? DEFAULT_PREVIEW;
  }, [selectedScenario]);

  // Topology node layout coordinates (SVG coordinate space 800 x 480)
  const nodes: TopologyNode[] = useMemo(() => [
    // Bank Alpha Cluster (Left)
    { id: 'a_hub', name: 'Alpha Core Gateway', bankId: 'bank_a', type: 'retail', x: 160, y: 160, role: 'Consortium Node' },
    { id: 'a_mule1', name: 'Retail Mule A1', bankId: 'bank_a', type: 'mule', x: 90, y: 270, role: 'Origin Account' },
    { id: 'a_mule2', name: 'Structuring Acc A2', bankId: 'bank_a', type: 'mule', x: 230, y: 280, role: 'Pooling Account' },

    // Bank Beta Cluster (Top-Right)
    { id: 'b_hub', name: 'Beta Clearing Node', bankId: 'bank_b', type: 'commercial', x: 500, y: 120, role: 'Consortium Node' },
    { id: 'b_layer', name: 'Corporate Mule B1', bankId: 'bank_b', type: 'mule', x: 640, y: 140, role: 'Layering Account' },
    { id: 'b_pool', name: 'B2B Liquidity B2', bankId: 'bank_b', type: 'commercial', x: 440, y: 220, role: 'Escrow Account' },

    // Bank Gamma Cluster (Bottom-Right)
    { id: 'c_hub', name: 'Gamma Cross-Border', bankId: 'bank_c', type: 'cross_border', x: 480, y: 380, role: 'Consortium Node' },
    { id: 'c_wire', name: 'Offshore Mule C1', bankId: 'bank_c', type: 'mule', x: 650, y: 360, role: 'Rapid Outward Wire' },
    { id: 'c_exit', name: 'Fintech Exit C2', bankId: 'bank_c', type: 'exit', x: 340, y: 410, role: 'Digital Asset Cash-Out' },
  ], []);

  // Multi-hop edges dynamically adjusted per scenario
  const edges: TopologyEdge[] = useMemo(() => {
    const baseEdges: TopologyEdge[] = [
      // Background inter-bank bridges
      { id: 'bg_ab', source: 'a_hub', target: 'b_hub', amount: 12500, isLaundering: false, label: 'SEPA Inst Clearing' },
      { id: 'bg_bc', source: 'b_hub', target: 'c_hub', amount: 45000, isLaundering: false, label: 'TARGET2 RTGS' },
      { id: 'bg_ca', source: 'c_hub', target: 'a_hub', amount: 8900, isLaundering: false, label: 'SWIFT Correspondent' },
    ];

    if (selectedScenario === 'SCENARIO_1') {
      return [
        ...baseEdges,
        { id: 'sc1_1', source: 'a_mule1', target: 'a_mule2', amount: 9450, scenarioId: 'SCENARIO_1', isLaundering: true, hopIndex: 0, label: 'Internal Smurfing ($9,450)' },
        { id: 'sc1_2', source: 'a_mule2', target: 'a_hub', amount: 9380, scenarioId: 'SCENARIO_1', isLaundering: true, hopIndex: 1, label: 'Consolidation Leg' },
      ];
    } else if (selectedScenario === 'SCENARIO_2') {
      return [
        ...baseEdges,
        { id: 'sc2_1', source: 'a_mule1', target: 'b_layer', amount: 22000, scenarioId: 'SCENARIO_2', isLaundering: true, hopIndex: 0, label: 'Cross-Bank Wire (A -> B)' },
        { id: 'sc2_2', source: 'b_layer', target: 'b_pool', amount: 21500, scenarioId: 'SCENARIO_2', isLaundering: true, hopIndex: 1, label: 'Layering Hop inside B' },
      ];
    } else if (selectedScenario === 'SCENARIO_3') {
      return [
        ...baseEdges,
        { id: 'sc3_1', source: 'a_mule2', target: 'b_layer', amount: 42000, scenarioId: 'SCENARIO_3', isLaundering: true, hopIndex: 0, label: 'Hop 0: A -> B ($42k)' },
        { id: 'sc3_2', source: 'b_layer', target: 'c_wire', amount: 41200, scenarioId: 'SCENARIO_3', isLaundering: true, hopIndex: 1, label: 'Hop 1: B -> C ($41.2k) [Invisible to A]' },
        { id: 'sc3_3', source: 'c_wire', target: 'a_mule1', amount: 39800, scenarioId: 'SCENARIO_3', isLaundering: true, hopIndex: 2, label: 'Hop 2: C -> A ($39.8k) [Cycle Closure]' },
      ];
    } else if (selectedScenario === 'SCENARIO_4') {
      return [
        ...baseEdges,
        { id: 'sc4_1', source: 'a_mule1', target: 'b_pool', amount: 9200, scenarioId: 'SCENARIO_4', isLaundering: true, hopIndex: 0, label: 'Smurf Leg 1 ($9.2k)' },
        { id: 'sc4_2', source: 'a_mule2', target: 'b_pool', amount: 9600, scenarioId: 'SCENARIO_4', isLaundering: true, hopIndex: 0, label: 'Smurf Leg 2 ($9.6k)' },
        { id: 'sc4_3', source: 'b_pool', target: 'c_wire', amount: 184000, scenarioId: 'SCENARIO_4', isLaundering: true, hopIndex: 1, label: 'Consolidated Wire ($184k)' },
      ];
    } else if (selectedScenario === 'SCENARIO_7') {
      return [
        ...baseEdges,
        { id: 'sc7_1', source: 'a_mule1', target: 'c_exit', amount: 34000, scenarioId: 'SCENARIO_7', isLaundering: true, hopIndex: 0, label: 'Zero-Positive Attack on C ($34k)' },
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

  return (
    <div className={`flex flex-col gap-6 rounded-2xl border border-slate-800 bg-slate-900/90 p-6 backdrop-blur-md shadow-2xl ${className}`}>
      {/* Top Controls: Scenario Selector & Horizon Mode */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 mr-2 flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5 text-cyan-400" /> Scenario:
          </span>
          {Object.keys(SCENARIO_PREVIEWS).map((scId) => {
            const isSelected = selectedScenario === scId;
            return (
              <button
                key={scId}
                onClick={() => onScenarioChange?.(scId)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 ${
                  isSelected
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm shadow-cyan-500/20'
                    : 'bg-slate-800/60 text-slate-400 border border-slate-700/50 hover:bg-slate-800 hover:text-slate-200'
                }`}
              >
                {scId.replace('_', ' ')}
              </button>
            );
          })}
        </div>

        {/* Information Horizon Filter */}
        <div className="flex items-center gap-2 rounded-xl bg-slate-950/80 p-1 border border-slate-800">
          <span className="text-xs font-mono text-slate-400 px-2 flex items-center gap-1">
            <Lock className="h-3 w-3 text-amber-400" /> Horizon:
          </span>
          {(['global', 'bank_a', 'bank_b', 'bank_c'] as const).map((hKey) => {
            const isActive = horizon === hKey;
            const labelMap = {
              global: 'Global Oracle',
              bank_a: 'Bank Alpha',
              bank_b: 'Bank Beta',
              bank_c: 'Bank Gamma',
            };
            return (
              <button
                key={hKey}
                onClick={() => setHorizon(hKey)}
                className={`px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                  isActive
                    ? 'bg-indigo-600 text-white font-semibold shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                }`}
              >
                {labelMap[hKey]}
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Graph Visualization Stage */}
      <div className="relative h-[480px] w-full rounded-xl border border-slate-800/80 bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 overflow-hidden shadow-inner">
        {/* Institutional Cluster Background Zones */}
        <div className="absolute inset-0 pointer-events-none">
          {/* Bank Alpha Zone */}
          <div className="absolute left-6 top-10 w-72 h-80 rounded-2xl border border-dashed border-sky-500/20 bg-sky-950/10 p-4">
            <div className="flex items-center gap-2 text-sky-400 text-xs font-semibold uppercase tracking-wider">
              <Building2 className="h-4 w-4" /> Bank Alpha (Retail)
            </div>
          </div>

          {/* Bank Beta Zone */}
          <div className="absolute right-8 top-8 w-80 h-56 rounded-2xl border border-dashed border-indigo-500/20 bg-indigo-950/10 p-4">
            <div className="flex items-center gap-2 text-indigo-400 text-xs font-semibold uppercase tracking-wider">
              <Building2 className="h-4 w-4" /> Bank Beta (Commercial)
            </div>
          </div>

          {/* Bank Gamma Zone */}
          <div className="absolute right-12 bottom-6 w-96 h-52 rounded-2xl border border-dashed border-emerald-500/20 bg-emerald-950/10 p-4">
            <div className="flex items-center gap-2 text-emerald-400 text-xs font-semibold uppercase tracking-wider">
              <Building2 className="h-4 w-4" /> Bank Gamma (Cross-Border)
            </div>
          </div>
        </div>

        {/* SVG Network Canvas */}
        <svg className="absolute inset-0 h-full w-full">
          <defs>
            <linearGradient id="fraudPulse" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#f43f5e" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#fbbf24" stopOpacity="0.9" />
            </linearGradient>
            <linearGradient id="benignFlow" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#818cf8" stopOpacity="0.3" />
            </linearGradient>
            <marker id="arrow-fraud" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#f43f5e" />
            </marker>
            <marker id="arrow-benign" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
            </marker>
          </defs>

          {/* Render Edges */}
          {edges.map((edge) => {
            const src = nodes.find((n) => n.id === edge.source);
            const tgt = nodes.find((n) => n.id === edge.target);
            if (!src || !tgt) return null;

            const isVisible = isEdgeVisibleInHorizon(edge);
            const isBlindSpot = isBlindSpotEdge(edge);
            const isHovered = activeEdgeHover === edge.id;

            return (
              <g key={edge.id} className="transition-opacity duration-300">
                <line
                  x1={src.x}
                  y1={src.y}
                  x2={tgt.x}
                  y2={tgt.y}
                  stroke={
                    isBlindSpot
                      ? '#475569'
                      : edge.isLaundering
                      ? 'url(#fraudPulse)'
                      : 'url(#benignFlow)'
                  }
                  strokeWidth={isHovered ? 4.5 : edge.isLaundering ? 3.5 : 1.5}
                  strokeDasharray={isBlindSpot ? '4,4' : edge.isLaundering ? '8,4' : undefined}
                  strokeOpacity={isBlindSpot ? 0.35 : isVisible ? 1.0 : 0.15}
                  markerEnd={edge.isLaundering && !isBlindSpot ? 'url(#arrow-fraud)' : 'url(#arrow-benign)'}
                  onMouseEnter={() => setActiveEdgeHover(edge.id)}
                  onMouseLeave={() => setActiveEdgeHover(null)}
                  className="cursor-pointer"
                />

                {/* Edge Label for Laundering Chains */}
                {edge.isLaundering && (
                  <text
                    x={(src.x + tgt.x) / 2}
                    y={(src.y + tgt.y) / 2 - 8}
                    fill={isBlindSpot ? '#94a3b8' : '#fecdd3'}
                    fontSize={10}
                    fontFamily="monospace"
                    textAnchor="middle"
                    className="select-none pointer-events-none font-semibold"
                  >
                    {isBlindSpot ? '🔒 BLIND SPOT' : edge.label || `$${edge.amount.toLocaleString()}`}
                  </text>
                )}
              </g>
            );
          })}

          {/* Render Nodes */}
          {nodes.map((node) => {
            const isBankHub = node.id.endsWith('_hub');
            const isNodeDimmed = horizon !== 'global' && node.bankId !== horizon;

            return (
              <g
                key={node.id}
                transform={`translate(${node.x}, ${node.y})`}
                className={`cursor-pointer transition-opacity duration-300 ${isNodeDimmed ? 'opacity-35' : 'opacity-100'}`}
              >
                {/* Node Glow Circle */}
                {isBankHub ? (
                  <circle
                    r={20}
                    fill="#1e293b"
                    stroke={node.bankId === 'bank_a' ? '#38bdf8' : node.bankId === 'bank_b' ? '#818cf8' : '#34d399'}
                    strokeWidth={2.5}
                    className="filter drop-shadow-md"
                  />
                ) : (
                  <circle
                    r={12}
                    fill={node.type === 'mule' ? '#e11d48' : '#334155'}
                    stroke={node.type === 'mule' ? '#fda4af' : '#64748b'}
                    strokeWidth={1.5}
                  />
                )}

                {/* Node Title */}
                <text
                  y={isBankHub ? 32 : 22}
                  fill="#e2e8f0"
                  fontSize={isBankHub ? 11 : 9}
                  fontWeight={isBankHub ? 'bold' : 'normal'}
                  textAnchor="middle"
                  className="select-none pointer-events-none"
                >
                  {node.name}
                </text>
              </g>
            );
          })}
        </svg>

        {/* Information Horizon Alert Banner */}
        <AnimatePresence>
          {horizon !== 'global' && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="absolute top-4 left-1/2 -translate-x-1/2 flex items-center gap-2 rounded-xl bg-amber-500/10 border border-amber-500/30 px-4 py-1.5 text-xs text-amber-300 backdrop-blur-md shadow-lg"
            >
              <EyeOff className="h-4 w-4 text-amber-400" />
              <span>
                <strong>Information Horizon Active:</strong> You are viewing through the private visibility horizon of{' '}
                <span className="font-semibold underline">{horizon.replace('_', ' ').toUpperCase()}</span>. Non-incident edges are masked.
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Live Scenario Overlay Card */}
        <div className="absolute bottom-4 left-4 max-w-md rounded-xl border border-slate-800 bg-slate-950/90 p-4 backdrop-blur-md shadow-xl">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <span className="text-xs font-mono font-semibold text-cyan-400 uppercase tracking-wider">
              {scenarioData.typology}
            </span>
            <span className="text-[10px] font-mono rounded bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 px-1.5 py-0.5">
              +{scenarioData.deltaUplift.toFixed(1)}% Uplift
            </span>
          </div>
          <h4 className="text-sm font-bold text-slate-100 mb-1">{scenarioData.title}</h4>
          <p className="text-xs text-slate-300 leading-relaxed mb-3">{scenarioData.description}</p>
          <div className="text-[11px] text-amber-300/90 bg-amber-950/20 border border-amber-500/20 rounded p-2 flex items-start gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-0.5" />
            <span>
              <strong>Silo Vulnerability:</strong> {scenarioData.vulnerability}
            </span>
          </div>
        </div>
      </div>

      {/* Metrics HUD: Isolated vs Federated Uplift */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
            <ShieldCheck className="h-4 w-4 text-rose-400" /> Isolated Silo Recall
          </span>
          <div className="text-2xl font-bold font-mono text-rose-400">
            {scenarioData.isolatedRecall.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400">Single-institution private visibility</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
            <Zap className="h-4 w-4 text-cyan-400" /> Federated Consensus
          </span>
          <div className="text-2xl font-bold font-mono text-cyan-400">
            {scenarioData.federatedRecall.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400">Collaborative zero-leakage model</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
            <Share2 className="h-4 w-4 text-emerald-400" /> Collaborative Uplift (Δ)
          </span>
          <div className="text-2xl font-bold font-mono text-emerald-400">
            +{scenarioData.deltaUplift.toFixed(1)}%
          </div>
          <span className="text-[11px] text-slate-400">Detection gain over isolated banking</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
          <span className="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
            <Lock className="h-4 w-4 text-indigo-400" /> Privacy & Information Horizon
          </span>
          <div className="text-2xl font-bold font-mono text-indigo-400">
            ZERO RAW PII
          </div>
          <span className="text-[11px] text-slate-400">Zero raw PII or cross-bank edge leakage</span>
        </div>
      </div>
    </div>
  );
}
