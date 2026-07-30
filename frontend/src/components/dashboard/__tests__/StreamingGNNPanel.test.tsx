import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import StreamingGNNPanel from '../StreamingGNNPanel';

describe('StreamingGNNPanel Component Test Suite', () => {
  it('renders streaming graph neural network risk score metrics', () => {
    render(<StreamingGNNPanel />);

    const headings = screen.getAllByText(/Streaming GNN|Anomaly|Risk|Graph|Node/i);
    expect(headings.length).toBeGreaterThan(0);
  });
});
