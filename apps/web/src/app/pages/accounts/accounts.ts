import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { AccountType } from '../../api-client';
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
  imports: [ReactiveFormsModule],
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
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  });

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
