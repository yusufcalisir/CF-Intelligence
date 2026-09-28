import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FourEyesApprovalModal, cleanIdentity } from '../FourEyesApprovalModal';

describe('FourEyesApprovalModal Component', () => {
  it('normalizes supervisor and actor identities correctly', () => {
    expect(cleanIdentity('supervisor:alice')).toBe('alice');
    expect(cleanIdentity('SIG_SUPERVISOR_BOB')).toBe('bob');
    expect(cleanIdentity('analyst:Charlie')).toBe('charlie');
    expect(cleanIdentity('  investigator:David  ')).toBe('david');
    expect(cleanIdentity('')).toBe('');
    expect(cleanIdentity(null)).toBe('');
  });

  it('renders modal with case metadata when open', () => {
    const handleClose = vi.fn();
    const handleConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <FourEyesApprovalModal
        isOpen={true}
        onClose={handleClose}
        caseId="CASE-2026-001"
        caseTitle="Cross-Bank Smurfing Syndicate"
        assignedInvestigator="investigator_john"
        currentActor="analyst_john"
        onConfirm={handleConfirm}
      />
    );

    expect(screen.getByText(/Four-Eyes Dual Control Signoff/i)).toBeInTheDocument();
    expect(screen.getByText('CASE-2026-001')).toBeInTheDocument();
    expect(screen.getByText('Cross-Bank Smurfing Syndicate')).toBeInTheDocument();
    expect(screen.getByText('investigator_john')).toBeInTheDocument();
    expect(screen.getByText(/Confirmed Fraud/i)).toBeInTheDocument();
    expect(screen.getByText(/False Positive/i)).toBeInTheDocument();
  });

  it('prohibits self-approval when supervisor matches assigned investigator', async () => {
    const handleClose = vi.fn();
    const handleConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <FourEyesApprovalModal
        isOpen={true}
        onClose={handleClose}
        caseId="CASE-2026-002"
        caseTitle="High Risk Crypto Structuring"
        assignedInvestigator="investigator_alice"
        currentActor="analyst_alice"
        onConfirm={handleConfirm}
      />
    );

    const primaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_alice/i);
    const secondaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_bob/i);
    const submitBtn = screen.getByRole('button', { name: /Authorize & Resolve Case/i });

    // Submit button should be disabled initially
    expect(submitBtn).toBeDisabled();

    // Enter assigned investigator's name as primary supervisor
    fireEvent.change(primaryInput, { target: { value: 'investigator_alice' } });
    fireEvent.change(secondaryInput, { target: { value: 'supervisor_bob' } });

    // Self-approval warning should appear
    expect(
      screen.getByText(/Self-approval blocked: Cannot match assigned investigator/i)
    ).toBeInTheDocument();
    expect(submitBtn).toBeDisabled();
  });

  it('prohibits identical duplicate supervisor identities', async () => {
    const handleClose = vi.fn();
    const handleConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <FourEyesApprovalModal
        isOpen={true}
        onClose={handleClose}
        caseId="CASE-2026-003"
        caseTitle="Layering Anomaly"
        assignedInvestigator="investigator_charlie"
        currentActor="analyst_99"
        onConfirm={handleConfirm}
      />
    );

    const primaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_alice/i);
    const secondaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_bob/i);
    const submitBtn = screen.getByRole('button', { name: /Authorize & Resolve Case/i });

    // Enter identical supervisor signatures (with different casing/prefixes)
    fireEvent.change(primaryInput, { target: { value: 'supervisor:alice' } });
    fireEvent.change(secondaryInput, { target: { value: 'SIG_SUPERVISOR_ALICE' } });

    expect(
      screen.getByText(/Distinct signers required: Secondary supervisor must be different/i)
    ).toBeInTheDocument();
    expect(submitBtn).toBeDisabled();
  });

  it('submits successfully when distinct, non-investigator supervisors are provided', async () => {
    const handleClose = vi.fn();
    const handleConfirm = vi.fn().mockResolvedValue(undefined);

    render(
      <FourEyesApprovalModal
        isOpen={true}
        onClose={handleClose}
        caseId="CASE-2026-004"
        caseTitle="Verified Mule Network"
        assignedInvestigator="investigator_charlie"
        currentActor="analyst_99"
        onConfirm={handleConfirm}
      />
    );

    const primaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_alice/i);
    const secondaryInput = screen.getByPlaceholderText(/e\.g\. supervisor_bob/i);
    const notesInput = screen.getByPlaceholderText(/Document consensus findings/i);
    const submitBtn = screen.getByRole('button', { name: /Authorize & Resolve Case/i });

    fireEvent.change(primaryInput, { target: { value: 'supervisor_alice' } });
    fireEvent.change(secondaryInput, { target: { value: 'supervisor_bob' } });
    fireEvent.change(notesInput, { target: { value: 'Verified high velocity transactions.' } });

    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(handleConfirm).toHaveBeenCalledWith({
        resolution: 'CONFIRMED_FRAUD',
        primarySupervisor: 'supervisor_alice',
        secondarySupervisor: 'supervisor_bob',
        notes: 'Verified high velocity transactions.',
      });
      expect(handleClose).toHaveBeenCalled();
    });
  });
});
