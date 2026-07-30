import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ConfusionMatrix from '../ConfusionMatrix';

describe('ConfusionMatrix Visualization Test Suite', () => {
  it('renders 2x2 confusion matrix heatmaps and cell values', () => {
    const mockData = {
      tp: 1420,
      fp: 32,
      fn: 18,
      tn: 9850,
    };

    render(<ConfusionMatrix matrix={mockData} />);

    expect(screen.getByText(/True Positive|1420|1,420/i)).toBeDefined();
    expect(screen.getByText(/False Positive|32/i)).toBeDefined();
  });
});
