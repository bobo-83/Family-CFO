import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type {
  Bill as BillDto,
  BillSuggestion,
  BillUpdateSuggestion,
  RecurringFrequency,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

const FREQUENCIES: RecurringFrequency[] = [
  'weekly',
  'biweekly',
  'semimonthly',
  'monthly',
  'quarterly',
  'annual',
];

@Component({
  selector: 'app-bills',
  imports: [ReactiveFormsModule, DatePipe],
  templateUrl: './bills.html',
  styleUrl: './bills.scss',
})
export class Bills {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly frequencies = FREQUENCIES;
  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly bills = signal<BillDto[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  // M58: recurring charges detected in checking/credit-card transactions.
  protected readonly suggestions = signal<BillSuggestion[]>([]);
  // M59: existing bills whose live charge pattern drifted (price changes).
  protected readonly updates = signal<BillUpdateSuggestion[]>([]);
  protected readonly suggestionError = signal<string | null>(null);
  protected readonly suggestionBusy = signal<string | null>(null);

  constructor() {
    void this.load();
    void this.loadSuggestions();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const { data, error } = await this.api.listBills();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load bills.'));
      return;
    }
    this.bills.set(data.bills);
  }

  private async loadSuggestions(): Promise<void> {
    const { data, error } = await this.api.listBillSuggestions();
    if (error || !data) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to load suggestions.'));
      return;
    }
    this.suggestions.set(data.suggestions);
    this.updates.set(data.updates ?? []);
  }

  protected async applyUpdate(update: BillUpdateSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(update.dismiss_key);
    this.suggestionError.set(null);
    const { error } = await this.api.updateBill(update.bill_id, {
      amount: update.suggested_amount,
      frequency: update.frequency,
      next_due_date: update.next_due_date,
    });
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to update the bill.'));
      return;
    }
    await Promise.all([this.load(), this.loadSuggestions()]);
  }

  protected async dismissUpdate(update: BillUpdateSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(update.dismiss_key);
    this.suggestionError.set(null);
    const { error } = await this.api.dismissBillSuggestion(update.dismiss_key);
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to dismiss the update.'));
      return;
    }
    await this.loadSuggestions();
  }

  protected async confirmSuggestion(suggestion: BillSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(suggestion.merchant_key);
    this.suggestionError.set(null);
    const { error } = await this.api.createBill({
      name: suggestion.name,
      amount: suggestion.amount,
      frequency: suggestion.frequency,
      next_due_date: suggestion.next_due_date,
    });
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to create the bill.'));
      return;
    }
    await Promise.all([this.load(), this.loadSuggestions()]);
  }

  protected async dismissSuggestion(suggestion: BillSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(suggestion.merchant_key);
    this.suggestionError.set(null);
    const { error } = await this.api.dismissBillSuggestion(suggestion.merchant_key);
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to dismiss the suggestion.'));
      return;
    }
    await this.loadSuggestions();
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    amount: [0, [Validators.required, Validators.min(0.01)]],
    frequency: ['monthly' as RecurringFrequency, Validators.required],
    nextDueDate: [''],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.submitError.set(null);
    const { name, amount, frequency, nextDueDate } = this.form.getRawValue();
    const { error } = await this.api.createBill({
      name,
      amount: { amount_minor: Math.round(amount * 100), currency: 'USD' },
      frequency,
      ...(nextDueDate ? { next_due_date: nextDueDate } : {}),
    });
    this.submitting.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create bill.'));
      return;
    }
    this.form.reset({ name: '', amount: 0, frequency: 'monthly', nextDueDate: '' });
    await this.load();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm('Delete this bill?')) {
      return;
    }
    const { error } = await this.api.deleteBill(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete bill.'));
      return;
    }
    await this.load();
  }
}
