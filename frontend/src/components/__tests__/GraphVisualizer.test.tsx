import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GraphVisualizer } from '../GraphVisualizer';

vi.mock('cytoscape', () => {
  return {
    default: vi.fn(() => ({
      on: vi.fn(),
      nodes: vi.fn(() => []),
      animate: vi.fn(),
      stop: vi.fn(),
      destroy: vi.fn(),
    })),
  };
});

describe('GraphVisualizer Component (User Interaction)', () => {
  it('renders graph topology header, layout switcher buttons, search input, and legend', () => {
    render(<GraphVisualizer selectedBank="all" />);

    expect(screen.getByText(/Financial Entity Resolution Topology/i)).toBeInTheDocument();
    expect(screen.getByText(/GraphSAGE Fraud Node Embeddings/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search Entity ID/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cose/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /circle/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /concentric/i })).toBeInTheDocument();
    expect(screen.getByText(/Critical/i)).toBeInTheDocument();
  });

  it('allows user to switch layout mode and type search query', async () => {
    const user = userEvent.setup();
    render(<GraphVisualizer selectedBank="all" />);

    const circleBtn = screen.getByRole('button', { name: /circle/i });
    await user.click(circleBtn);
    expect(circleBtn).toHaveClass('bg-cyan-500/20');

    const searchInput = screen.getByPlaceholderText(/Search Entity ID/i);
    await user.type(searchInput, 'CUST-5ac');
    expect(searchInput).toHaveValue('CUST-5ac');
  });
});
