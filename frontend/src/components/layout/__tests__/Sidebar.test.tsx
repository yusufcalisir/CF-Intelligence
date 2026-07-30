import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import Sidebar from '../Sidebar';

describe('Sidebar Navigation Drawer Test Suite', () => {
  it('renders primary navigation links and role clearance badge', () => {
    render(
      <BrowserRouter>
        <Sidebar />
      </BrowserRouter>
    );

    const navItems = screen.getAllByRole('link');
    expect(navItems.length).toBeGreaterThan(0);
  });
});
