import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BenchmarkLaunchModal from '../BenchmarkLaunchModal';

describe('BenchmarkLaunchModal', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <BenchmarkLaunchModal
        isOpen={false}
        onClose={vi.fn()}
        onComplete={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders benchmark pipeline stages and metrics when isOpen is true', () => {
    render(
      <BenchmarkLaunchModal
        isOpen={true}
        onClose={vi.fn()}
        onComplete={vi.fn()}
      />
    );

    expect(screen.getByText(/Initializing Benchmark Sandbox/i)).toBeInTheDocument();
    expect(screen.getByText(/Empirical Proof & Real Data Baseline Engine/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Kaggle & PaySim Baseline Ingestion/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Non-IID Dirichlet α=0.5 Cross-Bank Partition/i)).toBeInTheDocument();
  });
});
