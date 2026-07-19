import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import type {
  EmergencyFundSummary,
  Money,
  NetWorthPoint,
  OutlookEvent as OutlookEventDto,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
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


// M75: human labels for goal types (raw enums leaked into the UI).
const GOAL_TYPE_LABELS: Record<string, string> = {
  emergency_fund: 'Emergency fund',
  vacation: 'Vacation',
  retirement: 'Retirement',
  college: 'College',
  vehicle: 'Vehicle',
  renovation: 'Renovation',
  other: 'Other',
};

@Component({
  selector: 'app-overview',
  imports: [DatePipe, DecimalPipe, FormsModule, RouterLink],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class Overview {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly canWrite = () => {
    return this.auth.hasRight('transactions.manage');
  };

  protected readonly editingTarget = signal(false);
  protected readonly targetInput = signal<number | null>(null);
  protected readonly savingTarget = signal(false);

  protected readonly household = resource({
    loader: async () => {
      const { data, error } = await this.api.getHouseholdContext();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load household overview.'));
      }
      return data;
    },
  });

  // M112 (ADR 0026): the 30-day cash outlook. Degrades gracefully — the rest
  // of the overview renders without it.
  protected readonly outlook = resource({
    loader: async () => {
      const { data } = await this.api.getCashOutlook();
      return data ?? null;
    },
  });

  // M113 (ADR 0027): left to spend this month. Degrades gracefully.
  protected readonly plan = resource({
    loader: async () => {
      const { data } = await this.api.getSpendingPlan();
      return data ?? null;
    },
  });

  /** Running balance after each outlook event, for the day-by-day table. */
  protected outlookRows(): { event: OutlookEventDto; balance: Money }[] {
    const data = this.outlook.value();
    if (!data) {
      return [];
    }
    let running = data.starting_cash.amount_minor;
    return data.events.map((event) => {
      running += event.amount.amount_minor;
      return {
        event,
        balance: { amount_minor: running, currency: data.starting_cash.currency },
      };
    });
  }

  protected readonly formatMoney = formatMoney;

  protected efStatusLabel(fund: EmergencyFundSummary): string {
    return EF_STATUS_LABELS[fund.status];
  }

  protected categoryLabel(category: string): string {
    return CATEGORY_LABELS[category] ?? category;
  }

  // M75: enums are for machines; people get labels.
  protected goalTypeLabel(type: string): string {
    return GOAL_TYPE_LABELS[type] ?? type;
  }

  protected absPercent(value: number): number {
    return Math.abs(value);
  }

  protected startEditTarget(current: number): void {
    this.targetInput.set(current);
    this.editingTarget.set(true);
  }

  protected async saveTarget(): Promise<void> {
    const value = this.targetInput();
    if (value === null || value < 1 || value > 60 || this.savingTarget()) {
      return;
    }
    this.savingTarget.set(true);
    const { error } = await this.api.updateHousehold({ emergency_fund_target_months: value });
    this.savingTarget.set(false);
    if (error) {
      return;
    }
    this.editingTarget.set(false);
    this.household.reload();
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
