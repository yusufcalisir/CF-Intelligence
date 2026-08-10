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
import type { Entity } from '../api/types';

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
    if (!searchQuery) return entities.slice(0, 20);
    return entities
      .filter(
        (e) =>
          e.display_label.toLowerCase().includes(searchQuery.toLowerCase()) ||
          e.privacy_id.toLowerCase().includes(searchQuery.toLowerCase()),
      )
      .slice(0, 20);
  }, [entities, searchQuery]);

  const onNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedEntityId(node.id);
  }, []);

  const statsData = graphStats
    ? [
        { key: 'nodes',    label: 'Total Nodes',   value: graphStats.total_nodes,  icon: '◉', color: '#6366f1', bg: 'rgba(99,102,241,0.12)' },
        { key: 'edges',    label: 'Connections',    value: graphStats.total_edges,  icon: '⟶', color: '#22d3ee', bg: 'rgba(34,211,238,0.12)' },
        { key: 'clusters', label: 'Fraud Clusters', value: graphStats.cluster_count, icon: '⬡', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
        { key: 'types',    label: 'Entity Types',   value: Object.keys(graphStats.nodes_by_type).length, icon: '◈', color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
      ]
    : [];

  return (
    <div className="space-y-5">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="flex items-start justify-between gap-4 flex-wrap"
      >
        <div>
          <div className="flex items-center gap-3 mb-1">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-base shrink-0"
              style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)' }}
            >
              🕸️
            </div>
            <h1 className="text-2xl font-bold gradient-text">Entity Relationship Graph</h1>
          </div>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl ml-11">
            Interactive graph of entities connected by transactions, shared devices, and cross-institution
            intelligence. Clusters often indicate fraud rings.
          </p>
        </div>
        {graphStats?.database_backend && (
          <div
            className="shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold"
            style={{
              background: graphStats.database_backend.toLowerCase().includes('neo4j')
                ? 'rgba(0,140,193,0.15)'
                : 'rgba(168,85,247,0.15)',
              border: `1px solid ${graphStats.database_backend.toLowerCase().includes('neo4j') ? 'rgba(0,140,193,0.4)' : 'rgba(168,85,247,0.4)'}`,
              color: graphStats.database_backend.toLowerCase().includes('neo4j') ? '#008CC1' : '#a855f7',
            }}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse inline-block" />
            {graphStats.database_backend}
          </div>
        )}
      </motion.div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {graphStats
          ? statsData.map((stat, i) => (
              <motion.div
                key={stat.key}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.07 }}
                onMouseEnter={() => setHoveredStat(stat.key)}
                onMouseLeave={() => setHoveredStat(null)}
                className="relative overflow-hidden rounded-xl p-4 cursor-default transition-all duration-300"
                style={{
                  background: hoveredStat === stat.key ? stat.bg : 'var(--color-surface)',
                  border: `1px solid ${hoveredStat === stat.key ? stat.color + '55' : 'var(--color-border)'}`,
                  boxShadow: hoveredStat === stat.key ? `0 0 20px ${stat.color}18` : 'none',
                }}
              >
                <div
                  className="absolute -top-4 -right-4 w-16 h-16 rounded-full blur-xl"
                  style={{ background: stat.color, opacity: hoveredStat === stat.key ? 0.2 : 0.06 }}
                />
                <div className="relative z-10">
                  <div className="text-lg font-mono mb-2" style={{ color: stat.color }}>{stat.icon}</div>
                  <div
                    className="text-3xl font-black font-mono leading-none mb-1 transition-colors duration-200"
                    style={{ color: hoveredStat === stat.key ? stat.color : 'var(--color-text)' }}
                  >
                    {stat.value}
                  </div>
                  <div className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)] font-semibold">
                    {stat.label}
                  </div>
                </div>
              </motion.div>
            ))
          : Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="rounded-xl h-24 animate-pulse" style={{ background: 'var(--color-surface)' }} />
            ))}
      </div>

      {/* Controls Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Traversal Depth */}
        <motion.div
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
          className="glass-card p-5"
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                Traversal Depth
              </h3>
              <p className="text-[11px] text-[var(--color-text-muted)] mt-0.5">
                Transaction hops from selected node
              </p>
            </div>
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center text-sm font-black font-mono"
              style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.35)', color: '#6366f1' }}
            >
              {depth}
            </div>
          </div>

          {/* Visual hop selector */}
          <div className="flex items-end gap-1 mb-3">
            {[1, 2, 3, 4].map((hop) => (
              <button
                key={hop}
                onClick={() => setDepth(hop)}
                className="flex-1 flex flex-col items-center gap-1.5 transition-all duration-200 group"
              >
                <div
                  className="w-full rounded-t-sm transition-all duration-200"
                  style={{
                    height: `${hop * 10}px`,
                    background: hop <= depth ? '#6366f1' : 'var(--color-border)',
                    boxShadow: hop <= depth ? '0 0 8px rgba(99,102,241,0.4)' : 'none',
                  }}
                />
                <span
                  className="text-[10px] font-mono font-bold transition-colors duration-200"
                  style={{ color: hop <= depth ? '#6366f1' : 'var(--color-text-muted)' }}
                >
                  {hop}
                </span>
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between text-[10px] text-[var(--color-text-muted)] mt-1">
            <span className="flex items-center gap-1"><span style={{ color: '#10b981' }}>●</span> Shallow · fast</span>
            <span className="flex items-center gap-1"><span style={{ color: '#ef4444' }}>●</span> Deep · more edges</span>
          </div>
        </motion.div>

        {/* Search & Filter */}
        <motion.div
          initial={{ opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.25 }}
          className="glass-card p-5 flex flex-col"
        >
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              Entity Search
            </h3>
            {filteredEntities.length > 0 && (
              <span
                className="text-[10px] px-2 py-0.5 rounded-full font-mono"
                style={{ background: 'rgba(99,102,241,0.15)', color: '#6366f1' }}
              >
                {filteredEntities.length} results
              </span>
            )}
          </div>

          <div className="relative mb-3">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] text-xs pointer-events-none">🔍</span>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by label or ID..."
              className="w-full pl-8 pr-3 py-2 text-xs rounded-lg focus:outline-none focus:ring-1"
              style={{
                background: 'var(--color-surface-alt)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
              }}
            />
          </div>

          <div className="flex-1 space-y-0.5 overflow-y-auto max-h-28 pr-1">
            {filteredEntities.map((entity) => (
              <EntityListItem
                key={entity.id}
                entity={entity}
                isSelected={selectedEntityId === entity.id}
                onClick={() => setSelectedEntityId(entity.id)}
              />
            ))}
            {!filteredEntities.length && (
              <div className="flex flex-col items-center justify-center py-3 text-[var(--color-text-muted)]">
                <span className="text-xl mb-1">🔍</span>
                <p className="text-[11px]">
                  {entities?.length ? 'No matches found' : 'Run simulation to populate entities'}
                </p>
              </div>
            )}
          </div>
        </motion.div>
      </div>

      {/* Legend bar */}
      <motion.div
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="glass-card px-5 py-3"
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)] shrink-0">
            Legend
          </span>
          {Object.entries(ENTITY_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1.5">
              <div
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: color, boxShadow: `0 0 5px ${color}70` }}
              />
              <span className="text-[11px] capitalize text-[var(--color-text-muted)]">{type.replace('_', ' ')}</span>
            </div>
          ))}
          <div className="ml-auto flex items-center gap-4 text-[10px] text-[var(--color-text-muted)]">
            <span className="flex items-center gap-1.5">
              <svg width="16" height="2" viewBox="0 0 16 2"><line x1="0" y1="1" x2="16" y2="1" stroke="#6366f1" strokeWidth="2"/></svg>
              Transaction
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="16" height="2" viewBox="0 0 16 2"><line x1="0" y1="1" x2="16" y2="1" stroke="#f59e0b" strokeWidth="2" strokeDasharray="3,2"/></svg>
              Shared Device
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="16" height="2" viewBox="0 0 16 2"><line x1="0" y1="1" x2="16" y2="1" stroke="#ef4444" strokeWidth="2" strokeDasharray="1,2"/></svg>
              Cross-bank
            </span>
          </div>
        </div>
      </motion.div>

      {/* Graph Canvas */}
      <motion.div
        initial={{ opacity: 0, scale: 0.98 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 0.15 }}
        className="glass-card overflow-hidden"
        style={{ height: '560px' }}
      >
        <AnimatePresence mode="wait">
          {!selectedEntityId ? (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center justify-center h-full"
              style={{ background: 'radial-gradient(ellipse at center, rgba(99,102,241,0.04) 0%, transparent 70%)' }}
            >
              <div className="text-center p-8 max-w-md">
                {/* Animated network illustration */}
                <div className="relative w-28 h-28 mx-auto mb-6">
                  <svg className="absolute inset-0 w-full h-full" viewBox="0 0 112 112">
                    {[[56,56,18,22],[56,56,90,18],[56,56,14,70],[56,56,96,72],[56,56,56,14]].map(([x1,y1,x2,y2],i) => (
                      <motion.line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                        stroke="#6366f1" strokeWidth="1.5" strokeOpacity="0.35" strokeDasharray="4,3"
                        animate={{ strokeOpacity: [0.15, 0.55, 0.15] }}
                        transition={{ repeat: Infinity, duration: 2.2, delay: i * 0.35 }}
                      />
                    ))}
                  </svg>
                  {[
                    { cx: 56, cy: 56, r: 11, color: '#6366f1', delay: 0 },
                    { cx: 18, cy: 22, r: 7, color: '#22d3ee', delay: 0.2 },
                    { cx: 90, cy: 18, r: 6, color: '#10b981', delay: 0.4 },
                    { cx: 14, cy: 70, r: 6, color: '#f59e0b', delay: 0.6 },
                    { cx: 96, cy: 72, r: 7, color: '#ef4444', delay: 0.8 },
                    { cx: 56, cy: 14, r: 5, color: '#a855f7', delay: 1.0 },
                  ].map((n, i) => (
                    <motion.div
                      key={i}
                      className="absolute rounded-full"
                      style={{
                        width: n.r * 2, height: n.r * 2,
                        background: n.color,
                        left: `${(n.cx / 112) * 100}%`, top: `${(n.cy / 112) * 100}%`,
                        transform: 'translate(-50%, -50%)',
                        boxShadow: `0 0 ${n.r * 2}px ${n.color}90`,
                      }}
                      animate={{ scale: [1, 1.2, 1], opacity: [0.7, 1, 0.7] }}
                      transition={{ repeat: Infinity, duration: 2.2, delay: n.delay }}
                    />
                  ))}
                </div>

                <p className="text-lg font-bold mb-2" style={{ color: 'var(--color-text)' }}>
                  Select an Entity to Explore
                </p>
                <p className="text-sm text-[var(--color-text-muted)] leading-relaxed mb-5">
                  Choose a customer, device, merchant, or card from the search panel to visualize
                  resolved connections and cross-bank fraud clusters.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-2">
                  {['Customer', 'Device', 'Merchant', 'Card', 'Email', 'IP Address'].map((type) => (
                    <span
                      key={type}
                      className="px-3 py-1 rounded-full text-xs font-medium"
                      style={{ background: 'var(--color-surface-alt)', border: '1px solid var(--color-border)', color: 'var(--color-text-muted)' }}
                    >
                      {type}
                    </span>
                  ))}
                </div>
              </div>
            </motion.div>
          ) : !graphData?.nodes.length ? (
            <motion.div
              key="no-data"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center justify-center h-full"
            >
              <div className="text-center p-8">
                <div className="text-4xl mb-3">📊</div>
                <p className="text-lg font-semibold mb-1" style={{ color: 'var(--color-text)' }}>No Relationships Found</p>
                <p className="text-sm text-[var(--color-text-muted)]">
                  This entity has no registered transaction hops at depth {depth}. Try increasing traversal depth.
                </p>
              </div>
            </motion.div>
          ) : (
            <motion.div key="graph" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="h-full">
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
                <Background color="var(--color-border)" gap={24} size={1} />
                <Controls
                  showInteractive={false}
                  style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: '10px' }}
                />
                <MiniMap
                  style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: '10px' }}
                  maskColor="rgba(0,0,0,0.5)"
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

function EntityListItem({
  entity,
  isSelected,
  onClick,
}: {
  entity: Entity;
  isSelected: boolean;
  onClick: () => void;
}) {
  const color = ENTITY_TYPE_COLORS[entity.entity_type] || '#6b7280';

  return (
    <button
      onClick={onClick}
      className="w-full text-left px-2.5 py-2 rounded-lg text-xs transition-all duration-150"
      style={{
        background: isSelected ? `${color}18` : 'transparent',
        border: `1px solid ${isSelected ? color + '50' : 'transparent'}`,
      }}
    >
      <div className="flex items-center gap-2">
        <div
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: color, boxShadow: `0 0 5px ${color}60` }}
        />
        <span className="font-mono font-semibold" style={{ color: isSelected ? color : 'var(--color-text)' }}>
          {entity.display_label}
        </span>
        {entity.alert_count > 0 && (
          <span
            className="ml-auto px-1.5 py-0.5 rounded text-[9px] font-bold"
            style={{ background: 'rgba(239,68,68,0.2)', color: '#ef4444' }}
          >
            {entity.alert_count}
          </span>
        )}
      </div>
      <div className="text-[10px] text-[var(--color-text-muted)] ml-4 mt-0.5">
        {BANK_NAMES[entity.bank_id] || entity.bank_id} · {entity.risk_level}
      </div>
    </button>
  );
}
