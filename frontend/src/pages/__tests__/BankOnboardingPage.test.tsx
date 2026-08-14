import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import BankOnboardingPage from '../BankOnboardingPage';

describe('BankOnboardingPage', () => {
  it('renders onboarding header and step 1 legal information form', () => {
    render(
      <BrowserRouter>
        <BankOnboardingPage />
      </BrowserRouter>
    );

    expect(screen.getByText(/Bank Node Onboarding Wizard/i)).toBeInTheDocument();
    expect(screen.getByText(/Register institution, issue cryptographic X\.509 credentials/i)).toBeInTheDocument();
    expect(screen.getByText(/Step 1: Institutional Legal & Regional Profile/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/Delta International Bank AG/i)).toBeInTheDocument();
  });

  it('allows editing bank details and advancing to step 2 review', () => {
    render(
      <BrowserRouter>
        <BankOnboardingPage />
      </BrowserRouter>
    );

    const nameInput = screen.getByDisplayValue(/Delta International Bank AG/i);
    fireEvent.change(nameInput, { target: { value: 'Nordic Credit Union AB' } });
    expect(nameInput).toHaveValue('Nordic Credit Union AB');

    const nextBtn = screen.getByText(/Continue to Step 2/i);
    fireEvent.click(nextBtn);

    expect(screen.getByText(/Step 2: Review Registration Details/i)).toBeInTheDocument();
    expect(screen.getByText(/Nordic Credit Union AB/i)).toBeInTheDocument();
  });
});
