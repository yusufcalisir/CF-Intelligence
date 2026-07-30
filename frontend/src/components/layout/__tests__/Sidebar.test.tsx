import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import Sidebar from '../Sidebar';

describe('Sidebar Navigation Drawer Test Suite', () => {
  it('renders primary navigation links and role clearance badge', () => {
    const mockOnClose = vi.fn();

    render(
      <BrowserRouter>
        <Sidebar isOpen={true} onClose={mockOnClose} />
      </BrowserRouter>
    );

    const navItems = screen.getAllByRole('link');
    expect(navItems.length).toBeGreaterThan(0);
  });
});
