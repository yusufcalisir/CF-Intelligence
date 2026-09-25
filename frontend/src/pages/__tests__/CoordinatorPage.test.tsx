import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import CoordinatorPage from '../CoordinatorPage';
import { apiClient } from '../../api/client';
import type { ClientCapabilityItem } from '../../api/types';

const MOCK_CONSORTIUM_CLIENTS: ClientCapabilityItem[] = [
  {
    bank_id: 'bank_alpha',
    bank_name: 'Garanti BBVA',
    status: 'ONLINE',
    pytorch_version: '2.4.0+cu124',
    python_version: '3.12.3',
    ram_gb: 128.0,
    hardware_type: 'cuda',
    device_count: 4,
    last_heartbeat_ago_seconds: 1.2,
  },
  {
    bank_id: 'bank_beta',
    bank_name: 'İş Bankası',
    status: 'ONLINE',
    pytorch_version: '2.4.0+cu124',
    python_version: '3.12.3',
    ram_gb: 64.0,
    hardware_type: 'cuda',
    device_count: 2,
    last_heartbeat_ago_seconds: 2.1,
  },
  {
    bank_id: 'bank_gamma',
    bank_name: 'Akbank',
    status: 'ONLINE',
    pytorch_version: '2.4.0+cu121',
    python_version: '3.12.2',
    ram_gb: 64.0,
    hardware_type: 'cuda',
    device_count: 2,
    last_heartbeat_ago_seconds: 3.5,
  },
  {
    bank_id: 'bank_a',
    bank_name: 'Meridian National',
    status: 'ONLINE',
    pytorch_version: '2.4.0+cu121',
    python_version: '3.12.1',
    ram_gb: 48.0,
    hardware_type: 'cuda',
    device_count: 1,
    last_heartbeat_ago_seconds: 4.8,
  },
  {
    bank_id: 'bank_b',
    bank_name: 'Nexus Digital',
    status: 'ONLINE',
    pytorch_version: '2.4.0+cpu',
    python_version: '3.12.0',
    ram_gb: 32.0,
    hardware_type: 'cpu',
    device_count: 0,
    last_heartbeat_ago_seconds: 5.6,
  },
];

const createWrapper = (initialClients: ClientCapabilityItem[] | null = MOCK_CONSORTIUM_CLIENTS) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  if (initialClients !== null) {
    queryClient.setQueryData(['coordinator', 'clients'], initialClients);
  }
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('CoordinatorPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'get').mockImplementation(async (url: string) => {
      if (url.includes('/negotiate')) {
        return {
          data: {
            bank_id: 'bank_alpha',
            batch_size: 64,
            local_epochs: 3,
            gradient_accumulation_steps: 1,
            use_cuda: true,
            status: 'COMPATIBLE',
          },
        };
      }
      if (url.includes('/clients')) {
        return {
          data: MOCK_CONSORTIUM_CLIENTS,
        };
      }
      return { data: {} };
    });
  });
  it('renders coordinator dashboard title and authentic consortium bank nodes telemetry', () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Federated Coordinator Suite/i)).toBeInTheDocument();
    expect(screen.getByText(/Dynamic client registry, live heartbeat monitoring/i)).toBeInTheDocument();
    expect(screen.getByText(/5\/5 Live Quorum Active/i)).toBeInTheDocument();

    // Verify all 5 authentic consortium banking institutions are rendered
    expect(screen.getAllByText(/Garanti BBVA/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/İş Bankası/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Akbank/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Meridian National/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Nexus Digital/i).length).toBeGreaterThan(0);

    // Verify node IDs
    expect(screen.getAllByText(/bank_alpha/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_beta/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_gamma/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_a/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/bank_b/i).length).toBeGreaterThan(0);

    // Verify PyTorch 2.4.0 telemetry
    expect(screen.getAllByText(/2\.4\.0/i).length).toBeGreaterThan(0);
  });

  it('renders hyperparameter negotiation controls and hardware specs for selected bank node', () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    expect(screen.getByText(/Base Batch Size/i)).toBeInTheDocument();
    expect(screen.getByText(/Base Local Epochs/i)).toBeInTheDocument();
    expect(screen.getByText(/Refresh Registry/i)).toBeInTheDocument();
    expect(screen.getByText(/Hardware-Aware Parameter Negotiator/i)).toBeInTheDocument();
    expect(screen.getByText(/Scaled Batch Size/i)).toBeInTheDocument();
    expect(screen.getByText(/Scaled Epochs/i)).toBeInTheDocument();
    expect(screen.getByText(/Gradient Accumulation/i)).toBeInTheDocument();
  });

  it('switches between hardware acceleration filters (All, CUDA, CPU) and handles empty awaiting states', async () => {
    render(<CoordinatorPage />, { wrapper: createWrapper() });

    // Mode filter buttons should exist
    const allBtn = screen.getByRole('button', { name: /All Nodes \(\d+\)/i });
    const cudaBtn = screen.getByRole('button', { name: /CUDA \(\d+\)/i });
    const cpuBtn = screen.getByRole('button', { name: /CPU \(\d+\)/i });

    expect(allBtn).toBeInTheDocument();
    expect(cudaBtn).toBeInTheDocument();
    expect(cpuBtn).toBeInTheDocument();

    // Click CUDA filter -> shows only GPU accelerated nodes
    fireEvent.click(cudaBtn);
    expect(screen.getAllByText(/Garanti BBVA/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CUDA \(4\)/i).length).toBeGreaterThan(0);

    // Click CPU filter -> shows CPU Host enclave node (Nexus Digital)
    fireEvent.click(cpuBtn);
    expect(screen.getAllByText(/Nexus Digital/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/CPU \(1\)/i).length).toBeGreaterThan(0);

    // Click All Nodes filter -> returns all 5 nodes
    fireEvent.click(allBtn);
    expect(screen.getAllByText(/All Nodes \(5\)/i).length).toBeGreaterThan(0);
  });

  it('displays awaiting edge nodes card when zero clients are registered', () => {
    render(<CoordinatorPage />, { wrapper: createWrapper([]) });

    expect(screen.getByText(/Awaiting Live Edge Nodes/i)).toBeInTheDocument();
    expect(screen.getByText(/0\.0\.0\.0:50051/i)).toBeInTheDocument();
    expect(screen.getByText(/Start Bank Edge Node CLI/i)).toBeInTheDocument();
    expect(screen.getByText(/Awaiting Bank Edge Nodes/i)).toBeInTheDocument();
  });
});
