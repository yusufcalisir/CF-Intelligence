import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PlatformLaunchModal from '../PlatformLaunchModal';

describe('PlatformLaunchModal', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <PlatformLaunchModal
        isOpen={false}
        onClose={vi.fn()}
        onComplete={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders stages and hardware enclaves when isOpen is true', () => {
    render(
      <PlatformLaunchModal
        isOpen={true}
        onClose={vi.fn()}
        onComplete={vi.fn()}
      />
    );

    expect(screen.getByText(/Consortium Handshake/i)).toBeInTheDocument();
    expect(screen.getByText(/mTLS 1.3 & Vault PKI Handshake/i)).toBeInTheDocument();
    expect(screen.getByText(/SGX TEE/i)).toBeInTheDocument();
    expect(screen.getByText(/FL Coordinator/i)).toBeInTheDocument();
  });

  it('allows skipping straight to target dashboard with instant access button', () => {
    const handleComplete = vi.fn();
    render(
      <PlatformLaunchModal
        isOpen={true}
        onClose={vi.fn()}
        onComplete={handleComplete}
      />
    );

    const skipBtn = screen.getByText(/Skip/i);
    expect(skipBtn).toBeInTheDocument();
    fireEvent.click(skipBtn);

    expect(handleComplete).toHaveBeenCalledTimes(1);
  });
});
