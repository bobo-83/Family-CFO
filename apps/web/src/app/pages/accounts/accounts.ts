import { Component, HostListener, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { DatePipe } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type { Account, AccountType, AccountUpdateRequest } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
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
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

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
        throw new Error(apiErrorMessage(error, 'Failed to load accounts.'));
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
        await this.scanStatementFile(file);
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
      this.submitError.set(apiErrorMessage(error, 'Statement scan failed.'));
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
      this.submitError.set(apiErrorMessage(created.error, 'Failed to create account.'));
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
      this.submitError.set(apiErrorMessage(error, 'Failed to update emergency fund.'));
      return;
    }
    this.accounts.reload();
  }

  /** M35: fix mistyped accounts (e.g. a synced 401k created as "checking"). */
  protected async retype(id: string, event: Event): Promise<void> {
    const type = (event.target as HTMLSelectElement).value as AccountType;
    const { error } = await this.api.updateAccount(id, { type });
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to change account type.'));
      return;
    }
    this.accounts.reload();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm('Delete this account? Accounts referenced by transactions cannot be deleted.')) {
      return;
    }
    const { error } = await this.api.deleteAccount(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete account.'));
      return;
    }
    this.accounts.reload();
  }
}
