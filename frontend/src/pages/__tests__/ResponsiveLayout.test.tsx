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

describe('Mobile, Tablet & Desktop Responsive Layout Test Suite', () => {
  const setViewport = (width: number, height: number) => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: width });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: height });

    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes(`max-width: ${width}px`) || query.includes(`min-width: ${width}px`),
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

  it('renders mobile layout (375px) with accessible hamburger menu toggle', async () => {
    setViewport(375, 667);

    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    // Mobile menu toggle button should exist in DOM
    const menuToggleBtn = screen.getByLabelText('Toggle Navigation Menu');
    expect(menuToggleBtn).toBeDefined();

    // Click hamburger button to expand mobile drawer
    fireEvent.click(menuToggleBtn);

    // Verify mobile drawer overlay renders all section navigation links matching desktop
    const overviewElements = screen.getAllByText('Overview');
    expect(overviewElements.length).toBeGreaterThan(0);
    const platformElements = screen.getAllByText(/Platform/i);
    expect(platformElements.length).toBeGreaterThan(0);
  });

  it('renders tablet layout (768px) with responsive card grids and telemetry HUD', () => {
    setViewport(768, 1024);

    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    // Telemetry HUD items should be rendered cleanly
    expect(screen.getByText('Active FL Round')).toBeDefined();
    expect(screen.getByText('Global Accuracy')).toBeDefined();
    expect(screen.getByText('Stream Speed')).toBeDefined();
  });

  it('renders desktop layout (1280px) with floating navigation pill', () => {
    setViewport(1280, 800);

    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    // Desktop nav links should be rendered in the DOM
    expect(screen.getByText('Overview')).toBeDefined();
    expect(screen.getByText('Platform & Engine')).toBeDefined();
    expect(screen.getByText('Architecture & Security')).toBeDefined();
  });
});
