import type { ComputationAvailability, QualifiedMoney } from '../api-client';
import { formatMoney } from './format-money';

export const unavailableLabel = $localize`:Unavailable monetary decision|A calculation cannot be shown because stored input is unreadable:Unavailable`;

export function formatQualifiedMoney(money: QualifiedMoney): string {
  return formatMoney(money.value);
}

export function partialMoneyNote(money: QualifiedMoney): string | null {
  const count = money.incomplete_count;
  if (count === 0) {
    return null;
  }
  return count === 1
    ? $localize`:Partial monetary total|One stored amount was omitted from a readable total:Partial total—1 stored amount could not be read and was left out.`
    : $localize`:Partial monetary total|Several stored amounts were omitted from a readable total:Partial total—${count}:count: stored amounts could not be read and were left out.`;
}

export function availabilityNote(availability: ComputationAvailability): string | null {
  const count = availability.incomplete_count;
  if (availability.status === 'complete' || count === 0) {
    return null;
  }
  return count === 1
    ? $localize`:Unavailable computation|One unreadable stored amount prevents a calculation:Unavailable—1 stored amount could not be read.`
    : $localize`:Unavailable computation|Several unreadable stored amounts prevent a calculation:Unavailable—${count}:count: stored amounts could not be read.`;
}
