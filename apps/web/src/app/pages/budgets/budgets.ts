import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import type { Budget, Category } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-budgets',
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './budgets.html',
  styleUrl: './budgets.scss',
})
export class Budgets {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly budgets = signal<Budget[]>([]);
  protected readonly categories = signal<Category[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  /** Categories that don't have an envelope yet (create-form options). */
  protected readonly availableCategories = computed(() => {
    const used = new Set(this.budgets().map((b) => b.category_id));
    return this.categories().filter((c) => !used.has(c.id));
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const [budgets, categories] = await Promise.all([
      this.api.listBudgets(),
      this.api.listCategories(),
    ]);
    this.loading.set(false);
    if (budgets.error || !budgets.data) {
      this.loadError.set(apiErrorMessage(budgets.error, 'Failed to load budgets.'));
      return;
    }
    this.budgets.set(budgets.data.budgets);
    this.categories.set(categories.data?.categories ?? []);
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    categoryId: ['', Validators.required],
    limit: [0, [Validators.required, Validators.min(0.01)]],
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
    const { categoryId, limit } = this.form.getRawValue();
    const { error } = await this.api.createBudget({
      category_id: categoryId,
      limit: { amount_minor: Math.round(limit * 100), currency: 'USD' },
    });
    this.submitting.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create budget.'));
      return;
    }
    this.form.reset({ categoryId: '', limit: 0 });
    await this.load();
  }

  /** Inline limit edit: prompt-free number input committed on change. */
  protected async changeLimit(budget: Budget, event: Event): Promise<void> {
    const value = Number((event.target as HTMLInputElement).value);
    if (!Number.isFinite(value) || value <= 0) {
      return;
    }
    const { error } = await this.api.updateBudget(budget.id, {
      limit: { amount_minor: Math.round(value * 100), currency: budget.limit.currency },
    });
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to update the limit.'));
      return;
    }
    await this.load();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm('Delete this budget envelope?')) {
      return;
    }
    const { error } = await this.api.deleteBudget(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete budget.'));
      return;
    }
    await this.load();
  }

  protected barWidth(budget: Budget): number {
    return Math.min(100, budget.percent_used);
  }
}
