import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { Component, HostListener, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatRadioModule } from '@angular/material/radio';
import { MatSelectModule } from '@angular/material/select';
import type { Account, AccountType } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

/**
 * Debts & loans (M116): the dashboard counterpart of the iOS loan editor
 * (ADR 0025 parity) — list, add, edit, delete loan accounts; scan a statement
 * (file picker or paste, ADR 0028); enter the loan's end as a date OR as
 * "N payments remaining" (M115), both storing the maturity date.
 */

// The liability account types offered as "loans" — credit cards are handled by
// the pay-in-full setting, same split as iOS.
const LOAN_TYPES: { value: AccountType; label: string }[] = [
  { value: 'mortgage', label: 'Mortgage' },
  { value: 'auto_loan', label: 'Auto loan / lease' },
  { value: 'student_loan', label: 'Student loan' },
  { value: '401k_loan', label: '401(k) loan' },
  { value: 'other_liability', label: 'Other' },
];
const LOAN_TYPE_VALUES = new Set(LOAN_TYPES.map((t) => t.value));

interface LoanForm {
  name: string;
  type: AccountType;
  balanceOwed: number | null; // major units, positive
  monthlyPayment: number | null;
  apr: number | null;
  endMode: 'none' | 'date' | 'payments';
  maturityDate: string; // "yyyy-MM-dd" for the date input
  paymentsLeft: number | null;
  nextPaymentDueDate: string; // "yyyy-MM-dd"; the next payment due date (ADR 0033)
}

function emptyForm(): LoanForm {
  return {
    name: '',
    type: 'mortgage',
    balanceOwed: null,
    monthlyPayment: null,
    apr: null,
    endMode: 'none',
    maturityDate: '',
    paymentsLeft: null,
    nextPaymentDueDate: '',
  };
}

/** N monthly payments from today, as "yyyy-MM-dd" (M115's dateAfter). */
export function dateAfterPayments(payments: number, from = new Date()): string {
  const d = new Date(from.getFullYear(), from.getMonth() + Math.max(payments, 0), from.getDate());
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Whole months from today to an ISO date, rounded up (≈ payments remaining). */
export function monthsLeft(iso: string | null | undefined): number | null {
  if (!iso) {
    return null;
  }
  const target = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(target.getTime())) {
    return null;
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let months =
    (target.getFullYear() - today.getFullYear()) * 12 + (target.getMonth() - today.getMonth());
  if (target.getDate() > today.getDate()) {
    months += 1;
  }
  return Math.max(months, 0);
}

@Component({
  selector: 'app-loans',
  imports: [
    DatePipe,
    FormsModule,
    NgTemplateOutlet,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatRadioModule,
    MatButtonModule,
  ],
  templateUrl: './loans.html',
  styleUrl: './loans.scss',
})
export class Loans {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly formatMoney = formatMoney;
  protected readonly loanTypes = LOAN_TYPES;
  protected readonly monthsLeft = monthsLeft;
  protected readonly dateAfterPayments = dateAfterPayments;
  protected readonly canWrite = () => {
    return this.auth.hasRight('accounts.manage');
  };

  protected readonly loans = signal<Account[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);
  protected readonly saving = signal(false);
  protected readonly scanning = signal(false);
  protected readonly scanNote = signal<string | null>(null);

  /** null = closed; '' = adding; an id = editing that loan. */
  protected readonly editingId = signal<string | null>(null);
  protected form: LoanForm = emptyForm();

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const { data, error } = await this.api.listAccounts();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load loans.'));
      return;
    }
    this.loans.set(data.accounts.filter((a) => LOAN_TYPE_VALUES.has(a.type)));
  }

  protected typeLabel(type: AccountType): string {
    return LOAN_TYPES.find((t) => t.value === type)?.label ?? type;
  }

  protected owed(loan: Account): number {
    return Math.max(0, -loan.balance.amount_minor);
  }

  protected totalOwed(): number {
    // 401(k) loans are owed to yourself — excluded, matching iOS.
    return this.loans()
      .filter((l) => l.type !== '401k_loan')
      .reduce((sum, l) => sum + this.owed(l), 0);
  }

  protected totalMonthly(): number {
    return this.loans()
      .filter((l) => l.type !== '401k_loan')
      .reduce((sum, l) => sum + (l.minimum_payment?.amount_minor ?? 0), 0);
  }

  protected startAdd(): void {
    this.form = emptyForm();
    this.scanNote.set(null);
    this.actionError.set(null);
    this.editingId.set('');
  }

  protected startEdit(loan: Account): void {
    const maturity = loan.maturity_date ?? '';
    this.form = {
      name: loan.name,
      type: loan.type,
      balanceOwed: this.owed(loan) / 100,
      monthlyPayment: (loan.minimum_payment?.amount_minor ?? 0) / 100,
      apr: loan.annual_interest_rate ?? null,
      endMode: maturity ? 'date' : 'none',
      maturityDate: maturity,
      paymentsLeft: monthsLeft(maturity),
      nextPaymentDueDate: loan.next_payment_due_date ?? '',
    };
    this.scanNote.set(null);
    this.actionError.set(null);
    this.editingId.set(loan.id);
  }

  protected cancelEdit(): void {
    this.editingId.set(null);
  }

  /** Switching entry mode carries the value over — never loses what was typed. */
  protected onEndModeChange(mode: LoanForm['endMode']): void {
    if (mode === 'payments' && this.form.maturityDate) {
      this.form.paymentsLeft = monthsLeft(this.form.maturityDate);
    } else if (mode === 'date' && this.form.paymentsLeft && this.form.paymentsLeft > 0) {
      this.form.maturityDate = dateAfterPayments(this.form.paymentsLeft);
    }
  }

  /** The maturity the active entry mode implies, or null for "no end". */
  protected effectiveMaturity(): string | null {
    if (this.form.endMode === 'date') {
      return this.form.maturityDate || null;
    }
    if (this.form.endMode === 'payments' && this.form.paymentsLeft && this.form.paymentsLeft > 0) {
      return dateAfterPayments(this.form.paymentsLeft);
    }
    return null;
  }

  protected async save(): Promise<void> {
    const name = this.form.name.trim();
    if (!name || this.saving()) {
      return;
    }
    this.saving.set(true);
    this.actionError.set(null);
    const id = this.editingId();
    const currency = 'USD';
    const minor = (v: number | null) => Math.round((v ?? 0) * 100);
    const maturity = this.effectiveMaturity();
    const body = {
      name,
      type: this.form.type,
      // Always send a rate (0 when unknown): both terms present keeps the
      // payment counted as committed in safe-to-spend — same rule as iOS.
      annual_interest_rate: this.form.apr ?? 0,
      minimum_payment: { amount_minor: minor(this.form.monthlyPayment), currency },
      ...(maturity ? { maturity_date: maturity } : {}),
      ...(this.form.nextPaymentDueDate
        ? { next_payment_due_date: this.form.nextPaymentDueDate }
        : {}),
    };
    const saved = id
      ? await this.api.updateAccount(id, body)
      : await this.api.createAccount({ ...body, currency });
    if (saved.error || !saved.data) {
      this.saving.set(false);
      this.actionError.set(apiErrorMessage(saved.error, 'Failed to save the loan.'));
      return;
    }
    // A liability carries a NEGATIVE balance — the amount owed.
    const { error } = await this.api.recordAccountBalance(
      saved.data.id,
      -minor(this.form.balanceOwed),
      currency,
    );
    this.saving.set(false);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Saved, but recording the balance failed.'));
      return;
    }
    this.editingId.set(null);
    await this.load();
  }

  protected async remove(loan: Account): Promise<void> {
    if (!confirm(`Delete ${loan.name}?`)) {
      return;
    }
    const { error } = await this.api.deleteAccount(loan.id);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete the loan.'));
      return;
    }
    this.editingId.set(null);
    await this.load();
  }

  // --- Statement scan: file picker or paste (ADR 0028) ---

  protected async onStatementSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    await this.scanStatementFile(file);
  }

  @HostListener('window:paste', ['$event'])
  async onPaste(event: ClipboardEvent): Promise<void> {
    // Paste feeds the scan only while the form is open for a writer.
    if (!this.canWrite() || this.editingId() === null) {
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
    this.actionError.set(null);
    const dataUrl: string = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const [meta, base64] = dataUrl.split(',', 2);
    const mediaType = /data:([^;]+)/.exec(meta)?.[1] ?? 'image/jpeg';
    const { data, error } = await this.api.scanLoanStatement(base64, mediaType);
    this.scanning.set(false);
    if (error || !data) {
      this.actionError.set(apiErrorMessage(error, 'Statement scan failed.'));
      return;
    }
    // Prefill only — never overwrite what the user already typed.
    if (data.monthly_payment_minor) this.form.monthlyPayment = data.monthly_payment_minor / 100;
    if (data.balance_minor) this.form.balanceOwed = data.balance_minor / 100;
    if (data.apr_percent) this.form.apr = data.apr_percent;
    if (data.name && !this.form.name.trim()) this.form.name = data.name;
    if (data.maturity_date) {
      this.form.maturityDate = data.maturity_date;
      this.form.endMode = 'date'; // the scan read a concrete date; show it
      this.form.paymentsLeft = monthsLeft(data.maturity_date);
    } else if (data.payments_remaining) {
      this.form.paymentsLeft = data.payments_remaining;
      this.form.endMode = 'payments';
    }
    if (data.next_payment_due_date) this.form.nextPaymentDueDate = data.next_payment_due_date;
    if (data.is_lease && this.form.type === 'mortgage') this.form.type = 'auto_loan';
    this.scanNote.set(data.note);
  }
}
