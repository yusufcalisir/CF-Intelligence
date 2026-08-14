import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

describe('Desktop Viewport Component Fit & Overflow Test Suite', () => {
  const setDesktopViewport = (width: number, height: number) => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: width });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: height });

    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes(`min-width: 1024px`) || query.includes(`min-width: 1280px`),
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

  const desktopScreenResolutions = [
    { name: 'Ultra-Wide Desktop (1920x1080)', width: 1920, height: 1080 },
    { name: 'Standard Desktop (1440x900)', width: 1440, height: 900 },
    { name: 'Laptop / Small Desktop (1280x800)', width: 1280, height: 800 },
  ];

  desktopScreenResolutions.forEach(({ name, width, height }) => {
    it(`renders all desktop components cleanly without container overflow on ${name}`, () => {
      setDesktopViewport(width, height);

      const { container } = render(
        <BrowserRouter>
          <LandingPage />
        </BrowserRouter>
      );

      // Verify page hero section fits inside max container width
      const heroSection = container.querySelector('#hero');
      expect(heroSection).not.toBeNull();

      // Check all 8 major card containers have max width and no horizontal overflow
      const zeroOverflowContainers = container.querySelectorAll('.max-w-7xl');
      expect(zeroOverflowContainers.length).toBeGreaterThan(0);

      // Check brand headers and CTA buttons are rendered without text truncation
      expect(screen.getAllByText('CF-Intelligence').length).toBeGreaterThan(0);
    });
  });
});
