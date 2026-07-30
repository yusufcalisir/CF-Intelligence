import { describe, it, expect } from 'vitest';
import { formatPercent, formatDelta, formatNumber, formatDuration, formatMs, classNames } from '../formatters';

describe('Utility Formatters Test Suite', () => {
  it('formats numbers with locale separators', () => {
    expect(formatNumber(1450000)).toBe('1,450,000');
    expect(formatNumber(0)).toBe('0');
  });

  it('formats percentage values with decimal precision', () => {
    expect(formatPercent(0.9842)).toBe('98.4%');
    expect(formatPercent(0.5)).toBe('50.0%');
  });

  it('formats delta change values with +/- signs', () => {
    expect(formatDelta(0.052)).toBe('+5.20%');
    expect(formatDelta(-0.021)).toBe('-2.10%');
  });

  it('formats duration in seconds and milliseconds', () => {
    expect(formatDuration(45)).toBe('45.0s');
    expect(formatMs(120)).toBe('120ms');
  });

  it('combines truthy CSS class names', () => {
    expect(classNames('px-4', false, 'py-2', null, 'bg-slate-900')).toBe('px-4 py-2 bg-slate-900');
  });
});
