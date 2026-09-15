import { describe, it, expect } from 'vitest';
import {
  isValidLuhn,
  detectPIIType,
  typeSaltedHash,
  scanRecordsForPII,
  sniffCSVDelimiter,
  verifyParquetMagicBytes,
  hmacSha256,
} from '../piiSanitizer';

describe('piiSanitizer utility', () => {
  describe('isValidLuhn', () => {
    it('validates standard test credit card numbers correctly', () => {
      // 4532... standard test card passes Luhn
      expect(isValidLuhn('4532015112830366')).toBe(true);
      // Corrupted digit fails Luhn
      expect(isValidLuhn('4532015112830367')).toBe(false);
      // Too short
      expect(isValidLuhn('12345')).toBe(false);
    });
  });

  describe('detectPIIType', () => {
    it('detects IBAN, SSN, Credit Card, Email and Phone', () => {
      expect(detectPIIType('TR330006100511123456789012', 'iban')).toBe('BANK_IBAN');
      expect(detectPIIType('123-45-6789', 'ssn')).toBe('NATIONAL_ID');
      expect(detectPIIType('compliance@bank-alpha.com', 'email')).toBe('EMAIL_ADDRESS');
      expect(detectPIIType('+15551234567', 'phone')).toBe('PHONE_NUMBER');
      expect(detectPIIType('4532015112830366', 'card_number')).toBe('CREDIT_CARD_PAN');
      expect(detectPIIType('regular_text', 'channel_type')).toBeNull();
    });
  });

  describe('typeSaltedHash & HMAC-SHA256', () => {
    it('computes authentic 256-bit hexadecimal cryptographic digest', () => {
      const hashed = typeSaltedHash('TR330006100511123456789012', 'BANK_IBAN');
      expect(hashed).toMatch(/^enc_[0-9a-f]{64}$/);

      // Deterministic: identical input yields identical output
      const hashed2 = typeSaltedHash('TR330006100511123456789012', 'BANK_IBAN');
      expect(hashed).toBe(hashed2);

      // Type-salted: different piiType with same value produces different digest
      const hashedDiffType = typeSaltedHash('TR330006100511123456789012', 'ACCOUNT_ID');
      expect(hashed).not.toBe(hashedDiffType);
    });

    it('hmacSha256 produces expected standard digest', () => {
      const digest = hmacSha256('key', 'The quick brown fox jumps over the lazy dog');
      // Known HMAC-SHA256 test vector
      expect(digest).toBe('f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8');
    });
  });

  describe('scanRecordsForPII', () => {
    it('detects violations and returns deterministic HMAC receipt', () => {
      const records = [
        { account_name: 'John Doe', iban: 'TR330006100511123456789012', amount: 500 },
        { ssn: '123-45-6789', amount: 1200 },
      ];
      const result = scanRecordsForPII(records);
      expect(result.hasPII).toBe(true);
      expect(result.piiViolationsCount).toBe(2);
      expect(result.sanitizedReceipt).toMatch(/^HMAC-SHA256-CLIENT-SALTED-[0-9A-F]{16}$/);
    });

    it('reports clean status when no PII present', () => {
      const cleanRecords = [
        { transaction_amount: 150, velocity: 1.2, channel_type: 'WIRE' },
      ];
      const result = scanRecordsForPII(cleanRecords);
      expect(result.hasPII).toBe(false);
      expect(result.piiViolationsCount).toBe(0);
      expect(result.sanitizedReceipt).toBe('ZERO-PII-VERIFIED');
    });
  });

  describe('sniffCSVDelimiter & verifyParquetMagicBytes', () => {
    it('sniffs comma and semicolon delimiters accurately', () => {
      expect(sniffCSVDelimiter('col1,col2,col3')).toBe(',');
      expect(sniffCSVDelimiter('col1;col2;col3')).toBe(';');
      expect(sniffCSVDelimiter('col1\tcol2\tcol3')).toBe('\t');
    });

    it('validates PAR1 header correctly', () => {
      const validBuffer = new Uint8Array([0x50, 0x41, 0x52, 0x31]).buffer;
      expect(verifyParquetMagicBytes(validBuffer)).toBe(true);

      const invalidBuffer = new Uint8Array([0x50, 0x41, 0x52, 0x32]).buffer;
      expect(verifyParquetMagicBytes(invalidBuffer)).toBe(false);
    });
  });
});
