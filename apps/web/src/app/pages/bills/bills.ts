import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { Bill as BillDto, RecurringFrequency } from '../../api-client';
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

  constructor() {
    void this.load();
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
