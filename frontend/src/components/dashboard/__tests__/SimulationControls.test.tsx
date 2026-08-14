import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SimulationControls from '../SimulationControls';
import * as queries from '../../../api/queries';

describe('SimulationControls Component (User Interaction)', () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  it('renders simulation parameters inputs and launch button', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SimulationControls onSimulationCreated={vi.fn()} />
      </QueryClientProvider>
    );

    expect(screen.getByText('Simulation Configuration')).toBeInTheDocument();
    expect(screen.getByText('Rounds')).toBeInTheDocument();
    expect(screen.getByText('Local Epochs')).toBeInTheDocument();
    expect(screen.getByText('Learning Rate')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start Federated Training/i })).toBeInTheDocument();
  });

  it('allows user to modify rounds input and click start simulation', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();

    const mockMutate = vi.fn().mockImplementation((_config, options) => {
      options?.onSuccess?.({ id: 'sim_new_123' });
    });

    vi.spyOn(queries, 'useCreateSimulation').mockReturnValue({
      mutate: mockMutate,
      isPending: false,
    } as any);

    render(
      <QueryClientProvider client={queryClient}>
        <SimulationControls onSimulationCreated={onCreated} />
      </QueryClientProvider>
    );

    const startBtn = screen.getByRole('button', { name: /Start Federated Training/i });
    await user.click(startBtn);

    expect(mockMutate).toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalledWith('sim_new_123');
  });
});
