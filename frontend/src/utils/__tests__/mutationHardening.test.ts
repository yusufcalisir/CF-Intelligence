import { describe, it, expect } from 'vitest';
import { formatDelta, formatDuration, formatMs, formatPercent, classNames } from '../formatters';
import { DATASET_PROFILES, DATASET_IDS } from '../datasetProfiles';

describe('Mutation Testing & Fault Injection Hardening Suite', () => {
  describe('Boundary Condition Mutants in Formatters', () => {
    it('kills relational mutator: formatDelta value >= 0 boundary', () => {
      // Mutant: value > 0 instead of value >= 0
      expect(formatDelta(0)).toBe('+0.00%');
      expect(formatDelta(0.00001)).toBe('+0.00%');
      expect(formatDelta(-0.00001)).toBe('-0.00%');
      expect(formatDelta(-0.05)).toBe('-5.00%');
    });

    it('kills relational mutator: formatDuration seconds < 60 boundary', () => {
      // Mutant: seconds <= 60 instead of seconds < 60
      expect(formatDuration(0)).toBe('0.0s');
      expect(formatDuration(59)).toBe('59.0s');
      expect(formatDuration(59.9)).toBe('59.9s');
      expect(formatDuration(60)).toBe('1m 0s');
      expect(formatDuration(60.1)).toBe('1m 0s');
      expect(formatDuration(120)).toBe('2m 0s');
    });

    it('kills relational mutator: formatMs ms < 1000 boundary', () => {
      // Mutant: ms <= 1000 instead of ms < 1000
      expect(formatMs(0)).toBe('0ms');
      expect(formatMs(999)).toBe('999ms');
      expect(formatMs(1000)).toBe('1.00s');
      expect(formatMs(1001)).toBe('1.00s');
      expect(formatMs(5432)).toBe('5.43s');
    });

    it('kills arithmetic mutator: formatPercent calculation precision', () => {
      // Mutant: value / 100 instead of value * 100
      expect(formatPercent(1.0)).toBe('100.0%');
      expect(formatPercent(0.0123)).toBe('1.2%');
      expect(formatPercent(0)).toBe('0.0%');
    });

    it('kills boolean filter mutator in classNames', () => {
      // Mutant: removing Boolean filter or returning empty string
      const result = classNames('btn', false && 'hidden', 'btn-primary', null, undefined, '', 'active');
      expect(result).toBe('btn btn-primary active');
      expect(classNames()).toBe('');
    });
  });

  describe('Dataset Profile Registry Invariants', () => {
    it('kills dataset profile registry structure and completeness mutants', () => {
      expect(DATASET_IDS).toHaveLength(4);
      expect(DATASET_IDS).toEqual(['paysim', 'ieee_cis', 'elliptic', 'creditcard']);

      for (const id of DATASET_IDS) {
        const profile = DATASET_PROFILES[id];
        expect(profile.id).toBe(id);
        expect(profile.totalSamples).toBeGreaterThan(0);
        expect(profile.fraudRatio).toBeGreaterThan(0);
        expect(profile.fraudRatio).toBeLessThan(1);
        expect(profile.initialAuc).toBeGreaterThan(0.5);
        expect(profile.targetAuc).toBeGreaterThan(profile.initialAuc);
        expect(profile.initialLoss).toBeGreaterThan(profile.targetLoss);
        expect(profile.color.startsWith('#')).toBe(true);
        expect(profile.icon.length).toBeGreaterThan(0);
      }
    });
  });
});
