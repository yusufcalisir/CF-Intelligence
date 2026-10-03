import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import DatasetTrainingConfigPanel from '../DatasetTrainingConfigPanel';
import { DATASET_PROFILES } from '../../utils/datasetProfiles';

describe('DatasetTrainingConfigPanel', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <DatasetTrainingConfigPanel
        isOpen={false}
        onClose={vi.fn()}
        onLaunch={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders header, dataset cards, and mode toggles when isOpen is true', () => {
    render(
      <DatasetTrainingConfigPanel
        isOpen={true}
        onClose={vi.fn()}
        onLaunch={vi.fn()}
      />
    );

    expect(screen.getByText(/Configure Training Session/i)).toBeInTheDocument();
    expect(screen.getAllByText(/PaySim Mobile Money/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/IEEE-CIS Fraud Detection/i)).toBeInTheDocument();
    expect(screen.getByText(/Elliptic Bitcoin AML Graph/i)).toBeInTheDocument();
    expect(screen.getByText(/European Credit Card Fraud/i)).toBeInTheDocument();

    expect(screen.getByText(/Offline Sandbox Demo/i)).toBeInTheDocument();
    expect(screen.getByText(/Live Backend Engine/i)).toBeInTheDocument();
  });

  it('calls onLaunch with selected dataset and mode when Launch Training is clicked', () => {
    const handleLaunch = vi.fn();
    render(
      <DatasetTrainingConfigPanel
        isOpen={true}
        onClose={vi.fn()}
        onLaunch={handleLaunch}
      />
    );

    // Select Elliptic
    fireEvent.click(screen.getByText(/Elliptic Bitcoin AML Graph/i));

    // Toggle Live Backend Engine mode
    fireEvent.click(screen.getByText(/Live Backend Engine/i));

    // Click Launch
    fireEvent.click(screen.getByText(/Launch Real Training/i));

    expect(handleLaunch).toHaveBeenCalledTimes(1);
    expect(handleLaunch).toHaveBeenCalledWith(DATASET_PROFILES.elliptic, 'real');
  });

  it('calls onClose when close button is clicked', () => {
    const handleClose = vi.fn();
    render(
      <DatasetTrainingConfigPanel
        isOpen={true}
        onClose={handleClose}
        onLaunch={vi.fn()}
      />
    );

    const closeBtn = screen.getByLabelText(/Close config panel/i);
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
