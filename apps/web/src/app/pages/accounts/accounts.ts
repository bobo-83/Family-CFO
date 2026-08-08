import { Component, HostListener, computed, inject, resource, signal } from '@angular/core';
import { ACCOUNT_TYPE_LABELS, labelFor } from '../../shared/enum-labels';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Router } from '@angular/router';
import type {
  Account,
  AccountType,
  AccountUpdateRequest,
  CardStatement,
  CardStatementCreateRequest,
  CardStatementScanLine,
  StatementLine,
  StatementLineInput,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { statementMatchLabel, statementMatchState } from '../../shared/enum-labels';
import { formatMoney } from '../../shared/format-money';

const ACCOUNT_TYPES: AccountType[] = [
  'checking',
  'savings',
  'credit_card',
  'brokerage',
  'retirement',
  'hsa',
  '529',
  'mortgage',
  'auto_loan',
  'student_loan',
  'real_estate',
  'other_asset',
  'other_liability',
];

@Component({
  selector: 'app-accounts',
  imports: [
    ReactiveFormsModule,
    DatePipe,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './accounts.html',
  styleUrl: './accounts.scss',
})
export class Accounts {
  /** #20: the wire code (`auto_loan`) is not a label a person reads. */
  protected accountTypeLabel(type: string): string {
    return labelFor(ACCOUNT_TYPE_LABELS, type);
  }

  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly router = inject(Router);

  protected readonly accountTypes = ACCOUNT_TYPES;
  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = computed(() => {
    return this.auth.hasRight('accounts.manage');
  });

  /** Spendability-oriented grouping (M33) — mirrors the advisor's categories. */
  private static readonly CATEGORY_ORDER = [
    'Cash',
    'Investments',
    'Retirement',
    'Education',
    'Property',
    'Debts',
  ] as const;

  private static categoryOf(type: AccountType): (typeof Accounts.CATEGORY_ORDER)[number] {
    switch (type) {
      case 'checking':
      case 'savings':
        return 'Cash';
      case 'brokerage':
        return 'Investments';
      case 'retirement':
      case 'hsa':
        return 'Retirement';
      case '529':
        return 'Education';
      case 'real_estate':
      case 'other_asset':
        return 'Property';
      default:
        return 'Debts';
    }
  }

  /** Display names for the spendability groups — the keys above stay English
   * because they key the grouping map and its ordering. */
  protected categoryLabel(category: string): string {
    const labels: Record<string, string> = {
      Cash: $localize`:Account group|Everyday spendable money:Cash`,
      Investments: $localize`:Account group|Taxable brokerage holdings:Investments`,
      Retirement: $localize`:Account group|Retirement and health savings accounts:Retirement`,
      Education: $localize`:Account group|College savings accounts:Education`,
      Property: $localize`:Account group|Property and other owned assets:Property`,
      Debts: $localize`:Account group|What the household owes:Debts`,
    };
    return labels[category] ?? category;
  }

  protected readonly groupedAccounts = computed(() => {
    const list = this.accounts.value() ?? [];
    const groups = new Map<string, Account[]>();
    for (const account of list) {
      const category = Accounts.categoryOf(account.type);
      groups.set(category, [...(groups.get(category) ?? []), account]);
    }
    return Accounts.CATEGORY_ORDER.filter((c) => groups.has(c)).map((category) => ({
      category,
      accounts: groups.get(category)!,
      total: Accounts.rollup(groups.get(category)!),
    }));
  });

  /** Per-group balance rollup (e.g. Debts = sum of all debt), split by currency. */
  private static rollup(accounts: Account[]): string {
    const byCurrency = new Map<string, number>();
    for (const account of accounts) {
      const { amount_minor, currency } = account.balance;
      byCurrency.set(currency, (byCurrency.get(currency) ?? 0) + amount_minor);
    }
    return [...byCurrency.entries()]
      .map(([currency, amount_minor]) => formatMoney({ amount_minor, currency }))
      .join(' + ');
  }

  protected readonly accounts = resource({
    loader: async () => {
      const { data, error } = await this.api.listAccounts();
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load accounts.`));
      }
      return data.accounts;
    },
  });

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    type: ['checking' as AccountType, Validators.required],
    currency: ['USD', [Validators.required, Validators.minLength(3), Validators.maxLength(3)]],
    openingBalance: [0],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  // ADR 0057: paste (Ctrl/⌘+V) or pick a statement photo/PDF — the on-box
  // vision model prefills the add-account form; the user confirms and saves.
  protected readonly scanning = signal(false);
  protected readonly scanNote = signal<string | null>(null);

  protected async onStatementSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    await this.scanStatementFile(file);
  }

  /** ADR 0028: every statement input accepts paste. */
  @HostListener('window:paste', ['$event'])
  async onPaste(event: ClipboardEvent): Promise<void> {
    if (!this.canWrite()) {
      return;
    }
    const items = event.clipboardData?.items ?? [];
    for (const item of Array.from(items)) {
      if (item.kind !== 'file') {
        continue;
      }
      const file = item.getAsFile();
      if (file && /^(image\/|application\/pdf)/.test(file.type)) {
        event.preventDefault();
        // An open card panel owns the paste — otherwise it feeds the new-account form.
        if (this.openStatementsFor()) {
          await this.scanCardStatementFile(file);
        } else {
          await this.scanStatementFile(file);
        }
        return;
      }
    }
  }

  protected async scanStatementFile(file: File | undefined | null): Promise<void> {
    if (!file || this.scanning()) {
      return;
    }
    this.scanning.set(true);
    this.scanNote.set(null);
    this.submitError.set(null);
    const dataUrl: string = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const [meta, base64] = dataUrl.split(',', 2);
    const mediaType = /data:([^;]+)/.exec(meta)?.[1] ?? 'image/jpeg';
    const { data, error } = await this.api.scanAccountStatement(base64, mediaType);
    this.scanning.set(false);
    if (error || !data) {
      this.submitError.set(apiErrorMessage(error, $localize`Statement scan failed.`));
      return;
    }
    // Prefill only — the user confirms every value before saving.
    if (data.name) this.form.controls.name.setValue(data.name);
    if (data.account_type) this.form.controls.type.setValue(data.account_type);
    if (data.balance_minor) this.form.controls.openingBalance.setValue(data.balance_minor / 100);
    this.scanNote.set(data.note);
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.submitError.set(null);
    const { name, type, currency, openingBalance } = this.form.getRawValue();
    const created = await this.api.createAccount({ name, type, currency });
    if (created.error || !created.data) {
      this.submitting.set(false);
      this.submitError.set(apiErrorMessage(created.error, $localize`Failed to create account.`));
      return;
    }
    if (openingBalance !== 0) {
      await this.api.recordAccountBalance(
        created.data.id,
        Math.round(openingBalance * 100),
        currency,
      );
    }
    this.submitting.set(false);
    this.form.reset({ name: '', type: 'checking', currency: 'USD', openingBalance: 0 });
    this.accounts.reload();
  }

  /** M36: total money reserved for emergencies across all accounts. */
  protected readonly emergencyFundTotal = computed(() => {
    const list = this.accounts.value() ?? [];
    let minor = 0;
    let currency: string | null = null;
    for (const account of list) {
      if (account.emergency_fund_reserved) {
        minor += account.emergency_fund_reserved.amount_minor;
        currency ??= account.emergency_fund_reserved.currency;
      }
    }
    return currency ? formatMoney({ amount_minor: minor, currency }) : null;
  });

  protected efModeOf(account: Account): 'none' | 'percent' | 'amount' {
    if (account.emergency_fund_percent != null) return 'percent';
    if (account.emergency_fund_amount) return 'amount';
    return 'none';
  }

  protected efValueOf(account: Account): string {
    if (account.emergency_fund_percent != null) return String(account.emergency_fund_percent);
    if (account.emergency_fund_amount) {
      return (account.emergency_fund_amount.amount_minor / 100).toFixed(2);
    }
    return '';
  }

  /** M36: designate (or clear) this account's emergency-fund share. */
  protected async setEmergencyFund(account: Account, mode: string, raw: string): Promise<void> {
    let body: AccountUpdateRequest;
    if (mode === 'none') {
      if (this.efModeOf(account) === 'none') return;
      body = { clear_emergency_fund: true };
    } else {
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0 || raw.trim() === '') return;
      body =
        mode === 'percent'
          ? { emergency_fund_percent: Math.min(value, 100) }
          : {
              emergency_fund_amount: {
                amount_minor: Math.round(value * 100),
                currency: account.balance.currency,
              },
            };
    }
    const { error } = await this.api.updateAccount(account.id, body);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to update emergency fund.`));
      return;
    }
    this.accounts.reload();
  }

  /** Tag/untag this account's balance as vested RSUs ready to sell. */
  protected async setRsuReadyToSell(account: Account, event: Event): Promise<void> {
    const checked = (event.target as HTMLInputElement).checked;
    if (checked === (account.rsu_ready_to_sell ?? false)) {
      return;
    }
    // Send only this field — a bare boolean can never clear another account's tag.
    const { error } = await this.api.updateAccount(account.id, { rsu_ready_to_sell: checked });
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to update the RSU tag.`));
      return;
    }
    this.accounts.reload();
  }

  /** M35: fix mistyped accounts (e.g. a synced 401k created as "checking"). */
  protected async retype(id: string, event: Event): Promise<void> {
    const type = (event.target as HTMLSelectElement).value as AccountType;
    const { error } = await this.api.updateAccount(id, { type });
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to change account type.`));
      return;
    }
    this.accounts.reload();
  }

  protected async remove(id: string): Promise<void> {
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before an account is deleted:Delete this account? Accounts referenced by transactions cannot be deleted.`,
      )
    ) {
      return;
    }
    const { error } = await this.api.deleteAccount(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to delete account.`));
      return;
    }
    if (this.openStatementsFor() === id) {
      this.openStatementsFor.set(undefined);
    }
    this.accounts.reload();
  }

  // --- #11: credit-card statements -----------------------------------------
  // A synced card balance is a moving target; the statement is the EXACT sum
  // the issuer wants, by a date they set. Recording it here is what lets the
  // payment timeline stop guessing.

  /** Which card's statement panel is open (undefined = none, resource idle). */
  protected readonly openStatementsFor = signal<string | undefined>(undefined);

  protected readonly cardStatements = resource({
    params: () => this.openStatementsFor(),
    loader: async ({ params }) => {
      const { data, error } = await this.api.listCardStatements(params);
      if (error) {
        throw new Error(apiErrorMessage(error, $localize`Failed to load card statements.`));
      }
      return data?.statements ?? [];
    },
  });

  protected readonly statementForm = this.formBuilder.nonNullable.group({
    statementBalance: [null as number | null, Validators.required],
    dueDate: ['', Validators.required],
    minimumDue: [null as number | null],
    periodStart: [''],
    periodEnd: [''],
  });

  protected readonly statementSubmitting = signal(false);
  protected readonly statementScanning = signal(false);
  protected readonly statementScanNote = signal<string | null>(null);

  protected isCreditCard(account: Account): boolean {
    return account.type === 'credit_card';
  }

  protected toggleStatements(accountId: string): void {
    const next = this.openStatementsFor() === accountId ? undefined : accountId;
    this.openStatementsFor.set(next);
    this.resetStatementForm();
    // Leaving the panel abandons an unstored scan rather than letting it follow
    // the household to another card.
    this.scannedLines.set([]);
    this.scannedLinesFor.set(undefined);
    this.reconcilingStatement.set(undefined);
  }

  private resetStatementForm(): void {
    this.statementForm.reset({
      statementBalance: null,
      dueDate: '',
      minimumDue: null,
      periodStart: '',
      periodEnd: '',
    });
    this.statementScanNote.set(null);
  }

  protected async onCardStatementSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    await this.scanCardStatementFile(file);
  }

  /** Candidates only — the scan never saves; the user confirms in the form. */
  protected async scanCardStatementFile(file: File | undefined | null): Promise<void> {
    if (!file || this.statementScanning()) {
      return;
    }
    this.statementScanning.set(true);
    this.statementScanNote.set(null);
    this.submitError.set(null);
    const dataUrl: string = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const [meta, base64] = dataUrl.split(',', 2);
    const mediaType = /data:([^;]+)/.exec(meta)?.[1] ?? 'image/jpeg';
    const { data, error } = await this.api.scanCardStatement(base64, mediaType);
    this.statementScanning.set(false);
    if (error || !data) {
      // A 503 here means no vision model is serving — the server says so.
      this.submitError.set(apiErrorMessage(error, $localize`Statement scan failed.`));
      return;
    }
    // Prefill only — nothing is saved until the user presses “Save statement”.
    if (data.statement_balance_minor != null) {
      this.statementForm.controls.statementBalance.setValue(data.statement_balance_minor / 100);
    }
    if (data.minimum_due_minor != null) {
      this.statementForm.controls.minimumDue.setValue(data.minimum_due_minor / 100);
    }
    if (data.due_date) this.statementForm.controls.dueDate.setValue(data.due_date);
    if (data.period_start) this.statementForm.controls.periodStart.setValue(data.period_start);
    if (data.period_end) this.statementForm.controls.periodEnd.setValue(data.period_end);
    // #25: the transaction table rides along. It is held here, unsaved, until
    // the statement it belongs to exists and the household says to store it.
    this.scannedLines.set((data.lines ?? []).filter((line) => !!line.occurred_on));
    this.scannedLinesFor.set(undefined);
    this.statementScanNote.set(data.note);
  }

  protected async submitStatement(account: Account): Promise<void> {
    if (this.statementForm.invalid || this.statementSubmitting()) {
      this.statementForm.markAllAsTouched();
      return;
    }
    const { statementBalance, dueDate, minimumDue, periodStart, periodEnd } =
      this.statementForm.getRawValue();
    if (statementBalance == null || !dueDate) {
      this.statementForm.markAllAsTouched();
      return;
    }
    const currency = account.balance.currency;
    const body: CardStatementCreateRequest = {
      account_id: account.id,
      statement_balance: { amount_minor: Math.round(statementBalance * 100), currency },
      due_date: dueDate,
    };
    if (minimumDue != null) {
      body.minimum_due = { amount_minor: Math.round(minimumDue * 100), currency };
    }
    if (periodStart) body.period_start = periodStart;
    if (periodEnd) body.period_end = periodEnd;

    this.statementSubmitting.set(true);
    this.submitError.set(null);
    // Same card + due date updates that cycle rather than stacking a duplicate.
    const { data, error } = await this.api.recordCardStatement(body);
    this.statementSubmitting.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to record the statement.`));
      return;
    }
    // #25: line items need a statement to hang off, so the offer to store them
    // only appears once that statement exists.
    if (data && this.scannedLines().length > 0) {
      this.scannedLinesFor.set(data.id);
    }
    this.resetStatementForm();
    this.cardStatements.reload();
  }

  /** Today as "yyyy-MM-dd" in the member's own timezone, not UTC. */
  private static today(): string {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }

  /** Mark this cycle settled, or clear the mark if it is already set. */
  protected async toggleStatementPaid(statement: CardStatement): Promise<void> {
    const paidAt = statement.paid_at ? null : Accounts.today();
    this.submitError.set(null);
    const { error } = await this.api.markCardStatementPaid(statement.id, paidAt);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to update the statement.`));
      return;
    }
    this.cardStatements.reload();
  }

  protected async removeStatement(statementId: string): Promise<void> {
    if (
      !confirm(
        $localize`:Confirmation|Browser confirm before a recorded card statement is deleted:Delete this statement? The card falls back to an estimated amount due.`,
      )
    ) {
      return;
    }
    this.submitError.set(null);
    const { error } = await this.api.deleteCardStatement(statementId);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to delete the statement.`));
      return;
    }
    if (this.reconcilingStatement() === statementId) {
      this.reconcilingStatement.set(undefined);
    }
    this.cardStatements.reload();
  }

  // --- #25: reconciliation — does the synced ledger match the statement? ------
  // The balance says what is owed; the LINE ITEMS say whether what the household
  // is looking at is complete. A line no synced transaction explains is a charge
  // the bank feed never delivered, so the unmatched ones lead.

  protected readonly statementMatchState = statementMatchState;
  protected readonly statementMatchLabel = statementMatchLabel;

  /** Which statement's reconciliation is open (undefined = none, resource idle). */
  protected readonly reconcilingStatement = signal<string | undefined>(undefined);

  /** Line items the last scan read, held unsaved until the household stores them. */
  protected readonly scannedLines = signal<CardStatementScanLine[]>([]);
  /** The recorded statement those scanned lines can be stored against. */
  protected readonly scannedLinesFor = signal<string | undefined>(undefined);
  protected readonly storingLines = signal(false);

  /** Reading a reconciliation needs no write right — anyone may see the gaps. */
  protected readonly canAddTransactions = computed(() => {
    return this.auth.hasRight('transactions.manage');
  });

  protected readonly reconciliation = resource({
    params: () => this.reconcilingStatement(),
    loader: async ({ params }) => {
      const { data, error } = await this.api.getStatementReconciliation(params);
      if (error) {
        throw new Error(
          apiErrorMessage(error, $localize`Failed to load the statement reconciliation.`),
        );
      }
      return data;
    },
  });

  /** The counts the coverage line is built from. */
  protected readonly coverage = computed(() => {
    const found = this.reconciliation.value();
    const matched = found?.matched_count ?? 0;
    const missing = found?.missing_from_sync_count ?? 0;
    return {
      matched,
      missing,
      // Every stored line is either matched or missing, so the pair is the whole
      // statement — no separate total is needed from the server.
      total: matched + missing,
      differs: found?.amount_differs_count ?? 0,
      notOnStatement: found?.not_on_statement_count ?? 0,
    };
  });

  /**
   * Unmatched first, then near-misses, then the matched ones. A gap in the feed
   * is the reason to open this view, so it must never be buried under 140 rows
   * that are fine.
   */
  protected readonly reconciledLines = computed(() => {
    const rank = { missing: 0, amount_differs: 1, matched: 2 };
    return [...(this.reconciliation.value()?.lines ?? [])].sort((a, b) => {
      const byState = rank[statementMatchState(a)] - rank[statementMatchState(b)];
      return byState !== 0 ? byState : a.occurred_on.localeCompare(b.occurred_on);
    });
  });

  protected toggleReconciliation(statementId: string): void {
    this.reconcilingStatement.set(
      this.reconcilingStatement() === statementId ? undefined : statementId,
    );
  }

  /** Store the scanned table against the statement it was read from. */
  protected async storeScannedLines(account: Account): Promise<void> {
    const statementId = this.scannedLinesFor();
    const scanned = this.scannedLines();
    if (!statementId || scanned.length === 0 || this.storingLines()) {
      return;
    }
    const currency = account.balance.currency;
    const lines: StatementLineInput[] = scanned
      .filter((line) => !!line.occurred_on)
      .map((line) => ({
        occurred_on: line.occurred_on as string,
        description: line.description,
        // Already in the ledger's convention: negative is a charge.
        amount: { amount_minor: line.amount_minor, currency },
      }));

    this.storingLines.set(true);
    this.submitError.set(null);
    const { error } = await this.api.replaceStatementLines(statementId, lines);
    this.storingLines.set(false);
    if (error) {
      this.submitError.set(
        apiErrorMessage(error, $localize`Failed to store the statement line items.`),
      );
      return;
    }
    this.scannedLines.set([]);
    this.scannedLinesFor.set(undefined);
    this.reconcilingStatement.set(statementId);
    this.reconciliation.reload();
  }

  protected discardScannedLines(): void {
    this.scannedLines.set([]);
    this.scannedLinesFor.set(undefined);
  }

  /**
   * PREFILL ONLY. This navigates to the add-transaction form with the line's
   * values filled in — the household still presses Add there. Reconciliation
   * never writes to the ledger, because a statement read by a vision model is
   * not evidence enough to create money movement on its own.
   */
  protected prefillTransactionFromLine(account: Account, line: StatementLine): void {
    void this.router.navigate(['/transactions'], {
      queryParams: {
        account: account.id,
        date: line.occurred_on,
        amount: (line.amount.amount_minor / 100).toFixed(2),
        merchant: line.description,
      },
    });
  }
}
