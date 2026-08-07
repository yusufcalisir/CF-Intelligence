import React, { useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { Network, Search, Filter, ShieldAlert, Zap, Layers } from 'lucide-react';
import { GraphEdge, GraphNode } from '../types';

interface GraphVisualizerProps {
  selectedBank: string;
}

export const GraphVisualizer: React.FC<GraphVisualizerProps> = ({ selectedBank }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [layoutName, setLayoutName] = useState('cose');

  // Mock graph data for financial entity network
  const mockNodes: GraphNode[] = [
    { id: 'CUST-5ac28f', label: 'Customer A (john.doe@email.com)', type: 'CUSTOMER', riskScore: 850.0, bankId: 'bank_a', degree: 8 },
    { id: 'CUST-99f1b2', label: 'Customer B (m.smith@email.com)', type: 'CUSTOMER', riskScore: 240.0, bankId: 'bank_b', degree: 3 },
    { id: 'MERCH-crypto', label: 'Crypto Exchange Ltd', type: 'MERCH', riskScore: 920.0, bankId: 'bank_a', degree: 14 },
    { id: 'DEV-mobile-app', label: 'Mobile Device #4421', type: 'DEVICE', riskScore: 680.0, bankId: 'bank_b', degree: 5 },
    { id: 'IP-185.220.101', label: 'TOR Exit Node #88', type: 'IP', riskScore: 990.0, bankId: 'bank_a', degree: 12 },
    { id: 'BANK-bank_a', label: 'Bank A (JPMorgan)', type: 'BANK', riskScore: 100.0, bankId: 'bank_a', degree: 20 },
    { id: 'BANK-bank_b', label: 'Bank B (BofA)', type: 'BANK', riskScore: 100.0, bankId: 'bank_b', degree: 18 },
  ];

  const mockEdges: GraphEdge[] = [
    { id: 'e1', source: 'CUST-5ac28f', target: 'MERCH-crypto', weight: 0.95, relation: 'HIGH_AMT_TXN' },
    { id: 'e2', source: 'CUST-5ac28f', target: 'DEV-mobile-app', weight: 0.80, relation: 'SHARED_DEVICE' },
    { id: 'e3', source: 'DEV-mobile-app', target: 'IP-185.220.101', weight: 0.99, relation: 'TOR_CONNECTION' },
    { id: 'e4', source: 'MERCH-crypto', target: 'IP-185.220.101', weight: 0.88, relation: 'IP_GEO_MISMATCH' },
    { id: 'e5', source: 'CUST-99f1b2', target: 'MERCH-crypto', weight: 0.20, relation: 'NORMAL_PAYMENT' },
    { id: 'e6', source: 'CUST-5ac28f', target: 'BANK-bank_a', weight: 1.0, relation: 'ACCOUNT_HOLDER' },
    { id: 'e7', source: 'CUST-99f1b2', target: 'BANK-bank_b', weight: 1.0, relation: 'ACCOUNT_HOLDER' },
  ];

  const getNodeColor = (score: number) => {
    if (score >= 800) return '#EF4444'; // Red
    if (score >= 600) return '#F97316'; // Orange
    if (score >= 300) return '#EAB308'; // Yellow
    return '#10B981'; // Green
  };

  useEffect(() => {
    if (!containerRef.current) return;

    const filteredNodes = selectedBank === 'all'
      ? mockNodes
      : mockNodes.filter((n) => n.bankId === selectedBank || n.type === 'BANK');

    const filteredNodeIds = new Set(filteredNodes.map((n) => n.id));
    const filteredEdges = mockEdges.filter(
      (e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target)
    );

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...filteredNodes.map((n) => ({
          data: { ...n, color: getNodeColor(n.riskScore) },
        })),
        ...filteredEdges.map((e) => ({ data: e })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(color)',
            label: 'data(id)',
            color: '#F8FAFC',
            'font-size': '11px',
            'font-weight': 'bold',
            'text-valign': 'bottom',
            'text-margin-y': 5,
            width: 'mapData(degree, 1, 20, 24, 48)',
            height: 'mapData(degree, 1, 20, 24, 48)',
            'border-width': 2,
            'border-color': '#00F2FE',
            'shadow-blur': 12,
            'shadow-color': 'data(color)',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 'mapData(weight, 0, 1, 1, 4)',
            'line-color': '#334155',
            'target-arrow-color': '#00F2FE',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            opacity: 0.7,
          },
        },
        {
          selector: ':selected',
          style: {
            'border-width': 4,
            'border-color': '#00F2FE',
            'line-color': '#00F2FE',
            opacity: 1,
          },
        },
      ],
      layout: { name: layoutName, animate: true },
    });

    cy.on('tap', 'node', (evt) => {
      const nodeData = evt.target.data() as GraphNode;
      setSelectedNode(nodeData);
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
    };
  }, [selectedBank, layoutName]);

  const handleSearch = () => {
    if (!cyRef.current || !searchQuery) return;
    const matched = cyRef.current.nodes(`[id *= "${searchQuery}"]`);
    if (matched.length > 0) {
      cyRef.current.animate({
        center: { eles: matched },
        zoom: 1.5,
      });
      matched.select();
      setSelectedNode(matched[0].data() as GraphNode);
    }
  };

  return (
    <div className="space-y-6">
      {/* Visualizer Control Bar */}
      <div className="glass-card p-4 rounded-xl flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Network className="h-5 w-5" />
          </div>
          <div>
            <h2 className="font-semibold text-sm text-slate-100">Financial Entity Resolution Topology</h2>
            <p className="text-xs text-slate-400">GraphSAGE Fraud Node Embeddings & Shared Entity Links</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Search Input */}
          <div className="relative">
            <input
              type="text"
              placeholder="Search Entity ID (e.g. CUST-5ac)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="bg-slate-900 border border-slate-700/80 rounded-lg text-xs pl-8 pr-3 py-1.5 text-slate-200 outline-none focus:border-cyan-400"
            />
            <Search className="h-3.5 w-3.5 text-slate-400 absolute left-2.5 top-2.5" />
          </div>

          {/* Layout Switcher */}
          <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-lg">
            {['cose', 'circle', 'concentric'].map((mode) => (
              <button
                key={mode}
                onClick={() => setLayoutName(mode)}
                className={`px-2.5 py-1 text-xs rounded uppercase font-mono transition-all ${
                  layoutName === mode
                    ? 'bg-cyan-500/20 text-cyan-300 font-semibold'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {mode}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Main Canvas & Inspector Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Cytoscape Canvas */}
        <div className="lg:col-span-2 glass-card rounded-xl p-2 relative h-[500px] overflow-hidden border border-slate-800">
          <div ref={containerRef} className="w-full h-full" />
          <div className="absolute bottom-4 left-4 bg-slate-900/90 backdrop-blur border border-slate-800 rounded-lg p-2 flex items-center gap-4 text-xs text-slate-300">
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-red-500"/> Critical (&ge;800)</span>
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-orange-500"/> High (&ge;600)</span>
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-yellow-500"/> Medium (&ge;300)</span>
            <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-emerald-500"/> Low</span>
          </div>
        </div>

        {/* Selected Entity Inspector Panel */}
        <div className="glass-card rounded-xl p-5 border border-slate-800 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
            <ShieldAlert className="h-5 w-5 text-cyan-400" />
            <h3 className="font-semibold text-sm text-slate-100">Entity Node Inspector</h3>
          </div>

          {selectedNode ? (
            <div className="space-y-4 text-xs">
              <div>
                <span className="text-slate-400">Entity Identifier:</span>
                <p className="font-mono text-cyan-300 font-medium text-sm mt-0.5">{selectedNode.id}</p>
                <p className="text-slate-300 mt-0.5">{selectedNode.label}</p>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2">
                <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                  <span className="text-slate-400">Entity Type</span>
                  <p className="font-bold text-slate-200 uppercase mt-1">{selectedNode.type}</p>
                </div>
                <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                  <span className="text-slate-400">Graph Degree</span>
                  <p className="font-bold text-cyan-400 mt-1">{selectedNode.degree} Edges</p>
                </div>
              </div>

              <div className="bg-slate-900/80 p-3 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-slate-400">GraphSAGE Risk Score</span>
                  <span className="font-bold text-sm" style={{ color: getNodeColor(selectedNode.riskScore) }}>
                    {selectedNode.riskScore.toFixed(1)} / 1000
                  </span>
                </div>
                <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                  <div
                    className="h-full transition-all duration-300"
                    style={{
                      width: `${(selectedNode.riskScore / 1000) * 100}%`,
                      backgroundColor: getNodeColor(selectedNode.riskScore),
                    }}
                  />
                </div>
              </div>

              <div className="bg-cyan-500/5 border border-cyan-500/20 rounded-lg p-3">
                <span className="text-cyan-400 font-medium">Zero PII Privacy Protection</span>
                <p className="text-slate-400 mt-1 text-[11px]">
                  Identity mapped via salted MinHash LSH signature. Local PII never leaves bank perimeter.
                </p>
              </div>
            </div>
          ) : (
            <div className="text-center py-12 text-slate-500 text-xs">
              <Layers className="h-8 w-8 mx-auto mb-2 opacity-50 text-slate-400" />
              Click any node in the topology canvas to inspect graph embedding metrics.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
