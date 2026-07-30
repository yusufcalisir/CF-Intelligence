import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import LossChart from '../LossChart';
import type { TrainingRound } from '../../../api/types';

describe('LossChart Component Test Suite', () => {
  it('renders loss history line chart cleanly', () => {
    const mockRounds: TrainingRound[] = [
      {
        round_number: 1,
        total_rounds: 10,
        global_loss: 0.45,
        participating_banks: ['jpmorgan', 'hsbc'],
        dropped_banks: [],
        duration_ms: 1200,
        privacy_budget: 0.1,
      },
      {
        round_number: 2,
        total_rounds: 10,
        global_loss: 0.12,
        participating_banks: ['jpmorgan', 'hsbc', 'deutsche'],
        dropped_banks: [],
        duration_ms: 1100,
        privacy_budget: 0.2,
      },
    ];

    render(<LossChart rounds={mockRounds} totalRounds={10} />);

    expect(screen.getByText(/Training Loss Convergence/i)).toBeDefined();
  });
});
