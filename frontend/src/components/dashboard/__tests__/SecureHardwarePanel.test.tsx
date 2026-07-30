import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import SecureHardwarePanel from '../SecureHardwarePanel';

describe('SecureHardwarePanel Component Test Suite', () => {
  it('renders Intel SGX attestation and TEE hardware metrics', () => {
    render(<SecureHardwarePanel />);

    const hardwareLabels = screen.getAllByText(/Intel SGX|Hardware|TEE|Enclave|Attestation/i);
    expect(hardwareLabels.length).toBeGreaterThan(0);
  });
});
