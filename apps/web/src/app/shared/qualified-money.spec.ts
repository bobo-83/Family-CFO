import { describe, expect, it } from 'vitest';
import {
  availabilityNote,
  formatQualifiedMoney,
  partialMoneyNote,
  unavailableLabel,
} from './qualified-money';

describe('qualified money presentation', () => {
  it('formats complete and partial zero values without hiding zero', () => {
    expect(formatQualifiedMoney({
      value: { amount_minor: 0, currency: 'USD' },
      incomplete_count: 0,
    })).toBe('USD 0.00');
    expect(partialMoneyNote({
      value: { amount_minor: 0, currency: 'USD' },
      incomplete_count: 1,
    })).toContain('Partial total—1 stored amount');
  });

  it('pluralizes partial and unavailable counts', () => {
    expect(partialMoneyNote({
      value: { amount_minor: 125, currency: 'USD' },
      incomplete_count: 2,
    })).toContain('2 stored amounts');
    expect(availabilityNote({ status: 'unavailable', incomplete_count: 3 }))
      .toContain('3 stored amounts');
  });

  it('omits notes for complete values and exposes the shared unavailable label', () => {
    expect(partialMoneyNote({
      value: { amount_minor: 125, currency: 'USD' },
      incomplete_count: 0,
    })).toBeNull();
    expect(availabilityNote({ status: 'complete', incomplete_count: 0 })).toBeNull();
    expect(unavailableLabel).toBe('Unavailable');
  });
});
