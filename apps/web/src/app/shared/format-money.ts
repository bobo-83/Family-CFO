import type { Money } from '../api-client';

export function formatMoney(money: Money): string {
  const sign = money.amount_minor < 0 ? '-' : '';
  const majorUnits = Math.abs(money.amount_minor) / 100;
  return `${sign}${money.currency} ${majorUnits.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
