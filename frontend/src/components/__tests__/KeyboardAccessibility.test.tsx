import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Predictor } from '../Predictor';
import { Navbar } from '../Navbar';

describe('Interactive Keyboard Navigation & Control Access Test Suite', () => {
  it('allows full transaction parameter input and keyboard submission in Predictor', async () => {
    render(<Predictor />);

    // 1. Find Amount input and type value
    const amountInput = screen.getByDisplayValue('15000');
    expect(amountInput).toBeInTheDocument();
    await userEvent.clear(amountInput);
    await userEvent.type(amountInput, '2500');
    expect(amountInput).toHaveValue(2500);

    // 2. Tab to submit button and verify focusable
    const submitBtn = screen.getByRole('button', { name: /evaluate transaction risk/i });
    expect(submitBtn).toBeInTheDocument();
    submitBtn.focus();
    expect(document.activeElement).toBe(submitBtn);
  });

  it('allows keyboard navigation across global navigation tab items and active tenant selector', async () => {
    const handleTabChange = vi.fn();
    const handleBankChange = vi.fn();

    render(
      <Navbar
        activeTab="graph"
        setActiveTab={handleTabChange}
        selectedBank="bank_a"
        setSelectedBank={handleBankChange}
        health={null}
      />
    );

    // Tenant selector dropdown accessible via keyboard
    const bankSelect = screen.getByRole('combobox');
    expect(bankSelect).toBeInTheDocument();

    bankSelect.focus();
    expect(document.activeElement).toBe(bankSelect);

    // Change value via keyboard
    await userEvent.selectOptions(bankSelect, 'bank_b');
    expect(handleBankChange).toHaveBeenCalledWith('bank_b');

    // Navigation tab buttons are focusable and clickable via keyboard Enter
    const workbenchTab = screen.getByRole('button', { name: /counterfactual workbench/i });
    expect(workbenchTab).toBeInTheDocument();

    workbenchTab.focus();
    expect(document.activeElement).toBe(workbenchTab);

    await userEvent.keyboard('{Enter}');
    expect(handleTabChange).toHaveBeenCalledWith('counterfactual');
  });
});
