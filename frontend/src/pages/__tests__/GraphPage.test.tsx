import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import GraphPage from '../GraphPage';
import * as queries from '../../api/queries';

describe('GraphPage (Entity Graph Visualization) Test Suite', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const mockEntities = [
    {
      id: 'ent_01',
      entity_type: 'account',
      privacy_id: 'hmac_sha256_acc_01',
      bank_id: 'bank_a',
      display_label: 'Acc #88219 (Meridian)',
      risk_level: 'critical',
      alert_count: 5,
      first_seen: '2026-08-14T00:00:00Z',
      last_seen: '2026-08-14T10:00:00Z',
      attributes: {},
    },
    {
      id: 'ent_02',
      entity_type: 'device',
      privacy_id: 'hmac_sha256_dev_02',
      bank_id: 'bank_b',
      display_label: 'Device FP-9912',
      risk_level: 'high',
      alert_count: 3,
      first_seen: '2026-08-14T01:00:00Z',
      last_seen: '2026-08-14T11:00:00Z',
      attributes: {},
    },
  ];

  const mockGraphStats = {
    total_nodes: 42,
    total_edges: 89,
    cluster_count: 6,
    nodes_by_type: { account: 20, device: 12, ip: 10 },
    database_backend: 'NetworkX GNN Engine',
  };

  const mockGraphData = {
    nodes: [
      { id: 'ent_01', position: { x: 0, y: 0 }, data: { label: 'Acc #88219' } },
      { id: 'ent_02', position: { x: 100, y: 100 }, data: { label: 'Device FP-9912' } },
    ],
    edges: [{ id: 'e1-2', source: 'ent_01', target: 'ent_02' }],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useEntities').mockReturnValue({
      data: mockEntities,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useGraphStats').mockReturnValue({
      data: mockGraphStats,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(queries, 'useGraph').mockReturnValue({
      data: mockGraphData,
      isLoading: false,
      error: null,
    } as any);
  });

  it('renders graph investigation header, KPI stats, and entity search list', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <GraphPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Entity Relationship Graph/i)).toBeInTheDocument();
    expect(screen.getByText(/Total Nodes/i)).toBeInTheDocument();
    expect(screen.getByText(/Fraud Clusters/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Acc #88219/i).length).toBeGreaterThan(0);
  });

  it('allows filtering entities via search input', async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <GraphPage />
        </BrowserRouter>
      </QueryClientProvider>
    );

    const searchInput = screen.getByPlaceholderText(/Search by display label, entity type, or HMAC privacy ID\.\.\./i);
    await user.type(searchInput, 'Device');

    expect(searchInput).toHaveValue('Device');
    expect(screen.getAllByText(/Device FP-9912/i).length).toBeGreaterThan(0);
  });

  it('reads entity_id and depth from URL parameters and displays deep-linked focus banner', () => {
    const { MemoryRouter } = require('react-router-dom');

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/graph?entity_id=ent_01&depth=3']}>
          <GraphPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Deep-Linked Ego Focus:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/ent_01/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/3-Hop Ego Network/i)).toBeInTheDocument();
    expect(queries.useGraph).toHaveBeenCalledWith('ent_01', 3);
  });

  it('allows changing traversal depth hops and updates graph query', async () => {
    const user = userEvent.setup();
    const { MemoryRouter } = require('react-router-dom');

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/graph?entity_id=ent_01&depth=2']}>
          <GraphPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/2-Hop Ego Network/i)).toBeInTheDocument();

    const hop4Button = screen.getByRole('button', { name: /4 Hops/i });
    await user.click(hop4Button);

    expect(screen.getByText(/4-Hop Ego Network/i)).toBeInTheDocument();
    expect(queries.useGraph).toHaveBeenCalledWith('ent_01', 4);
  });

  it('allows resetting focus to clear entity selection', async () => {
    const user = userEvent.setup();
    const { MemoryRouter } = require('react-router-dom');

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/graph?entity_id=ent_01&depth=2']}>
          <GraphPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText(/Deep-Linked Ego Focus:/i)).toBeInTheDocument();

    const resetBtn = screen.getByRole('button', { name: /Reset Focus/i });
    await user.click(resetBtn);

    expect(screen.queryByText(/Deep-Linked Ego Focus:/i)).not.toBeInTheDocument();
  });

  it('selects an entity from directory pills and activates ego network', async () => {
    const user = userEvent.setup();
    const { MemoryRouter } = require('react-router-dom');

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/graph']}>
          <GraphPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    const devicePill = screen.getAllByRole('button', { name: /Device FP-9912/i })[0];
    expect(devicePill).toBeDefined();
    await user.click(devicePill!);

    expect(screen.getByText(/Deep-Linked Ego Focus:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/ent_02/i).length).toBeGreaterThan(0);
  });
});
