import { Component, computed, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-transactions',
  imports: [
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './transactions.html',
  styleUrl: './transactions.scss',
})
export class Transactions {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = computed(() => {
    return this.auth.hasRight('transactions.manage');
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

  protected readonly categories = resource({
    loader: async () => {
      const { data, error } = await this.api.listCategories();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load categories.'));
      }
      return data.categories;
    },
  });

  protected readonly form = this.formBuilder.nonNullable.group({
    accountId: ['', Validators.required],
    occurredAt: ['', Validators.required],
    amount: [0, [Validators.required]],
    merchant: [''],
    description: [''],
    categoryId: [''],
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
    const { accountId, occurredAt, amount, merchant, description, categoryId } =
      this.form.getRawValue();
    const { error } = await this.api.createTransaction({
      account_id: accountId,
      occurred_at: occurredAt,
      amount: { amount_minor: Math.round(amount * 100), currency: account.balance.currency },
      merchant: merchant || undefined,
      description: description || undefined,
      category_id: categoryId || undefined,
    });
    this.submitting.set(false);

    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create transaction.'));
      return;
    }
    this.form.reset({
      accountId: '',
      occurredAt: '',
      amount: 0,
      merchant: '',
      description: '',
      categoryId: '',
    });
    this.transactions.reload();
  }

  /** M45: assign or clear a transaction's category inline. */
  protected async setCategory(id: string, value: string): Promise<void> {
    const { error } = await this.api.updateTransaction(
      id,
      value ? { category_id: value } : { clear_category: true },
    );
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to update category.'));
      return;
    }
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
