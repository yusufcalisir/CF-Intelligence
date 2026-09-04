import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChaosAttackInjectorPanel from '../ChaosAttackInjectorPanel';
import * as queries from '../../../api/queries';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('ChaosAttackInjectorPanel Component', () => {
  it('renders initial nominal state with attack injection buttons', () => {
    render(<ChaosAttackInjectorPanel />, { wrapper: createWrapper() });

    expect(screen.getByText('Live Chaos & Attack Simulator')).toBeInTheDocument();
    expect(screen.getByText('CONSORTIUM NOMINAL')).toBeInTheDocument();
    expect(screen.getByText('Inject 500 tx/s Smurfing Burst')).toBeInTheDocument();
    expect(screen.getByText('Inject Byzantine Poisoning')).toBeInTheDocument();
  });

  it('triggers Byzantine poisoned gradient attack and invokes quarantine callback', async () => {
    const onAttackMock = vi.fn();
    const onQuarantineMock = vi.fn();

    vi.spyOn(queries, 'useInjectAttack').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        attack_id: 'ATK-BYZ-9941',
        attack_type: 'byzantine_poisoning',
        status: 'quarantined',
        defense_activated: 'Krum Robust Byzantine Aggregation',
        adversary_quarantined: 'bank_gamma',
        euclidean_distance: 48.24,
        distance_threshold: 14.10,
        packets_blocked: 500,
        mitigation_latency_ms: 3.8,
        auc_protected: 0.9412,
        auc_compromised_baseline: 0.5218,
        log_entry: 'Byzantine poisoned gradient from Bank Gamma rejected by KRUM (dist 48.2 > cutoff 14.1).',
      }),
      isPending: false,
    } as any);

    render(
      <ChaosAttackInjectorPanel
        onAttackTriggered={onAttackMock}
        onQuarantineChange={onQuarantineMock}
      />,
      { wrapper: createWrapper() }
    );

    const byzantineBtn = screen.getByRole('button', { name: /Inject Byzantine Poisoning/i });
    fireEvent.click(byzantineBtn);

    await waitFor(() => {
      expect(screen.getByText('CRITICAL THREAT INJECTED')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText(/Byzantine Aggregation/i)).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByText(/bank_gamma/i)).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(onQuarantineMock).toHaveBeenCalledWith('bank_gamma');
    });

    // Verify Neutralize button resets state
    const neutralizeBtn = screen.getByRole('button', { name: /Neutralize & Restore Quorum/i });
    fireEvent.click(neutralizeBtn);

    await waitFor(() => {
      expect(screen.getByText('CONSORTIUM NOMINAL')).toBeInTheDocument();
      expect(onQuarantineMock).toHaveBeenCalledWith(null);
    });
  });


  it('triggers 500 tx/s smurfing burst and displays intercepted packets metric', async () => {
    vi.spyOn(queries, 'useInjectAttack').mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        attack_id: 'ATK-SMURF-1122',
        attack_type: 'smurfing_layering',
        status: 'intercepted',
        defense_activated: 'GraphSAGE Temporal GNN & LSH Private Set Intersection',
        adversary_quarantined: null,
        euclidean_distance: 0.0,
        distance_threshold: 0.0,
        packets_blocked: 1500,
        mitigation_latency_ms: 4.2,
        auc_protected: 0.9385,
        auc_compromised_baseline: 0.6120,
        log_entry: 'Smurfing burst of 500 tx/s intercepted. 1500 transfers quarantined in LSH-PSI memory pool.',
      }),
      isPending: false,
    } as any);

    render(<ChaosAttackInjectorPanel />, { wrapper: createWrapper() });

    const smurfingBtn = screen.getByRole('button', { name: /Inject 500 tx\/s Smurfing Burst/i });
    fireEvent.click(smurfingBtn);

    await waitFor(() => {
      expect(screen.getByText(/GraphSAGE Temporal GNN/i)).toBeInTheDocument();
      expect(screen.getByText(/Intercepted: 1500 txs/i)).toBeInTheDocument();
    });
  });
});
