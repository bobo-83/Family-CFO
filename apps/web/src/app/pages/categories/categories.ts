import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { Category } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-categories',
  imports: [ReactiveFormsModule],
  templateUrl: './categories.html',
  styleUrl: './categories.scss',
})
export class Categories {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly canWrite = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly categories = signal<Category[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const { data, error } = await this.api.listCategories();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load categories.'));
      return;
    }
    this.categories.set(data.categories);
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
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
    const { name } = this.form.getRawValue();
    const { error } = await this.api.createCategory({ name });
    this.submitting.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create category.'));
      return;
    }
    this.form.reset({ name: '' });
    await this.load();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm('Delete this category? Transactions using it will become uncategorized.')) {
      return;
    }
    const { error } = await this.api.deleteCategory(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete category.'));
      return;
    }
    await this.load();
  }
}
