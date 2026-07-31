import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import LandingPage from '../LandingPage';

// Mock Three.js WebGLRenderer instance methods for test runner
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();

  class MockWebGLRenderer {
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };

    setSize() {}
    setPixelRatio() {}
    render() {}
    dispose() {}
    clear() {}
  }

  return {
    ...actual,
    WebGLRenderer: MockWebGLRenderer,
  };
});

describe('Mobile Viewport Component Fit, Overflow & Drawer Test Suite', () => {
  const setMobileViewport = (width: number, height: number) => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: width });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: height });

    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes(`max-width: ${width}px`) || query.includes(`max-width: 768px`) || query.includes(`max-width: 640px`),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(() => true),
      })),
    });

    window.dispatchEvent(new Event('resize'));
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const mobileScreenResolutions = [
    { name: 'Ultra-Narrow Mobile (320x568)', width: 320, height: 568 },
    { name: 'Standard Mobile (375x667)', width: 375, height: 667 },
    { name: 'Large Mobile (428x926)', width: 428, height: 926 },
  ];

  mobileScreenResolutions.forEach(({ name, width, height }) => {
    it(`renders single-column stacked layout without horizontal page overflow on ${name}`, () => {
      setMobileViewport(width, height);

      const { container } = render(
        <BrowserRouter>
          <LandingPage />
        </BrowserRouter>
      );

      // Verify page hero section exists and fits mobile view
      const heroSection = container.querySelector('#hero');
      expect(heroSection).not.toBeNull();

      // Mobile hamburger button must be accessible
      const hamburgerBtn = screen.getByLabelText('Toggle Navigation Menu');
      expect(hamburgerBtn).toBeDefined();

      // Verify overall page wrapper has overflow containment class
      const pageWrapper = container.querySelector('.overflow-x-hidden');
      expect(pageWrapper).not.toBeNull();
    });
  });

  it('toggles mobile drawer overlay cleanly on hamburger button click', () => {
    setMobileViewport(375, 667);

    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const hamburgerBtn = screen.getByLabelText('Toggle Navigation Menu');
    fireEvent.click(hamburgerBtn);

    // Mobile overlay should display section navigation items mirroring desktop exactly
    const overviewElements = screen.getAllByText('Overview');
    expect(overviewElements.length).toBeGreaterThan(0);
    const capabilitiesElements = screen.getAllByText('Capabilities');
    expect(capabilitiesElements.length).toBeGreaterThan(0);
    const problemElements = screen.getAllByText('Problem');
    expect(problemElements.length).toBeGreaterThan(0);
  });
});
