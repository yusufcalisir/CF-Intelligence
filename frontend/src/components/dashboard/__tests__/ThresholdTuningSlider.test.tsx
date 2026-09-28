import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThresholdTuningSlider } from '../ThresholdTuningSlider';

describe('ThresholdTuningSlider Component Test Suite', () => {
  it('renders slider header, default threshold, and confusion matrix', () => {
    render(<ThresholdTuningSlider initialThreshold={600} />);

    expect(
      screen.getByText(/Cost-Sensitive Risk Threshold & Financial Utility Optimizer/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/Decision Threshold Tuning/i)).toBeInTheDocument();
    expect(screen.getByText(/τ = 600 \(0.60\)/i)).toBeInTheDocument();

    // Verify initial Confusion Matrix values at threshold 600
    // TP = 81, FP = 149, FN = 19, TN = 4751
    expect(screen.getByText('81')).toBeInTheDocument();
    expect(screen.getByText('149')).toBeInTheDocument();
    expect(screen.getByText('19')).toBeInTheDocument();
    expect(screen.getByText('4751')).toBeInTheDocument();

    // Metrics
    expect(screen.getByText('81.0%')).toBeInTheDocument(); // Recall
    expect(screen.getByText('35.2%')).toBeInTheDocument(); // Precision
  });

  it('handles slider value changes and invokes onThresholdChange callback', () => {
    const onThresholdChange = vi.fn();
    render(<ThresholdTuningSlider initialThreshold={600} onThresholdChange={onThresholdChange} />);

    const slider = screen.getByLabelText('Decision Threshold Slider');
    fireEvent.change(slider, { target: { value: '750' } });

    expect(onThresholdChange).toHaveBeenCalledWith(750);
    expect(screen.getByText(/τ = 750 \(0.75\)/i)).toBeInTheDocument();

    // At threshold 750: TP = 46, FP = 22, FN = 54
    expect(screen.getByText('46')).toBeInTheDocument();
    expect(screen.getByText('22')).toBeInTheDocument();
    expect(screen.getByText('54')).toBeInTheDocument();
  });

  it('selects threshold via preset buttons', () => {
    const onThresholdChange = vi.fn();
    render(<ThresholdTuningSlider initialThreshold={600} onThresholdChange={onThresholdChange} />);

    // Click 800 preset
    const preset800 = screen.getByRole('button', { name: '800' });
    fireEvent.click(preset800);

    expect(onThresholdChange).toHaveBeenCalledWith(800);
    expect(screen.getByText(/τ = 800 \(0.80\)/i)).toBeInTheDocument();
  });

  it('updates financial calculations when cost parameters change', () => {
    render(<ThresholdTuningSlider initialThreshold={600} initialCostFn={850} initialCostFp={45} />);

    const costFnInput = screen.getByLabelText('Cost of False Negative');
    fireEvent.change(costFnInput, { target: { value: '1200' } });

    // Baseline should now be 100 * 1200 = $120,000
    expect(screen.getByText('$120,000')).toBeInTheDocument();
  });

  it('applies optimal threshold and resets to defaults', () => {
    const onThresholdChange = vi.fn();
    render(<ThresholdTuningSlider initialThreshold={800} onThresholdChange={onThresholdChange} />);

    // At default costs (850, 45, 15), optimal threshold is 600
    const applyOptBtn = screen.getByRole('button', { name: /Apply Optimal/i });
    fireEvent.click(applyOptBtn);

    expect(onThresholdChange).toHaveBeenCalledWith(600);
    expect(screen.getByText(/τ = 600 \(0.60\)/i)).toBeInTheDocument();

    // Now reset
    const resetBtn = screen.getByLabelText('Reset parameters');
    fireEvent.click(resetBtn);

    expect(screen.getByText(/τ = 600 \(0.60\)/i)).toBeInTheDocument();
  });
});
