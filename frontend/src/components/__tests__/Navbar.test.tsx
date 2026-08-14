import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Navbar } from '../Navbar';

describe('Navbar Component (User Interaction)', () => {
  it('renders brand header, health indicator, and tab navigation buttons', () => {
    render(
      <Navbar
        activeTab="graph"
        setActiveTab={vi.fn()}
        selectedBank="all"
        setSelectedBank={vi.fn()}
        health={{ status: 'healthy', database: 'connected', version: '1.4.2' }}
      />
    );

    expect(screen.getByText(/Cross-Bank FL Fraud Intelligence/i)).toBeInTheDocument();
    expect(screen.getByText(/Backend Online/i)).toBeInTheDocument();
    expect(screen.getByText(/Graph Fraud Visualizer/i)).toBeInTheDocument();
    expect(screen.getByText(/Counterfactual Workbench/i)).toBeInTheDocument();
  });

  it('allows user to switch tabs and select bank tenant', async () => {
    const user = userEvent.setup();
    const setActiveTab = vi.fn();
    const setSelectedBank = vi.fn();

    render(
      <Navbar
        activeTab="graph"
        setActiveTab={setActiveTab}
        selectedBank="all"
        setSelectedBank={setSelectedBank}
        health={{ status: 'healthy', database: 'connected', version: '1.4.2' }}
      />
    );

    const cfTab = screen.getByRole('button', { name: /Counterfactual Workbench/i });
    await user.click(cfTab);
    expect(setActiveTab).toHaveBeenCalledWith('counterfactual');

    const bankSelect = screen.getByRole('combobox');
    await user.selectOptions(bankSelect, 'bank_a');
    expect(setSelectedBank).toHaveBeenCalledWith('bank_a');
  });
});
