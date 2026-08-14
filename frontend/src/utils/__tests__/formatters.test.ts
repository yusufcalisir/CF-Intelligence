import { describe, it, expect } from 'vitest';
import {
  formatPercent,
  formatDelta,
  formatNumber,
  formatDuration,
  formatMs,
  classNames,
} from '../formatters';

describe('Utility Formatters Test Suite (Mutation Hardened)', () => {
  it('formats numbers with locale separators and zero handling', () => {
    expect(formatNumber(1450000)).toBe('1,450,000');
    expect(formatNumber(0)).toBe('0');
    expect(formatNumber(-500)).toBe('-500');
  });

  it('formats percentage values with decimal precision', () => {
    expect(formatPercent(0.9842)).toBe('98.4%');
    expect(formatPercent(0.5)).toBe('50.0%');
    expect(formatPercent(0)).toBe('0.0%');
  });

  it('formats delta change values with exact boundary handling (value >= 0 mutant protection)', () => {
    expect(formatDelta(0.052)).toBe('+5.20%');
    expect(formatDelta(-0.021)).toBe('-2.10%');
    // Boundary check for value === 0 to kill mutation from `>= 0` to `> 0`
    expect(formatDelta(0)).toBe('+0.00%');
    expect(formatDelta(-0.0001)).toBe('-0.01%');
  });

  it('formats duration in seconds and minutes with exact 60s boundary handling', () => {
    expect(formatDuration(45)).toBe('45.0s');
    expect(formatDuration(0)).toBe('0.0s');
    expect(formatDuration(59.9)).toBe('59.9s');
    // Boundary check for seconds === 60 to kill mutation from `< 60` to `<= 60`
    expect(formatDuration(60)).toBe('1m 0s');
    expect(formatDuration(125)).toBe('2m 5s');
  });

  it('formats milliseconds with exact 1000ms boundary handling', () => {
    expect(formatMs(120)).toBe('120ms');
    expect(formatMs(0)).toBe('0ms');
    expect(formatMs(999)).toBe('999ms');
    // Boundary check for ms === 1000 to kill mutation from `< 1000` to `<= 1000`
    expect(formatMs(1000)).toBe('1.00s');
    expect(formatMs(2500)).toBe('2.50s');
  });

  it('combines truthy CSS class names and filters all falsy variants', () => {
    expect(classNames('px-4', false, 'py-2', null, 'bg-slate-900', undefined)).toBe('px-4 py-2 bg-slate-900');
    expect(classNames()).toBe('');
    expect(classNames('w-full')).toBe('w-full');
  });
});
