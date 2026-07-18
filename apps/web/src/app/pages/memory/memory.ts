import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { Memory as MemoryDto } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';

@Component({
  selector: 'app-memory',
  imports: [ReactiveFormsModule, DatePipe],
  templateUrl: './memory.html',
  styleUrl: './memory.scss',
})
export class Memory {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly canWrite = () => {
    return this.auth.hasRight('advisor.manage');
  };

  protected readonly memories = signal<MemoryDto[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const { data, error } = await this.api.listMemories();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load memories.'));
      return;
    }
    this.memories.set(data.memories);
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    value: ['', [Validators.required, Validators.maxLength(500)]],
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
    const { value } = this.form.getRawValue();
    const { error } = await this.api.createMemory({ value: value.trim() });
    this.submitting.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to save the fact.'));
      return;
    }
    this.form.reset({ value: '' });
    await this.load();
  }

  protected async forget(id: string): Promise<void> {
    if (!confirm('Forget this fact? The advisor will no longer know it.')) {
      return;
    }
    const { error } = await this.api.deleteMemory(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete the memory.'));
      return;
    }
    await this.load();
  }
}
