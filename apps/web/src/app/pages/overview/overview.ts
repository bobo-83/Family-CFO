import { DatePipe, DecimalPipe } from '@angular/common';
import { FREQUENCY_LABELS } from '../../shared/enum-labels';
import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
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
  SavingsRate,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { TimezonePicker } from '../../shared/timezone-picker/timezone-picker';
import { TIMEZONE_BOX_DEFAULT, TIMEZONE_HINT } from '../../shared/timezones';
import { formatMoney } from '../../shared/format-money';

const EF_STATUS_LABELS: Record<EmergencyFundSummary['status'], string> = {
  no_bills: $localize`:Emergency fund status|No bills recorded so coverage cannot be measured:Add bills to measure`,
  no_fund: $localize`:Emergency fund status|Nothing set aside yet:Not started`,
  getting_started: $localize`:Emergency fund status|Some months of cover saved:Getting started`,
  on_track: $localize`:Emergency fund status|Close to the target months of cover:On track`,
  fully_funded: $localize`:Emergency fund status|Target months of cover reached:Fully funded`,
};

const CATEGORY_LABELS: Record<string, string> = {
  liquid: $localize`:Asset category|Cash and current accounts:Cash`,
  investments: $localize`:Asset category|Brokerage and investment accounts:Investments`,
  retirement: $localize`:Asset category|Retirement accounts such as a 401(k):Retirement`,
  education: $localize`:Asset category|Education savings such as a 529:Education`,
  property: $localize`:Asset category|Real estate and other property:Property`,
};


// #201: enums are for machines; a savings row reads "USD 500.00 monthly".

// M75: human labels for goal types (raw enums leaked into the UI).
const GOAL_TYPE_LABELS: Record<string, string> = {
  emergency_fund: $localize`:Goal type|Saving for an emergency fund:Emergency fund`,
  vacation: $localize`:Goal type|Saving for a holiday:Vacation`,
  retirement: $localize`:Goal type|Saving for retirement:Retirement`,
  college: $localize`:Goal type|Saving for college tuition:College`,
  vehicle: $localize`:Goal type|Saving for a car or other vehicle:Vehicle`,
  renovation: $localize`:Goal type|Saving for home renovation:Renovation`,
  other: $localize`:Goal type|Any other kind of savings goal:Other`,
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
    MatSlideToggleModule,
    MatButtonModule,
    TimezonePicker,
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

  // #10: household language — the server enforces this right on updateHousehold.
  protected readonly canManageSettings = () => {
    return this.auth.hasRight('household.settings.manage');
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
      this.yearError.set(
        apiErrorMessage(error, $localize`:Error message|The yearly overview could not be loaded:Failed to load the year.`),
      );
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
      this.yearError.set(
        apiErrorMessage(error, $localize`:Error message|The AI written year review could not be generated:Could not write the year review.`),
      );
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
    return [
      $localize`:Month abbreviation|Short name for January:Jan`,
      $localize`:Month abbreviation|Short name for February:Feb`,
      $localize`:Month abbreviation|Short name for March:Mar`,
      $localize`:Month abbreviation|Short name for April:Apr`,
      $localize`:Month abbreviation|Short name for May:May`,
      $localize`:Month abbreviation|Short name for June:Jun`,
      $localize`:Month abbreviation|Short name for July:Jul`,
      $localize`:Month abbreviation|Short name for August:Aug`,
      $localize`:Month abbreviation|Short name for September:Sep`,
      $localize`:Month abbreviation|Short name for October:Oct`,
      $localize`:Month abbreviation|Short name for November:Nov`,
      $localize`:Month abbreviation|Short name for December:Dec`,
    ][index] ?? month;
  }

  protected yearFocused() {
    const overview = this.yearData();
    const month = this.yearFocusMonth();
    return overview?.months.find((m) => m.month === month) ?? null;
  }

  protected yearMonthLongLabel(month: string): string {
    const index = Number(month.slice(5, 7)) - 1;
    const name = [
      $localize`:Month name|Full name of the first month:January`,
      $localize`:Month name|Full name of the second month:February`,
      $localize`:Month name|Full name of the third month:March`,
      $localize`:Month name|Full name of the fourth month:April`,
      $localize`:Month name|Full name of the fifth month:May`,
      $localize`:Month name|Full name of the sixth month:June`,
      $localize`:Month name|Full name of the seventh month:July`,
      $localize`:Month name|Full name of the eighth month:August`,
      $localize`:Month name|Full name of the ninth month:September`,
      $localize`:Month name|Full name of the tenth month:October`,
      $localize`:Month name|Full name of the eleventh month:November`,
      $localize`:Month name|Full name of the twelfth month:December`,
    ][index];
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
        ? $localize`:Advisor question|Prefilled question about a month's income:What made up my income in ${label}:month:? List where the money came from.`
        : $localize`:Advisor question|Prefilled question about a month's spending:What made up my spending in ${label}:month:? Break it down by category and biggest merchants.`;
    void this.router.navigate(['/chat'], { queryParams: { ask } });
  }

  protected readonly household = resource({
    loader: async () => {
      const { data, error } = await this.api.getHouseholdContext();
      if (error) {
        throw new Error(
          apiErrorMessage(error, $localize`:Error message|The overview page data could not be loaded:Failed to load household overview.`),
        );
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
      return $localize`:Cash outlook action|Move money from savings into the current account:Free up cash`;
    }
    return cash.sell_units && cash.sell_ticker
      ? $localize`:Cash outlook action|Sell vested stock, with the share count and ticker:Sell RSUs (≈ ${cash.sell_units}:units: ${cash.sell_ticker}:ticker:)`
      : $localize`:Cash outlook action|Sell vested stock to cover the shortfall:Sell RSUs`;
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
  protected readonly savingsFootnote = $localize`:Savings section footnote|Only transfers between accounts are detected, payroll deductions are not:Detected from transfers between your accounts. Payroll deductions like a 401(k) don't appear here.`;

  /**
   * #207: explains the "inferred" marker. Informational, not a warning — a 529
   * the aggregator doesn't carry is the normal case, not a degraded result.
   */
  protected readonly savingsInferredFootnote = $localize`:Savings section footnote|Explains the inferred marker on a savings row:Rows marked inferred were matched from the money leaving your account — the destination isn't synced.`;

  protected hasInferredContribution(contributions: SavingsContribution[]): boolean {
    return contributions.some((c) => c.inferred === true);
  }

  protected cadenceWord(frequency: RecurringFrequency): string {
    return FREQUENCY_LABELS[frequency] ?? frequency;
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
      contribution.occurrences === 1
        ? $localize`:Savings row evidence|The transfer was detected exactly once:seen 1 time`
        : $localize`:Savings row evidence|How many times the transfer was detected:seen ${contribution.occurrences}:count: times`;
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

  /**
   * #6: the savings rate as observed saving from three sources — declared
   * transfers, pre-tax payroll (401(k)/HSA), and residual (take-home left
   * unspent and unmoved) — instead of a single income-minus-spending figure.
   * Each source is shown when it carries a non-zero amount.
   */
  protected savingsSources(rate: SavingsRate): { label: string; amount: Money }[] {
    const sources: { label: string; amount: Money }[] = [];
    if (rate.transfers && rate.transfers.amount_minor !== 0) {
      sources.push({
        label: $localize`:Savings rate source|Money saved by moving it between accounts:transfers`,
        amount: rate.transfers,
      });
    }
    if (rate.payroll_deductions && rate.payroll_deductions.amount_minor !== 0) {
      sources.push({
        label: $localize`:Savings rate source|Money saved before pay lands, such as a 401k or HSA:payroll (401k/HSA)`,
        amount: rate.payroll_deductions,
      });
    }
    if (rate.residual && rate.residual.amount_minor !== 0) {
      sources.push({
        label: $localize`:Savings rate source|Take-home pay left unspent and unmoved:residual`,
        amount: rate.residual,
      });
    }
    return sources;
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

  protected readonly cadences = Object.keys(FREQUENCY_LABELS) as RecurringFrequency[];

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
        throw new Error(
          apiErrorMessage(error, $localize`:Error message|The account list could not be loaded:Failed to load accounts.`),
        );
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
      this.savingsError.set(
        apiErrorMessage(error, $localize`:Error message|A declared savings contribution could not be saved:Failed to save the contribution.`),
      );
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
      this.savingsError.set(
        apiErrorMessage(error, $localize`:Error message|A tracked savings contribution could not be removed:Failed to stop tracking that contribution.`),
      );
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
      this.savingsError.set(
        apiErrorMessage(error, $localize`:Error message|A detected transfer could not be dismissed:Failed to dismiss that transfer.`),
      );
      return;
    }
    this.household.reload();
  }

  // --- #4: linking contributions to the goals they fund ----------------------

  /** Goal names only matter once a row is linked or carries a suggestion. */
  private readonly needsGoalNames = computed(() => {
    const rows = this.household.value()?.savings_contributions ?? [];
    return rows.some((c) => c.goal_id || c.suggested_goal_id);
  });

  /**
   * The context exposes goal ids only (top_goal aside), so names are fetched
   * lazily via the goals list — and only when there is a link or suggestion
   * to label. An error degrades to no labels, never a broken overview.
   */
  protected readonly goalNames = resource({
    params: () => (this.needsGoalNames() ? true : undefined),
    loader: async () => {
      const { data, error } = await this.api.listGoals();
      if (error || !data) {
        return {} as Record<string, string>;
      }
      return Object.fromEntries(data.goals.map((goal) => [goal.id, goal.name]));
    },
  });

  /**
   * #10 phase 2: an interpolated `aria-label` cannot carry `i18n-aria-label` —
   * Angular applies translated attributes as DOM properties, which would leave
   * the button with no accessible name — so the label is built here instead.
   */
  protected unlinkGoalLabel(goalName: string): string {
    return $localize`:Savings row goal link|Screen-reader name for the button that unlinks a goal from a savings contribution:Unlink from ${goalName}:goal:`;
  }

  protected goalName(goalId: string | null | undefined): string | null {
    if (!goalId) {
      return null;
    }
    return this.goalNames.value()?.[goalId] ?? null;
  }

  /** One PATCH does both directions: a goal id links, null unlinks. */
  protected async linkContributionToGoal(
    contribution: SavingsContribution,
    goalId: string | null,
  ): Promise<void> {
    const contributionId = contribution.contribution_id;
    if (!contributionId || this.savingsSubmitting()) {
      return;
    }
    this.savingsSubmitting.set(true);
    this.savingsError.set(null);
    const { error } = await this.api.updateSavingsContribution(contributionId, goalId);
    this.savingsSubmitting.set(false);
    if (error) {
      this.savingsError.set(
        apiErrorMessage(
          error,
          goalId
            ? $localize`:Error message|A savings contribution could not be linked to a goal:Failed to link the goal.`
            : $localize`:Error message|A savings contribution could not be unlinked from a goal:Failed to unlink the goal.`,
        ),
      );
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

  // --- #10 phase 1: the household's answer language --------------------------

  /** A language's own name is never translated. */
  protected readonly languageOptions = [
    { value: 'en', label: 'English' },
    { value: 'vi', label: 'Tiếng Việt' },
    { value: 'lt', label: 'Lietuvių' },
  ] as const;

  protected readonly languageHint = $localize`:Household language hint|Explains what the language setting changes:The advisor answers in this language. Screens follow in a later update.`;

  /** Optimistic pick shown while saving; null means "use the context value". */
  protected readonly languageInput = signal<string | null>(null);
  protected readonly savingLanguage = signal(false);
  protected readonly languageError = signal<string | null>(null);

  protected languageValue(context: HouseholdContext): string {
    return this.languageInput() ?? context.language ?? 'en';
  }

  protected languageLabel(context: HouseholdContext): string {
    const value = this.languageValue(context);
    return this.languageOptions.find((option) => option.value === value)?.label ?? value;
  }

  protected async changeLanguage(language: string): Promise<void> {
    if (this.savingLanguage()) {
      return;
    }
    const previous = this.languageInput();
    this.languageInput.set(language);
    this.savingLanguage.set(true);
    this.languageError.set(null);
    const { error } = await this.api.updateHousehold({ language });
    this.savingLanguage.set(false);
    if (error) {
      // The server 422s an unsupported value; show its message and revert.
      this.languageError.set(
        apiErrorMessage(error, $localize`:Error message|The household answer language could not be changed:Failed to change the language.`),
      );
      this.languageInput.set(previous);
      return;
    }
    this.household.reload();
  }

  // --- #41: the zone this household reckons "today" in -----------------------
  // Same right as the language selector above — the server enforces
  // household.settings.manage on updateHousehold.

  protected readonly timezoneHint = TIMEZONE_HINT;

  /** No zone chosen yet: dates follow the box's own zone. */
  protected readonly timezoneUnset = $localize`:Household timezone unset|Shown in place of a zone when the household has never chosen one:Not set — the box's own zone`;

  /** Optimistic pick shown while saving; null means "use the context value". */
  protected readonly timezoneInput = signal<string | null>(null);
  protected readonly savingTimezone = signal(false);
  protected readonly timezoneError = signal<string | null>(null);

  /** The saved (or optimistically picked) zone; '' when none is set. */
  protected timezoneValue(context: HouseholdContext): string {
    return this.timezoneInput() ?? context.timezone ?? '';
  }

  /** The read-only line for members who cannot change it. */
  protected timezoneLabel(context: HouseholdContext): string {
    return this.timezoneValue(context) || this.timezoneUnset;
  }

  protected async changeTimezone(choice: string): Promise<void> {
    if (this.savingTimezone() || !choice) {
      return;
    }
    // #43: null on the column is the inherit state, and a null `timezone` in
    // the payload would read as "field omitted" — hence the separate flag.
    const clearing = choice === TIMEZONE_BOX_DEFAULT;
    const previous = this.timezoneInput();
    this.timezoneInput.set(clearing ? '' : choice);
    this.savingTimezone.set(true);
    this.timezoneError.set(null);
    const { error } = await this.api.updateHousehold(
      clearing ? { clear_timezone: true } : { timezone: choice },
    );
    this.savingTimezone.set(false);
    if (error) {
      // The server 422s a zone it doesn't know; show its message and revert.
      this.timezoneError.set(
        apiErrorMessage(error, $localize`:Error message|The household time zone could not be changed:Failed to change the time zone.`),
      );
      this.timezoneInput.set(previous);
      return;
    }
    // Every date on the page was computed in the old zone.
    this.household.reload();
  }

  // --- #5: reserve committed savings like a bill -----------------------------
  // The server enforces household.settings.manage on updateHousehold, exactly
  // like #10's language selector.

  /** Optimistic pick shown while saving; null means "use the context value". */
  protected readonly committedReserveInput = signal<boolean | null>(null);
  protected readonly savingCommittedReserve = signal(false);
  protected readonly committedReserveError = signal<string | null>(null);

  /**
   * The setting's state is read back from safe_to_spend.committed_savings_reserved
   * (the only place the context carries it). Defaults to off — shown beside
   * Safe to Spend, not subtracted.
   */
  protected committedReserveValue(context: HouseholdContext): boolean {
    return this.committedReserveInput() ?? context.safe_to_spend?.committed_savings_reserved ?? false;
  }

  protected async toggleCommittedReserve(reserve: boolean): Promise<void> {
    if (this.savingCommittedReserve()) {
      return;
    }
    const previous = this.committedReserveInput();
    this.committedReserveInput.set(reserve);
    this.savingCommittedReserve.set(true);
    this.committedReserveError.set(null);
    const { error } = await this.api.updateHousehold({ reserve_committed_savings: reserve });
    this.savingCommittedReserve.set(false);
    if (error) {
      this.committedReserveError.set(
        apiErrorMessage(error, $localize`:Error message|The committed savings setting could not be saved:Failed to update committed savings.`),
      );
      this.committedReserveInput.set(previous);
      return;
    }
    this.household.reload();
  }

  protected dueLabel(daysUntil: number): string {
    if (daysUntil <= 0) {
      return $localize`:Bill due label|The bill is due today:Due today`;
    }
    if (daysUntil === 1) {
      return $localize`:Bill due label|The bill is due tomorrow:Due tomorrow`;
    }
    return $localize`:Bill due label|How many days until the bill is due:Due in ${daysUntil}:days: days`;
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
