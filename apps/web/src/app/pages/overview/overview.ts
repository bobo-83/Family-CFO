import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject, resource } from '@angular/core';
import { RouterLink } from '@angular/router';
import type { EmergencyFundSummary, Money, NetWorthPoint } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

const EF_STATUS_LABELS: Record<EmergencyFundSummary['status'], string> = {
  no_bills: 'Add bills to measure',
  no_fund: 'Not started',
  getting_started: 'Getting started',
  on_track: 'On track',
  fully_funded: 'Fully funded',
};

const CATEGORY_LABELS: Record<string, string> = {
  liquid: 'Cash',
  investments: 'Investments',
  retirement: 'Retirement',
  education: 'Education',
  property: 'Property',
};

@Component({
  selector: 'app-overview',
  imports: [DatePipe, DecimalPipe, RouterLink],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class Overview {
  private readonly api = inject(ApiService);

  protected readonly household = resource({
    loader: async () => {
      const { data, error } = await this.api.getHouseholdContext();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load household overview.'));
      }
      return data;
    },
  });

  protected readonly formatMoney = formatMoney;

  protected efStatusLabel(fund: EmergencyFundSummary): string {
    return EF_STATUS_LABELS[fund.status];
  }

  protected categoryLabel(category: string): string {
    return CATEGORY_LABELS[category] ?? category;
  }

  protected absPercent(value: number): number {
    return Math.abs(value);
  }

  protected dueLabel(daysUntil: number): string {
    if (daysUntil <= 0) {
      return 'Due today';
    }
    if (daysUntil === 1) {
      return 'Due tomorrow';
    }
    return `Due in ${daysUntil} days`;
  }

  /**
   * M40: net-worth trend as an SVG polyline over a fixed 100x28 viewBox.
   * Returns null when there are fewer than two points to connect.
   */
  protected sparklinePoints(history: NetWorthPoint[]): string | null {
    if (!history || history.length < 2) {
      return null;
    }
    const values = history.map((p) => p.net_worth.amount_minor);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const width = 100;
    const height = 28;
    const stepX = width / (values.length - 1);
    return values
      .map((value, index) => {
        const x = index * stepX;
        // Invert: higher net worth sits nearer the top of the viewBox.
        const y = height - ((value - min) / range) * height;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  /** Change from the earliest shown snapshot to the latest. */
  protected netWorthChange(history: NetWorthPoint[]): Money | null {
    if (!history || history.length < 2) {
      return null;
    }
    const first = history[0].net_worth;
    const last = history[history.length - 1].net_worth;
    return {
      amount_minor: last.amount_minor - first.amount_minor,
      currency: last.currency,
    };
  }
}
