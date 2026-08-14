import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import BankCard from '../BankCard';
import type { BankInfo } from '../../../api/types';

describe('BankCard Component', () => {
  const mockBank: BankInfo = {
    id: 'bank_a',
    name: 'Meridian National Bank',
    tier: 'tier_1',
    description: 'Tier 1 Global National Bank',
    default_transactions: 125000,
    default_fraud_ratio: 0.024,
    fraud_pattern: 'High-frequency cross-border structuring',
    characteristics: ['Tier 1 Global', 'High Volume', 'SWIFT Direct'],
  };

  it('renders bank details, tier, transaction metrics, and fraud pattern', () => {
    render(<BankCard bank={mockBank} index={0} />);

    expect(screen.getByText('Meridian National Bank')).toBeInTheDocument();
    expect(screen.getByText('tier_1 bank')).toBeInTheDocument();
    expect(screen.getByText('125,000')).toBeInTheDocument();
    expect(screen.getByText('2.4%')).toBeInTheDocument();
    expect(screen.getByText(/High-frequency cross-border structuring/i)).toBeInTheDocument();
    expect(screen.getByText('Tier 1 Global')).toBeInTheDocument();
  });
});
