import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-transactions',
  imports: [ReactiveFormsModule],
  templateUrl: './transactions.html',
  styleUrl: './transactions.scss',
})
export class Transactions {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

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

  protected readonly transactions = resource({
    loader: async () => {
      const { data, error } = await this.api.listTransactions();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load transactions.'));
      }
      return data.transactions;
    },
  });

  protected readonly form = this.formBuilder.nonNullable.group({
    accountId: ['', Validators.required],
    occurredAt: ['', Validators.required],
    amount: [0, [Validators.required]],
    merchant: [''],
    description: [''],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    const account = this.accounts.value()?.find((a) => a.id === this.form.getRawValue().accountId);
    if (!account) {
      this.submitError.set('Select an account first.');
      return;
    }

    this.submitting.set(true);
    this.submitError.set(null);
    const { accountId, occurredAt, amount, merchant, description } = this.form.getRawValue();
    const { error } = await this.api.createTransaction({
      account_id: accountId,
      occurred_at: occurredAt,
      amount: { amount_minor: Math.round(amount * 100), currency: account.balance.currency },
      merchant: merchant || undefined,
      description: description || undefined,
    });
    this.submitting.set(false);

    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create transaction.'));
      return;
    }
    this.form.reset({ accountId: '', occurredAt: '', amount: 0, merchant: '', description: '' });
    this.transactions.reload();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm('Delete this transaction? This cannot be undone.')) {
      return;
    }
    const { error } = await this.api.deleteTransaction(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete transaction.'));
      return;
    }
    this.transactions.reload();
  }
}
