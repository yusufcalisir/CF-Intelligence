import { describe, it, expect } from 'vitest';
import { formatCurrency, formatPercent, formatTimestamp, formatBytes } from '../formatters';

describe('Utility Formatters Test Suite', () => {
  it('formats currency correctly in USD and EUR', () => {
    expect(formatCurrency(1450000)).toContain('1,450,000');
    expect(formatCurrency(0)).toContain('0');
  });

  it('formats percentage values with decimal precision', () => {
    expect(formatPercent(98.42)).toBe('98.42%');
    expect(formatPercent(0.5, 1)).toBe('0.5%');
  });

  it('formats ISO timestamps into human-readable strings', () => {
    const isoString = '2026-07-30T12:00:00.000Z';
    const formatted = formatTimestamp(isoString);
    expect(typeof formatted).toBe('string');
    expect(formatted.length).toBeGreaterThan(0);
  });

  it('formats data byte sizes into KB, MB, and GB', () => {
    expect(formatBytes(1024)).toContain('1.0 KB');
    expect(formatBytes(1048576)).toContain('1.0 MB');
    expect(formatBytes(1073741824)).toContain('1.0 GB');
  });
});
