import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Router, RouterLink } from '@angular/router';
import type {
  YearlyOverview,
  CashOutlookResponse,
  EmergencyFundSummary,
  HouseholdContext,
  Money,
  NetWorthPoint,
  OutlookEvent as OutlookEventDto,
  RecurringFrequency,
  SavingsContribution,
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


// #201: enums are for machines; a savings row reads "USD 500.00 monthly".
const CADENCE_WORDS: Record<RecurringFrequency, string> = {
  weekly: 'weekly',
  biweekly: 'every two weeks',
  semimonthly: 'twice a month',
  monthly: 'monthly',
  quarterly: 'quarterly',
  semiannual: 'twice a year',
  annual: 'yearly',
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
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    ReactiveFormsModule,
    RouterLink,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class Overview {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly canWrite = () => {
    return this.auth.hasRight('transactions.manage');
  };

  protected readonly canChat = () => {
    return this.auth.hasRight('advisor.use');
  };

  protected readonly editingTarget = signal(false);
  protected readonly targetInput = signal<number | null>(null);
  protected readonly savingTarget = signal(false);

  // --- M-yearly: the Overview's Year mode ------------------------------------
  protected readonly yearMode = signal(false);
  protected readonly yearData = signal<YearlyOverview | null>(null);
  protected readonly yearLoading = signal(false);
  protected readonly yearGenerating = signal(false);
  protected readonly yearError = signal<string | null>(null);
  /** The month clicked in the chart — its numbers show in the detail strip. */
  protected readonly yearFocusMonth = signal<string | null>(null);

  protected toggleYearMode(on: boolean): void {
    this.yearMode.set(on);
    if (on && !this.yearData()) {
      void this.loadYear();
    }
  }

  protected async loadYear(year?: number): Promise<void> {
    this.yearLoading.set(true);
    this.yearError.set(null);
    this.yearFocusMonth.set(null);
    const { data, error } = await this.api.getYearlyOverview(year);
    this.yearLoading.set(false);
    if (error || !data) {
      this.yearError.set(apiErrorMessage(error, 'Failed to load the year.'));
      return;
    }
    this.yearData.set(data);
  }

  protected stepYear(delta: number): void {
    const current = this.yearData()?.year ?? new Date().getFullYear();
    void this.loadYear(current + delta);
  }

  protected async generateYearReview(): Promise<void> {
    if (this.yearGenerating()) {
      return;
    }
    this.yearGenerating.set(true);
    this.yearError.set(null);
    const { data, error } = await this.api.generateYearlyReview(this.yearData()?.year);
    this.yearGenerating.set(false);
    if (error || !data) {
      this.yearError.set(apiErrorMessage(error, 'Could not write the year review.'));
      return;
    }
    const overview = this.yearData();
    if (overview) {
      this.yearData.set({ ...overview, review: data });
    }
  }

  /** Bar height (0-100) against the year's largest monthly flow. */
  protected yearBarHeight(minor: number): number {
    const overview = this.yearData();
    if (!overview) {
      return 0;
    }
    const peak = Math.max(
      1,
      ...overview.months.flatMap((m) => [m.income.amount_minor, m.spending.amount_minor]),
    );
    return Math.round((Math.max(0, minor) / peak) * 100);
  }

  protected yearMonthLabel(month: string): string {
    const index = Number(month.slice(5, 7)) - 1;
    return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][index] ?? month;
  }

  protected yearFocused() {
    const overview = this.yearData();
    const month = this.yearFocusMonth();
    return overview?.months.find((m) => m.month === month) ?? null;
  }

  protected yearMonthLongLabel(month: string): string {
    const index = Number(month.slice(5, 7)) - 1;
    const name = ['January','February','March','April','May','June','July','August','September','October','November','December'][index];
    return name ? `${name} ${month.slice(0, 4)}` : month;
  }

  /**
   * ADR 0068: hand the advisor a grounded "what made this bar up?" question.
   * Same wording as iOS; the advisor's month-scoped tools do the digging.
   */
  protected askAboutMonth(month: string, kind: 'income' | 'spending'): void {
    const label = this.yearMonthLongLabel(month);
    const ask =
      kind === 'income'
        ? `What made up my income in ${label}? List where the money came from.`
        : `What made up my spending in ${label}? Break it down by category and biggest merchants.`;
    void this.router.navigate(['/chat'], { queryParams: { ask } });
  }

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

  /**
   * ADR 0069 headline verb. M-rsu-grants: with grants and a live quote the
   * server translates the shortfall into whole shares — "Sell RSUs (≈ 12 XYZ)".
   */
  protected runwayActionLabel(cash: CashOutlookResponse): string {
    if (cash.runway_action === 'move_cash') {
      return 'Free up cash';
    }
    return cash.sell_units && cash.sell_ticker
      ? `Sell RSUs (≈ ${cash.sell_units} ${cash.sell_ticker})`
      : 'Sell RSUs';
  }

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

  // --- #201: detected recurring savings contributions ------------------------

  /**
   * Required honesty footnote, verbatim on both platforms: detection only sees
   * transfers, so a 401(k) withheld before pay lands is invisible here.
   */
  protected readonly savingsFootnote =
    'Detected from transfers between your accounts. ' +
    "Payroll deductions like a 401(k) don't appear here.";

  /**
   * #207: explains the "inferred" marker. Informational, not a warning — a 529
   * the aggregator doesn't carry is the normal case, not a degraded result.
   */
  protected readonly savingsInferredFootnote =
    'Rows marked inferred were matched from the money leaving your account — ' +
    "the destination isn't synced.";

  protected hasInferredContribution(contributions: SavingsContribution[]): boolean {
    return contributions.some((c) => c.inferred === true);
  }

  protected cadenceWord(frequency: RecurringFrequency): string {
    return CADENCE_WORDS[frequency] ?? frequency;
  }

  /**
   * "USD 500.00 monthly · seen 4 times". A declared row carries no evidence
   * count — it is a stated fact, so quoting occurrences would invent one.
   */
  protected contributionDetail(contribution: SavingsContribution): string {
    const cadence = `${formatMoney(contribution.amount)} ${this.cadenceWord(contribution.frequency)}`;
    if (contribution.declared) {
      return cadence;
    }
    const times =
      contribution.occurrences === 1 ? 'seen 1 time' : `seen ${contribution.occurrences} times`;
    return `${cadence} · ${times}`;
  }

  /**
   * Cadences differ, so only the server's monthly_equivalent can be summed —
   * adding the raw amounts would call a yearly USD 1,200 a monthly USD 1,200.
   * Null when nothing was detected: the section is hidden, never zeroed.
   */
  protected savingsMonthlyTotal(contributions: SavingsContribution[]): Money | null {
    if (!contributions || contributions.length === 0) {
      return null;
    }
    return {
      amount_minor: contributions.reduce((sum, c) => sum + c.monthly_equivalent.amount_minor, 0),
      currency: contributions[0].monthly_equivalent.currency,
    };
  }

  // --- #203: declaring what detection cannot see -----------------------------

  /**
   * Never null, unlike #201's hide-when-empty: zero detections is the ordinary
   * outcome when the destination account never syncs, so the section stays and
   * offers the declaration instead of disappearing.
   */
  protected savingsRows(context: HouseholdContext): SavingsContribution[] {
    return context.savings_contributions ?? [];
  }

  protected readonly cadences = Object.keys(CADENCE_WORDS) as RecurringFrequency[];

  protected readonly declaring = signal(false);
  protected readonly savingsSubmitting = signal(false);
  protected readonly savingsError = signal<string | null>(null);

  /**
   * The overview never otherwise needs the account list, so it is fetched only
   * once the form opens — an idle params() keeps the first paint one call lighter.
   */
  protected readonly accounts = resource({
    params: () => (this.declaring() ? true : undefined),
    loader: async () => {
      const { data, error } = await this.api.listAccounts();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load accounts.'));
      }
      return data.accounts;
    },
  });

  protected readonly declareForm = this.formBuilder.nonNullable.group({
    sourceAccountId: ['', Validators.required],
    destinationAccountId: ['', Validators.required],
    amount: [0, [Validators.required, Validators.min(0.01)]],
    frequency: ['monthly' as RecurringFrequency, Validators.required],
  });

  protected startDeclaring(): void {
    this.savingsError.set(null);
    this.declaring.set(true);
  }

  protected cancelDeclaring(): void {
    this.declaring.set(false);
    this.resetDeclareForm();
  }

  private resetDeclareForm(): void {
    this.declareForm.reset({
      sourceAccountId: '',
      destinationAccountId: '',
      amount: 0,
      frequency: 'monthly',
    });
  }

  protected async declareContribution(currency: string): Promise<void> {
    if (this.declareForm.invalid || this.savingsSubmitting()) {
      this.declareForm.markAllAsTouched();
      return;
    }
    this.savingsSubmitting.set(true);
    this.savingsError.set(null);
    const { sourceAccountId, destinationAccountId, amount, frequency } =
      this.declareForm.getRawValue();
    const { error } = await this.api.declareSavingsContribution({
      source_account_id: sourceAccountId,
      destination_account_id: destinationAccountId,
      amount: { amount_minor: Math.round(amount * 100), currency },
      frequency,
    });
    this.savingsSubmitting.set(false);
    if (error) {
      this.savingsError.set(apiErrorMessage(error, 'Failed to save the contribution.'));
      return;
    }
    this.declaring.set(false);
    this.resetDeclareForm();
    this.household.reload();
  }

  protected async stopTracking(contribution: SavingsContribution): Promise<void> {
    const contributionId = contribution.contribution_id;
    if (!contributionId || this.savingsSubmitting()) {
      return;
    }
    this.savingsSubmitting.set(true);
    this.savingsError.set(null);
    const { error } = await this.api.deleteSavingsContribution(contributionId);
    this.savingsSubmitting.set(false);
    if (error) {
      this.savingsError.set(apiErrorMessage(error, 'Failed to stop tracking that contribution.'));
      return;
    }
    this.household.reload();
  }

  /**
   * A route needs both ends to be dismissed. The source is empty when only the
   * arrival synced, and there is nothing to name in that case.
   */
  protected canDismiss(contribution: SavingsContribution): boolean {
    return (
      !contribution.declared &&
      !!contribution.source_account_id &&
      !!contribution.destination_account_id
    );
  }

  protected async dismissContribution(contribution: SavingsContribution): Promise<void> {
    if (!this.canDismiss(contribution) || this.savingsSubmitting()) {
      return;
    }
    this.savingsSubmitting.set(true);
    this.savingsError.set(null);
    const { error } = await this.api.dismissSavingsContribution({
      source_account_id: contribution.source_account_id ?? '',
      destination_account_id: contribution.destination_account_id ?? '',
    });
    this.savingsSubmitting.set(false);
    if (error) {
      this.savingsError.set(apiErrorMessage(error, 'Failed to dismiss that transfer.'));
      return;
    }
    this.household.reload();
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
