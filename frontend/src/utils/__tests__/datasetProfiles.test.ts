import { describe, it, expect } from 'vitest';
import { DATASET_PROFILES, DATASET_IDS, type DatasetProfile } from '../datasetProfiles';

describe('datasetProfiles', () => {
  it('should have exactly 4 registered Kaggle benchmark datasets', () => {
    expect(DATASET_IDS).toHaveLength(4);
    expect(DATASET_IDS).toEqual(['paysim', 'ieee_cis', 'elliptic', 'creditcard']);
  });

  it.each(DATASET_IDS)('should provide valid profile structure for %s', (id: DatasetProfile['id']) => {
    const profile = DATASET_PROFILES[id];
    expect(profile).toBeDefined();
    expect(profile.id).toBe(id);
    expect(profile.label).toBeTruthy();
    expect(profile.subtitle).toBeTruthy();
    expect(profile.badge).toBeTruthy();
    expect(profile.sourceLink).toMatch(/^https:\/\/www\.kaggle\.com\//);
    expect(profile.color).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(profile.icon).toBeTruthy();

    expect(profile.totalSamples).toBeGreaterThan(0);
    expect(profile.fraudRatio).toBeGreaterThan(0);
    expect(profile.fraudRatio).toBeLessThan(1);
    expect(profile.numFeatures).toBeGreaterThan(0);
    expect(profile.fraudPattern).toBeTruthy();

    expect(profile.initialAuc).toBeGreaterThan(0.5);
    expect(profile.targetAuc).toBeGreaterThan(profile.initialAuc);
    expect(profile.initialLoss).toBeGreaterThan(profile.targetLoss);
    expect(profile.lossDecayRate).toBeGreaterThan(0);
    expect(profile.championAucDefault).toBeGreaterThan(0.5);
  });

  it('should have exact verified feature dimensions for each dataset', () => {
    expect(DATASET_PROFILES.paysim.numFeatures).toBe(11);
    expect(DATASET_PROFILES.ieee_cis.numFeatures).toBe(394);
    expect(DATASET_PROFILES.elliptic.numFeatures).toBe(166);
    expect(DATASET_PROFILES.creditcard.numFeatures).toBe(30);
  });
});
