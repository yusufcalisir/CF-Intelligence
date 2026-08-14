import { useRef } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useModalA11y } from '../../hooks/useModalA11y';
import PlatformLaunchModal from '../PlatformLaunchModal';
import BenchmarkLaunchModal from '../BenchmarkLaunchModal';
import DatasetTrainingConfigPanel from '../DatasetTrainingConfigPanel';

// Test modal harness component for testing useModalA11y
function TestModalComponent({
  isOpen,
  onClose,
}: {
  isOpen: boolean;
  onClose: () => void;
}) {
  const firstInputRef = useRef<HTMLInputElement | null>(null);
  const { containerRef } = useModalA11y<HTMLDivElement>({
    isOpen,
    onClose,
    initialFocusRef: firstInputRef,
    closeOnEscape: true,
    trapFocus: true,
    restoreFocus: true,
  });

  if (!isOpen) return null;

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="test-dialog-title"
      tabIndex={-1}
      data-testid="modal-container"
    >
      <h2 id="test-dialog-title">Accessible Test Dialog</h2>
      <input ref={firstInputRef} data-testid="input-1" placeholder="First Field" />
      <button data-testid="btn-action">Action Button</button>
      <button data-testid="btn-cancel" onClick={onClose}>
        Cancel
      </button>
    </div>
  );
}

describe('Modal Accessibility & Keyboard Focus Trap Test Suite', () => {
  it('focuses the initial focus ref element immediately on modal open', async () => {
    const handleClose = vi.fn();
    render(<TestModalComponent isOpen={true} onClose={handleClose} />);

    const input1 = screen.getByTestId('input-1');
    // Allow requestAnimationFrame execution
    await vi.waitFor(() => {
      expect(document.activeElement).toBe(input1);
    });
  });

  it('traps focus forward when pressing Tab key from last element back to first element', async () => {
    const handleClose = vi.fn();
    render(<TestModalComponent isOpen={true} onClose={handleClose} />);

    const input1 = screen.getByTestId('input-1');
    const cancelBtn = screen.getByTestId('btn-cancel');

    // Move focus to last element
    cancelBtn.focus();
    expect(document.activeElement).toBe(cancelBtn);

    // Press Tab on the last element
    fireEvent.keyDown(cancelBtn, { key: 'Tab', shiftKey: false });

    // Focus must wrap around to the first element
    expect(document.activeElement).toBe(input1);
  });

  it('traps focus backward when pressing Shift+Tab from first element to last element', async () => {
    const handleClose = vi.fn();
    render(<TestModalComponent isOpen={true} onClose={handleClose} />);

    const input1 = screen.getByTestId('input-1');
    const cancelBtn = screen.getByTestId('btn-cancel');

    input1.focus();
    expect(document.activeElement).toBe(input1);

    // Press Shift+Tab on the first element
    fireEvent.keyDown(input1, { key: 'Tab', shiftKey: true });

    // Focus must wrap around to the last element
    expect(document.activeElement).toBe(cancelBtn);
  });

  it('triggers onClose callback when user presses Escape key', async () => {
    const handleClose = vi.fn();
    render(<TestModalComponent isOpen={true} onClose={handleClose} />);

    expect(handleClose).not.toHaveBeenCalled();

    await userEvent.keyboard('{Escape}');

    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('renders PlatformLaunchModal with accessible ARIA attributes and focus trap container', () => {
    const handleComplete = vi.fn();
    render(<PlatformLaunchModal isOpen={true} onComplete={handleComplete} />);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'launch-modal-title');
    expect(dialog).toHaveAttribute('aria-describedby', 'launch-modal-desc');

    const skipBtn = screen.getByRole('button', { name: /skip/i });
    expect(skipBtn).toBeInTheDocument();
  });

  it('renders BenchmarkLaunchModal with accessible ARIA attributes and skip button', () => {
    const handleComplete = vi.fn();
    render(<BenchmarkLaunchModal isOpen={true} onComplete={handleComplete} />);

    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'benchmark-modal-title');
    expect(dialog).toHaveAttribute('aria-describedby', 'benchmark-modal-desc');

    const skipBtn = screen.getByRole('button', { name: /skip/i });
    expect(skipBtn).toBeInTheDocument();
  });

  it('renders DatasetTrainingConfigPanel with accessible region attributes and close button', async () => {
    const handleClose = vi.fn();
    const handleLaunch = vi.fn();
    render(
      <DatasetTrainingConfigPanel
        isOpen={true}
        onClose={handleClose}
        onLaunch={handleLaunch}
      />
    );

    const region = screen.getByRole('region');
    expect(region).toBeInTheDocument();
    expect(region).toHaveAttribute('aria-labelledby', 'training-config-title');
    expect(region).toHaveAttribute('aria-describedby', 'training-config-desc');

    const closeBtn = screen.getByRole('button', { name: /close config panel/i });
    expect(closeBtn).toBeInTheDocument();

    await userEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
