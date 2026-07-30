import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import Header from '../Header';

describe('Header Component Test Suite', () => {
  it('renders application branding and active node status', () => {
    const mockOnMenuClick = vi.fn();

    render(
      <BrowserRouter>
        <Header onMenuClick={mockOnMenuClick} />
      </BrowserRouter>
    );

    const elements = screen.getAllByText(/CF-Intelligence|JPMorgan Chase|Node|System Status|Quorum|API Docs/i);
    expect(elements.length).toBeGreaterThan(0);
  });
});
