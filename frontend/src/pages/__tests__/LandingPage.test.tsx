import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import LandingPage from '../LandingPage';

// Hoisted Mock for Three.js WebGLRenderer in headless Vitest runner
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

describe('LandingPage Architecture & Navigation Component Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders CF-Intelligence brand header with logo and version badge', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    expect(screen.getByText('CF-Intelligence')).toBeDefined();
    expect(screen.getByText('v2.4.0')).toBeDefined();
  });

  it('contains all 8 required section anchor targets for complete component access', () => {
    const { container } = render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const requiredSectionIds = [
      'hero',
      'problem-solution',
      'how-it-works',
      'product',
      'platform',
      'architecture',
      'security',
      'api',
      'docs',
    ];

    requiredSectionIds.forEach((id) => {
      const section = container.querySelector(`#${id}`);
      expect(section).not.toBeNull();
    });
  });

  it('renders top floating navigation links covering all major components', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    expect(screen.getByText('Overview')).toBeDefined();
    expect(screen.getByText('Problem')).toBeDefined();
    expect(screen.getByText('Workflow')).toBeDefined();
    expect(screen.getByText('Capabilities')).toBeDefined();
    expect(screen.getByText('Platform')).toBeDefined();
    expect(screen.getByText('Architecture')).toBeDefined();
    expect(screen.getByText('Security')).toBeDefined();
    expect(screen.getByText('API & Docs')).toBeDefined();
  });

  it('renders call to action buttons leading to live platform demo dashboard', () => {
    render(
      <BrowserRouter>
        <LandingPage />
      </BrowserRouter>
    );

    const launchDemoButtons = screen.getAllByText(/Launch Demo|Launch Live Platform Demo/i);
    expect(launchDemoButtons.length).toBeGreaterThan(0);
  });
});
