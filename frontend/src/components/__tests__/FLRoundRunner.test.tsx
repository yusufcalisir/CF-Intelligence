import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FLRoundRunner } from '../FLRoundRunner';
import * as api from '../../services/api';

describe('FLRoundRunner Component (User Interaction)', () => {
  it('renders simulation parameters and orchestrator header', () => {
    render(<FLRoundRunner />);

    expect(screen.getByText(/Live Federated Learning Orchestrator/i)).toBeInTheDocument();
    expect(screen.getByText(/Ray Parallel Simulation & Multi-Bank Parameter Aggregation/i)).toBeInTheDocument();
    expect(screen.getByText(/Aggregation Strategy/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start FL Training Simulation/i })).toBeInTheDocument();
  });

  it('allows user to change aggregation algorithm and trigger training simulation', async () => {
    const user = userEvent.setup();

    const mockFLRounds = [
      {
        round_number: 1,
        global_loss: 0.65,
        global_auc: 0.78,
        privacy_spent: 0.2,
        per_bank_loss: { bank_a: 0.62, bank_b: 0.68, bank_c: 0.65 },
        status: 'COMPLETED' as const,
      },
    ];

    vi.spyOn(api, 'runFLSimulation').mockResolvedValue(mockFLRounds);

    render(<FLRoundRunner />);

    const select = screen.getByRole('combobox');
    await user.selectOptions(select, 'KRUM');
    expect(select).toHaveValue('KRUM');

    const startBtn = screen.getByRole('button', { name: /Start FL Training Simulation/i });
    await user.click(startBtn);

    expect(api.runFLSimulation).toHaveBeenCalledWith(
      expect.objectContaining({
        algorithm: 'KRUM',
      })
    );
  });
});
