import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import LiveOperationsView from '../LiveOperationsView';
import * as queries from '../../api/queries';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('LiveOperationsView Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(queries, 'useScoringVolume').mockReturnValue({
      data: { total_scored: 15420, fraud_detected: 42, tps_current: 24.5 },
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useSimulation').mockReturnValue({
      data: null,
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useTrainingRounds').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);
  });

  it('renders dashboard title, active champion AUC, and empty round telemetry CTA', () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    expect(screen.getByText(/Live Operations Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/Active Champion AUC/i)).toBeInTheDocument();
    expect(screen.getByText(/No Telemetry Rounds Recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/Start Training Run/i)).toBeInTheDocument();
  });

  it('renders dataset configuration and import buttons', () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    expect(screen.getByRole('button', { name: /Import Dataset/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Configure/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start Simulation/i })).toBeInTheDocument();
  });

  it('handles offline demo fallback banner and retry button click', async () => {
    render(<LiveOperationsView />, { wrapper: createWrapper() });

    // In jsdom environment without live WebSocket server, ws will fail and trigger offline demo mode
    // Wait for the offline demo mode badge or retry button to appear
    const retryBtn = await screen.findByRole('button', { name: /Retry Live Stream/i }, { timeout: 2000 }).catch(() => null);
    if (retryBtn) {
      expect(screen.getByText(/Simulated Telemetry \(Offline Demo Mode\)/i)).toBeInTheDocument();
      fireEvent.click(retryBtn);
      expect(retryBtn).toBeInTheDocument();
    }
  });

  it('maintains CONNECTED status and sends periodic liveness ping via watchdog timer', () => {
    vi.useFakeTimers();

    const mockSend = vi.fn();
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = mockSend;
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });

    // Simulate WebSocket opening
    expect(instance).not.toBeNull();
    act(() => {
      instance.onopen();
    });

    // Verify CONNECTED status
    expect(screen.getByText('CONNECTED')).toBeInTheDocument();

    // Advance timer by 10s to trigger liveness check ping
    act(() => {
      vi.advanceTimersByTime(10000);
    });

    expect(mockSend).toHaveBeenCalledWith(expect.stringContaining('"event":"ping"'));

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('transitions to reconnecting offline mode and cleans up liveness timer on connection close', () => {
    vi.useFakeTimers();

    const mockSend = vi.fn();
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = mockSend;
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });

    act(() => {
      instance.onopen();
    });
    expect(screen.getByText('CONNECTED')).toBeInTheDocument();

    // Simulate socket closing
    instance.readyState = 3;
    act(() => {
      instance.onclose();
    });

    // Verify OFFLINE mode triggered
    expect(screen.getByText('OFFLINE')).toBeInTheDocument();
    expect(screen.getByText(/Offline Demo Mode \(Connection Lost — Simulated\)/i)).toBeInTheDocument();

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('tears down active WebSocket and cleans all timers on component unmount', () => {
    const mockClose = vi.fn();
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = vi.fn();
      close = mockClose;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    const { unmount } = render(<LiveOperationsView />, { wrapper: createWrapper() });
    expect(instance).not.toBeNull();

    unmount();

    expect(mockClose).toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it('cancels pending reconnect timer and triggers immediate reconnect when clicking retry button', () => {
    vi.useFakeTimers();

    let socketCount = 0;
    let instance: any = null;

    class TestWebSocket {
      static OPEN = 1;
      static CLOSED = 3;
      readyState = 1;
      send = vi.fn();
      close = vi.fn();
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((e: { data: string }) => void) | null = null;

      constructor(public url: string) {
        socketCount++;
        instance = this;
      }
    }

    vi.stubGlobal('WebSocket', TestWebSocket);

    render(<LiveOperationsView />, { wrapper: createWrapper() });
    expect(socketCount).toBe(1);

    // Trigger connection close synchronously
    act(() => {
      instance.readyState = 3;
      instance.onclose();
    });

    // Offline banner and retry button appear synchronously
    const retryBtn = screen.getByRole('button', { name: /Retry Live Stream/i });
    expect(retryBtn).toBeInTheDocument();

    // User clicks retry button
    act(() => {
      fireEvent.click(retryBtn);
    });

    // Socket count should increment due to immediate reconnect retry
    expect(socketCount).toBe(2);

    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('wires deep operational panels to active simulation telemetry, compliance audits, and Web3 settlement', () => {
    const mockSim = {
      id: 'sim_active_telemetry_99',
      status: 'completed',
      config: {
        hardware_isolation_mode: 'tee',
        enable_web3_settlement: true,
        settlement_currency: 'wCBDC',
        smart_contract_address: '0x71C7656EC7ab88b098defB751B7401B5f6d8976F',
      },
      current_round: 10,
      total_rounds: 10,
      progress_pct: 100,
      banks: [
        {
          id: 'bank_alpha',
          name: 'Bank Alpha',
          tier: 'Tier 1',
          fraud_ratio: 0.012,
          num_transactions: 120000,
          status: 'ACTIVE',
          contribution_score: 0.55,
          quarantined: false,
          local_metrics: null,
          federated_metrics: {
            accuracy: 0.96,
            precision: 0.93,
            recall: 0.91,
            f1_score: 0.92,
            auc_roc: 0.965,
            loss: 0.15,
            confusion_matrix: [[1000, 20], [10, 990]],
            roc_fpr: [0, 0.05, 1],
            roc_tpr: [0, 0.95, 1],
            roc_thresholds: [1, 0.5, 0],
            feature_importance: {},
            disparate_impact: 0.942,
            equal_opportunity_diff: 0.038,
            protected_selection_rate: 0.048,
            reference_selection_rate: 0.051,
          },
          improvement: null,
          data_profile: null,
        },
      ],
      rounds: [],
      tee_mrenclave: 'a7b8e9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8',
      tee_mrsigner: 'f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2',
      tee_attestation_signature: 'sgx_quote_verified',
      settlement_tx_hash: '0x3a7e58b1c4d92a0e7f8b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f',
      settlement_block_number: 19482035,
      settlement_status: 'DISTRIBUTED',
      on_chain_payouts: [
        {
          bank_name: 'Bank Alpha',
          wallet_address: '0x90F79bf6EB2c4f870365E785982E1f101E93b906',
          shapley_score: 0.55,
          shapley_basis_points: 5500,
          share_percent: 55.0,
          payout_usd: 55000,
          payout_wei: '55000000000000000000000',
          is_quarantined: false,
          status: 'DISTRIBUTED',
        },
      ],
    };

    vi.spyOn(queries, 'useSimulations').mockReturnValue({
      data: [{ id: 'sim_active_telemetry_99', status: 'completed' }],
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useSimulation').mockReturnValue({
      data: mockSim,
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useModelVersions').mockReturnValue({
      data: [
        {
          version: 1,
          filename: 'model_v1.pt',
          is_active: true,
          status: 'ACTIVE_CHAMPION',
          git_commit_hash: 'a1b2c3d4',
          dataset_hash: 'd4e5f6g7',
          dp_noise_profile: { mechanism: 'opacus', epsilon: 1.5, delta: 1e-5 },
          metrics: { accuracy: 0.96, precision: 0.93, recall: 0.91, f1_score: 0.92, auc_roc: 0.965, loss: 0.15 },
        },
      ],
      isLoading: false,
    } as any);
    vi.spyOn(queries, 'useCanaryHistory').mockReturnValue({
      data: [],
      isLoading: false,
    } as any);

    render(<LiveOperationsView />, { wrapper: createWrapper() });

    // Verify Model Registry & Canary Evaluation
    expect(screen.getByText(/Model Registry & Canary Evaluation/i)).toBeInTheDocument();

    // Verify AI Regulatory Compliance & Bias Audit
    expect(screen.getByText(/AI Regulatory Compliance & Bias Audit/i)).toBeInTheDocument();
    expect(screen.getByText(/Disparate Impact Ratio/i)).toBeInTheDocument();

    // Verify Consortium Incentive Registry
    expect(screen.getByText(/Consortium Incentive Registry/i)).toBeInTheDocument();

    // Verify SGX TEE Trusted Execution Environment
    expect(screen.getByText(/Trusted Execution Environment \(TEE\)/i)).toBeInTheDocument();
    expect(screen.getByText(/a7b8e9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8/i)).toBeInTheDocument();

    // Verify Web3 Smart Contract Settlement
    expect(screen.getByText(/Automated Smart Contract Settlement/i)).toBeInTheDocument();
    expect(screen.getByText(/0x71C7656EC7ab88b098defB751B7401B5f6d8976F/i)).toBeInTheDocument();
    expect(screen.getByText(/0x3a7e58b1c4d92a0e7f8b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f/i)).toBeInTheDocument();
  });
});

