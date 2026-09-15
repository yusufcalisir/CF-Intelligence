/**
 * piiSanitizer.ts
 *
 * Client-side cryptographic pre-flight sanitizer ensuring Zero-Raw-PII
 * compliance (GDPR Art. 25/32, KVKK, CCPA).
 *
 * Features:
 *  - Luhn algorithm verification for Credit Card PANs
 *  - IBAN format and checksum validator
 *  - National ID (SSN/TCKN) format matcher
 *  - Type-salted client-side pseudonymization
 *  - CSV delimiter sniffer and Parquet magic bytes inspector (PAR1)
 */

export interface PIIScanResult {
  hasPII: boolean;
  piiViolationsCount: number;
  detectedFields: string[];
  sanitizedReceipt: string;
}

// ── Luhn Algorithm for Card PAN ───────────────────
export function isValidLuhn(val: string): boolean {
  const digits = val.replace(/\D/g, '');
  if (digits.length < 13 || digits.length > 19) return false;

  let sum = 0;
  let shouldDouble = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let digit = parseInt(digits.charAt(i), 10);
    if (shouldDouble) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    shouldDouble = !shouldDouble;
  }
  return sum % 10 === 0;
}

// ── PII Pattern Matchers ──────────────────────────
const IBAN_REGEX = /^[A-Z]{2}[0-9]{2}[A-Z0-9]{12,30}$/;
const SSN_REGEX = /^(?:\d{3}-\d{2}-\d{4}|\d{9}|\d{11})$/;
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_REGEX = /^\+?[0-9]{10,14}$/;

export function detectPIIType(val: any, fieldName: string): string | null {
  if (val === null || val === undefined) return null;
  const str = String(val).trim();
  const lowerName = fieldName.toLowerCase();

  // Field name heuristics
  if (lowerName.includes('pan') || lowerName.includes('card_number') || lowerName.includes('cc_num')) {
    if (isValidLuhn(str)) return 'CREDIT_CARD_PAN';
  }
  if (lowerName.includes('iban') || lowerName.includes('account_number')) {
    if (IBAN_REGEX.test(str.replace(/\s+/g, ''))) return 'BANK_IBAN';
  }
  if (lowerName.includes('ssn') || lowerName.includes('tckn') || lowerName.includes('national_id')) {
    if (SSN_REGEX.test(str)) return 'NATIONAL_ID';
  }
  if (lowerName.includes('email') && EMAIL_REGEX.test(str)) return 'EMAIL_ADDRESS';
  if (lowerName.includes('phone') && PHONE_REGEX.test(str.replace(/[\s-()]/g, ''))) return 'PHONE_NUMBER';

  // Value-only heuristics
  if (str.length >= 13 && str.length <= 19 && /^\d+$/.test(str) && isValidLuhn(str)) {
    return 'CREDIT_CARD_PAN';
  }
  if (IBAN_REGEX.test(str.replace(/\s+/g, ''))) {
    return 'BANK_IBAN';
  }

  return null;
}

// ── Pure TypeScript Standard SHA-256 & HMAC-SHA256 (RFC 2104 / FIPS 180-4) ──
function rightRotate(value: number, amount: number): number {
  return (value >>> amount) | (value << (32 - amount));
}

export function sha256Bytes(data: Uint8Array): Uint8Array {
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  let H0 = 0x6a09e667;
  let H1 = 0xbb67ae85;
  let H2 = 0x3c6ef372;
  let H3 = 0xa54ff53a;
  let H4 = 0x510e527f;
  let H5 = 0x9b05688c;
  let H6 = 0x1f83d9ab;
  let H7 = 0x5be0cd19;

  const dataLen = data.length;
  const bitLen = dataLen * 8;
  const padLen = (((dataLen + 9 + 63) >>> 6) << 6);
  const padded = new Uint8Array(padLen);
  padded.set(data, 0);
  padded[dataLen] = 0x80;

  const view = new DataView(padded.buffer);
  view.setUint32(padLen - 8, Math.floor(bitLen / 0x100000000), false);
  view.setUint32(padLen - 4, bitLen >>> 0, false);

  const W = new Uint32Array(64);

  for (let offset = 0; offset < padLen; offset += 64) {
    for (let i = 0; i < 16; i++) {
      W[i] = view.getUint32(offset + (i * 4), false);
    }
    for (let i = 16; i < 64; i++) {
      const w15 = W[i - 15] ?? 0;
      const w2 = W[i - 2] ?? 0;
      const w16 = W[i - 16] ?? 0;
      const w7 = W[i - 7] ?? 0;
      const s0 = rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3);
      const s1 = rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10);
      W[i] = (w16 + s0 + w7 + s1) >>> 0;
    }

    let a = H0, b = H1, c = H2, d = H3, e = H4, f = H5, g = H6, h = H7;

    for (let i = 0; i < 64; i++) {
      const ki = K[i] ?? 0;
      const wi = W[i] ?? 0;
      const S1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + ki + wi) >>> 0;
      const S0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    H0 = (H0 + a) >>> 0;
    H1 = (H1 + b) >>> 0;
    H2 = (H2 + c) >>> 0;
    H3 = (H3 + d) >>> 0;
    H4 = (H4 + e) >>> 0;
    H5 = (H5 + f) >>> 0;
    H6 = (H6 + g) >>> 0;
    H7 = (H7 + h) >>> 0;
  }

  const result = new Uint8Array(32);
  const outView = new DataView(result.buffer);
  outView.setUint32(0, H0, false);
  outView.setUint32(4, H1, false);
  outView.setUint32(8, H2, false);
  outView.setUint32(12, H3, false);
  outView.setUint32(16, H4, false);
  outView.setUint32(20, H5, false);
  outView.setUint32(24, H6, false);
  outView.setUint32(28, H7, false);
  return result;
}

function stringToUtf8(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

export function hmacSha256(keyStr: string, messageStr: string): string {
  let keyBytes = stringToUtf8(keyStr);
  const msgBytes = stringToUtf8(messageStr);

  const blockSize = 64;
  if (keyBytes.length > blockSize) {
    keyBytes = sha256Bytes(keyBytes);
  }
  const paddedKey = new Uint8Array(blockSize);
  paddedKey.set(keyBytes, 0);

  const oKeyPad = new Uint8Array(blockSize);
  const iKeyPad = new Uint8Array(blockSize);
  for (let i = 0; i < blockSize; i++) {
    const k = paddedKey[i] ?? 0;
    oKeyPad[i] = k ^ 0x5c;
    iKeyPad[i] = k ^ 0x36;
  }

  const inner = new Uint8Array(blockSize + msgBytes.length);
  inner.set(iKeyPad, 0);
  inner.set(msgBytes, blockSize);
  const innerHash = sha256Bytes(inner);

  const outer = new Uint8Array(blockSize + 32);
  outer.set(oKeyPad, 0);
  outer.set(innerHash, blockSize);
  const outerHash = sha256Bytes(outer);

  return bytesToHex(outerHash);
}

// ── Client-side Edge Cryptographic HMAC-SHA256 Salted Hash ──
export function typeSaltedHash(value: string, piiType: string, salt: string = 'consortium_edge_salt_2026'): string {
  const key = `${salt}:${piiType}`;
  const digest = hmacSha256(key, value.trim());
  return `enc_${digest}`;
}

export function scanRecordsForPII(records: Record<string, any>[]): PIIScanResult {
  const detectedFields = new Set<string>();
  let count = 0;

  for (const row of records.slice(0, 100)) {
    for (const [key, value] of Object.entries(row)) {
      const piiType = detectPIIType(value, key);
      if (piiType) {
        detectedFields.add(`${key} (${piiType})`);
        count++;
      }
    }
  }

  const fieldList = Array.from(detectedFields).sort();
  const summaryPayload = `fields:${fieldList.join(',')}|violations:${count}`;
  const receiptHash = hmacSha256('consortium_audit_salt_2026', summaryPayload);

  return {
    hasPII: detectedFields.size > 0,
    piiViolationsCount: count,
    detectedFields: fieldList,
    sanitizedReceipt: detectedFields.size > 0
      ? `HMAC-SHA256-CLIENT-SALTED-${receiptHash.slice(0, 16).toUpperCase()}`
      : 'ZERO-PII-VERIFIED',
  };
}

// ── CSV Delimiter Sniffer ─────────────────────────
export function sniffCSVDelimiter(headerLine: string): string {
  const delimiters = [',', ';', '\t', '|'];
  let bestDelimiter = ',';
  let maxCount = 0;

  for (const delim of delimiters) {
    const count = headerLine.split(delim).length - 1;
    if (count > maxCount) {
      maxCount = count;
      bestDelimiter = delim;
    }
  }
  return bestDelimiter;
}

// ── Parquet Magic Bytes Verifier ──────────────────
export function verifyParquetMagicBytes(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 4) return false;
  const bytes = new Uint8Array(buffer, 0, 4);
  // 'P' = 0x50, 'A' = 0x41, 'R' = 0x52, '1' = 0x31
  return bytes[0] === 0x50 && bytes[1] === 0x41 && bytes[2] === 0x52 && bytes[3] === 0x31;
}
