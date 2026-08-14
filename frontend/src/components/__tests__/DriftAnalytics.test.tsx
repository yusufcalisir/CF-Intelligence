import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DriftAnalytics } from '../DriftAnalytics';

describe('DriftAnalytics', () => {
  it('renders model drift & calibration analytics header and PSI features', () => {
    render(<DriftAnalytics />);

    expect(screen.getByText(/Model Drift & Calibration Analytics/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Population Stability Index \(PSI\)/i).length).toBeGreaterThan(0);

    expect(screen.getByText(/transaction_amount/i)).toBeInTheDocument();
    expect(screen.getByText(/merchant_category/i)).toBeInTheDocument();
    expect(screen.getByText(/country_code/i)).toBeInTheDocument();
  });

  it('renders drift threshold status badges (STABLE, MODERATE, DRIFT)', () => {
    render(<DriftAnalytics />);

    expect(screen.getAllByText(/STABLE/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/MODERATE/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/DRIFT/i).length).toBeGreaterThan(0);
  });
});
