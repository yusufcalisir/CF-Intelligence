import { useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useEntities, useGraph, useGraphStats } from '../api/queries';
import { ENTITY_TYPE_COLORS, BANK_NAMES } from '../api/types';

export default function GraphPage() {
  const [selectedEntityId, setSelectedEntityId] = useState<string | undefined>();
  const [depth, setDepth] = useState(2);
  const [searchQuery, setSearchQuery] = useState('');
  const [hoveredStat, setHoveredStat] = useState<string | null>(null);
  const { data: entities } = useEntities();
  const { data: graphData } = useGraph(selectedEntityId, depth);
  const { data: graphStats } = useGraphStats();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useMemo(() => {
    if (graphData) {
      setNodes(graphData.nodes as unknown as Node[]);
      setEdges(graphData.edges as unknown as Edge[]);
    }
  }, [graphData, setNodes, setEdges]);

  const filteredEntities = useMemo(() => {
    if (!entities) return [];
    if (!searchQuery) return entities.slice(0, 25);
    return entities
      .filter(
        (e) =>
          e.display_label.toLowerCase().includes(searchQuery.toLowerCase()) ||
          e.privacy_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
          e.entity_type.toLowerCase().includes(searchQuery.toLowerCase()),
      )
      .slice(0, 25);
  }, [entities, searchQuery]);

  const onNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedEntityId(node.id);
  }, []);

  const statsData = graphStats
    ? [
        { key: 'nodes',    label: 'Total Nodes',    value: graphStats.total_nodes,  icon: '◉', color: '#6366f1', glow: 'rgba(99,102,241,0.2)' },
        { key: 'edges',    label: 'Connections',    value: graphStats.total_edges,  icon: '⟶', color: '#22d3ee', glow: 'rgba(34,211,238,0.2)' },
        { key: 'clusters', label: 'Fraud Clusters', value: graphStats.cluster_count, icon: '⬡', color: '#f59e0b', glow: 'rgba(245,158,11,0.2)' },
        { key: 'types',    label: 'Entity Types',   value: Object.keys(graphStats.nodes_by_type).length, icon: '◈', color: '#10b981', glow: 'rgba(16,185,129,0.2)' },
      ]
    : [];

  return (
    <div className="space-y-6 max-w-7xl mx-auto w-full min-w-0">
      {/* ── HEADER ────────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="p-5 sm:p-6 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-4"
      >
        <div className="flex items-start sm:items-center gap-3.5 min-w-0">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center text-lg bg-indigo-600/15 border border-indigo-500/30 text-indigo-400 shrink-0 shadow-[0_0_20px_rgba(99,102,241,0.2)]">
            🕸️
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2.5 flex-wrap">
              <h1 className="text-xl sm:text-2xl font-black text-white tracking-tight">
                Entity Relationship Graph
              </h1>
              <span className="text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-semibold">
                Federated Identity Mesh
              </span>
            </div>
            <p className="text-xs sm:text-sm text-slate-400 mt-1 max-w-2xl leading-relaxed">
              Interactive multi-bank identity topology across transactions, shared device fingerprints, and confidential risk clusters.
            </p>
          </div>
        </div>

        {graphStats?.database_backend && (
          <div className="self-start sm:self-auto shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-xl bg-white/5 border border-white/10 text-xs font-mono text-slate-300">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse inline-block" />
            <span>Backend:</span>
            <span className="text-white font-bold">{graphStats.database_backend}</span>
          </div>
        )}
      </motion.div>

      {/* ── STATS ROW ─────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        {graphStats
          ? statsData.map((stat, i) => (
              <motion.div
                key={stat.key}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.06 }}
                onMouseEnter={() => setHoveredStat(stat.key)}
                onMouseLeave={() => setHoveredStat(null)}
                className="relative overflow-hidden rounded-2xl p-4 sm:p-5 bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl transition-all duration-300 group hover:border-indigo-500/40"
                style={{
                  boxShadow: hoveredStat === stat.key ? `0 0 25px ${stat.glow}` : 'none',
                }}
              >
                <div
                  className="absolute -top-6 -right-6 w-20 h-20 rounded-full blur-2xl transition-opacity duration-300"
                  style={{ background: stat.color, opacity: hoveredStat === stat.key ? 0.25 : 0.08 }}
                />
                <div className="relative z-10 flex items-center justify-between">
                  <div className="text-[11px] uppercase tracking-wider text-slate-400 font-semibold truncate">
                    {stat.label}
                  </div>
                  <span className="text-sm font-mono" style={{ color: stat.color }}>{stat.icon}</span>
                </div>
                <div className="text-2xl sm:text-3xl font-black font-mono text-white mt-2 mb-0.5">
                  {stat.value}
                </div>
              </motion.div>
            ))
          : Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="rounded-2xl h-24 animate-pulse bg-[#090a1f]/80 border border-white/10" />
            ))}
      </div>

      {/* ── CONTROLS ROW ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Traversal Depth Control */}
        <motion.div
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.15 }}
          className="lg:col-span-4 p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl flex flex-col justify-between"
        >
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
                <span>🎯</span>
                <span>Traversal Hops</span>
              </h3>
              <span className="px-2 py-0.5 rounded-lg text-xs font-mono font-bold bg-indigo-600/20 text-indigo-300 border border-indigo-500/30">
                Depth {depth}
              </span>
            </div>
            <p className="text-[11.5px] text-slate-400 leading-relaxed mb-4">
              Number of recursive entity hops analyzed across consortium boundaries.
            </p>
          </div>

          <div className="grid grid-cols-4 gap-2">
            {[1, 2, 3, 4].map((hop) => (
              <button
                key={hop}
                onClick={() => setDepth(hop)}
                className={`py-2.5 px-3 rounded-xl text-xs font-mono font-bold transition-all cursor-pointer flex flex-col items-center justify-center gap-1 active:scale-95 ${
                  depth === hop
                    ? 'bg-gradient-to-r from-indigo-600 to-purple-600 text-white shadow-[0_0_15px_rgba(99,102,241,0.4)] border border-indigo-400/40'
                    : 'bg-white/4 border border-white/8 text-slate-300 hover:text-white hover:bg-white/8'
                }`}
              >
                <span>{hop} {hop === 1 ? 'Hop' : 'Hops'}</span>
                <span className="text-[9px] font-sans text-slate-300">
                  {hop === 1 ? 'Direct' : hop === 2 ? 'Standard' : hop === 3 ? 'Deep' : 'Exhaustive'}
                </span>
              </button>
            ))}
          </div>
        </motion.div>

        {/* Search & Entity Quick Selector */}
        <motion.div
          initial={{ opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
          className="lg:col-span-8 p-5 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-xl flex flex-col justify-between"
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <span>🔎</span>
              <span>Entity Directory & Resolver</span>
            </h3>
            {filteredEntities.length > 0 && (
              <span className="text-[10px] px-2.5 py-0.5 rounded-full font-mono bg-white/5 border border-white/10 text-slate-300">
                {filteredEntities.length} active entities
              </span>
            )}
          </div>

          {/* Search Input */}
          <div className="relative mb-3">
            <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 text-xs pointer-events-none">
              🔍
            </span>
            <input
              type="text"
              placeholder="Search by display label, entity type, or HMAC privacy ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-xs font-mono text-white placeholder-slate-400 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all"
            />
          </div>

          {/* Quick Entity Pills */}
          <div className="flex flex-wrap gap-2 max-h-36 overflow-y-auto pr-1">
            {filteredEntities.slice(0, 16).map((entity) => {
              const isSelected = selectedEntityId === entity.id;
              const typeColor = ENTITY_TYPE_COLORS[entity.entity_type] || '#6366f1';
              return (
                <button
                  key={entity.id}
                  onClick={() => setSelectedEntityId(entity.id)}
                  className={`px-3 py-1.5 rounded-xl text-xs flex items-center gap-2 transition-all cursor-pointer border ${
                    isSelected
                      ? 'bg-indigo-600/30 border-indigo-500/60 text-white shadow-[0_0_12px_rgba(99,102,241,0.3)]'
                      : 'bg-white/4 border-white/8 text-slate-300 hover:text-white hover:bg-white/8'
                  }`}
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ backgroundColor: typeColor, boxShadow: `0 0 6px ${typeColor}80` }}
                  />
                  <span className="font-mono font-medium truncate max-w-[120px] sm:max-w-[160px]">
                    {entity.display_label}
                  </span>
                  <span className="text-[9.5px] font-mono text-slate-300">
                    {BANK_NAMES[entity.bank_id] || entity.bank_id}
                  </span>
                  {entity.alert_count > 0 && (
                    <span className="px-1.5 py-0.2 rounded-full text-[9px] font-bold bg-rose-500/20 border border-rose-500/30 text-rose-300">
                      {entity.alert_count}
                    </span>
                  )}
                </button>
              );
            })}
            {!filteredEntities.length && (
              <div className="w-full py-4 text-center text-xs text-slate-400 font-mono">
                No matching entities found in current consortium state.
              </div>
            )}
          </div>
        </motion.div>
      </div>

      {/* ── LEGEND BAR ────────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.25 }}
        className="p-4 rounded-2xl bg-[#090a1f]/80 border border-white/10 backdrop-blur-xl shadow-lg flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs"
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 font-mono shrink-0">
            Node Types:
          </span>
          {Object.entries(ENTITY_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: color, boxShadow: `0 0 6px ${color}80` }}
              />
              <span className="text-[11.5px] capitalize text-slate-300 font-medium">
                {type.replace('_', ' ')}
              </span>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-400 font-mono border-t md:border-t-0 pt-2 md:pt-0 border-white/6">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-indigo-500 inline-block" /> Transaction
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 border-t border-dashed border-amber-400 inline-block" /> Shared Device
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 border-t border-dotted border-rose-500 inline-block" /> Cross-Bank Ring
          </span>
        </div>
      </motion.div>

      {/* ── GRAPH CANVAS ──────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, scale: 0.99 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 0.3 }}
        className="rounded-2xl overflow-hidden bg-[#050514]/90 border border-white/10 shadow-2xl relative h-[450px] sm:h-[600px] w-full"
      >
        <AnimatePresence mode="wait">
          {!selectedEntityId ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center justify-center h-full p-6 text-center"
              style={{ background: 'radial-gradient(ellipse at center, rgba(99,102,241,0.06) 0%, transparent 70%)' }}
            >
              <div className="max-w-md space-y-4">
                {/* Animated Pulsing Orbit Visual */}
                <div className="relative w-24 h-24 mx-auto">
                  <div className="absolute inset-0 rounded-full border border-indigo-500/20 animate-ping" />
                  <div className="absolute inset-2 rounded-full border border-cyan-500/30 animate-pulse" />
                  <div className="absolute inset-0 flex items-center justify-center text-3xl">
                    🕸️
                  </div>
                </div>

                <div>
                  <h3 className="text-base sm:text-lg font-bold text-white mb-1">
                    Select an Entity to Trace Topology
                  </h3>
                  <p className="text-xs sm:text-sm text-slate-400 leading-relaxed">
                    Pick a customer, device, card, or merchant from the directory above to visualize cross-bank transaction links and fraud rings.
                  </p>
                </div>

                {/* Quick Selection Helper */}
                {entities && entities.length > 0 && (
                  <div className="pt-2 flex flex-wrap items-center justify-center gap-2">
                    <span className="text-[11px] font-mono text-slate-500">Quick trace:</span>
                    {entities.slice(0, 3).map((e) => (
                      <button
                        key={e.id}
                        onClick={() => setSelectedEntityId(e.id)}
                        className="px-3 py-1 rounded-xl text-xs font-mono font-semibold bg-white/5 hover:bg-white/10 border border-white/10 text-indigo-300 hover:text-white transition-all cursor-pointer active:scale-95"
                      >
                        {e.display_label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          ) : !graphData?.nodes.length ? (
            <motion.div
              key="no-data"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center justify-center h-full p-6 text-center"
            >
              <div className="max-w-md space-y-3">
                <div className="text-4xl">📊</div>
                <h3 className="text-base font-bold text-white">No Direct Relationships at Depth {depth}</h3>
                <p className="text-xs text-slate-400">
                  This entity has no cross-bank connections within {depth} hops. Try increasing traversal hops to 3 or 4.
                </p>
                <button
                  onClick={() => setDepth((d) => Math.min(4, d + 1))}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 transition-all cursor-pointer"
                >
                  Increase to {Math.min(4, depth + 1)} Hops
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.div key="graph" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="h-full w-full">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                fitView
                proOptions={{ hideAttribution: true }}
                style={{ background: 'transparent' }}
              >
                <Background color="rgba(255,255,255,0.06)" gap={24} size={1} />
                <Controls
                  showInteractive={false}
                  style={{
                    background: '#090a1f',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '12px',
                    boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
                  }}
                />
                <MiniMap
                  style={{
                    background: '#090a1f',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '12px',
                  }}
                  maskColor="rgba(0,0,0,0.6)"
                  nodeColor={(node) => {
                    const entityType = (node.data as { entity_type?: string })?.entity_type || 'customer';
                    return ENTITY_TYPE_COLORS[entityType] || '#6366f1';
                  }}
                />
              </ReactFlow>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
