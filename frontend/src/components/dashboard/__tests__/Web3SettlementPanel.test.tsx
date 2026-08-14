import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Web3SettlementPanel } from '../Web3SettlementPanel';
import type { OnChainPayout } from '../../../api/types';

describe('Web3SettlementPanel Component', () => {
  const mockPayouts: OnChainPayout[] = [
    {
      bank_name: 'Meridian National',
      wallet_address: '0x1234567890abcdef1234567890abcdef12345678',
      shapley_score: 0.45,
      shapley_basis_points: 4500,
      share_percent: 45.0,
      payout_usd: 4500.0,
      payout_wei: '4500000000000000000',
      is_quarantined: false,
      status: 'DISTRIBUTED',
    },
    {
      bank_name: 'Nexus Digital',
      wallet_address: '0xabcdef1234567890abcdef1234567890abcdef12',
      shapley_score: 0.35,
      shapley_basis_points: 3500,
      share_percent: 35.0,
      payout_usd: 3500.0,
      payout_wei: '3500000000000000000',
      is_quarantined: false,
      status: 'DISTRIBUTED',
    },
  ];

  it('renders smart contract settlement, payouts table, and Shapley reward allocation', () => {
    render(
      <Web3SettlementPanel
        enableWeb3Settlement={true}
        settlementCurrency="wCBDC"
        smartContractAddress="0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
        settlementTxHash="0x9abc87654321fedcba0987654321fedcba0987654321fedcba0987654321fedc"
        settlementBlockNumber={19842000}
        settlementStatus="EXECUTED"
        onChainPayouts={mockPayouts}
      />
    );

    expect(screen.getByText(/Automated Smart Contract Settlement/i)).toBeInTheDocument();
    expect(screen.getByText(/Meridian National/i)).toBeInTheDocument();
    expect(screen.getByText(/Nexus Digital/i)).toBeInTheDocument();
    expect(screen.getByText(/0.4500/i)).toBeInTheDocument();
  });
});
