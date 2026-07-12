import { Component, inject, resource, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import type { GoalType } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

const GOAL_TYPES: GoalType[] = [
  'emergency_fund',
  'vacation',
  'retirement',
  'college',
  'vehicle',
  'renovation',
  'other',
];

@Component({
  selector: 'app-goals',
  imports: [ReactiveFormsModule],
  templateUrl: './goals.html',
  styleUrl: './goals.scss',
})
export class Goals {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);

  protected readonly goalTypes = GOAL_TYPES;

  // M75: human labels for goal types.
  protected goalTypeLabel(type: string): string {
    const labels: Record<string, string> = {
      emergency_fund: 'Emergency fund',
      vacation: 'Vacation',
      retirement: 'Retirement',
      college: 'College',
      vehicle: 'Vehicle',
      renovation: 'Renovation',
      other: 'Other',
    };
    return labels[type] ?? type;
  }
  protected readonly canCreateGoals = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly goals = resource({
    loader: async () => {
      const { data, error } = await this.api.listGoals();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load goals.'));
      }
      return data.goals;
    },
  });

  protected readonly formatMoney = formatMoney;

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    type: ['other' as GoalType, Validators.required],
    targetAmount: [0, [Validators.required, Validators.min(0.01)]],
    priority: [3, [Validators.required, Validators.min(1), Validators.max(5)]],
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

    const { name, type, targetAmount, priority } = this.form.getRawValue();
    const { error } = await this.api.createGoal({
      name,
      type,
      target: { amount_minor: Math.round(targetAmount * 100), currency: 'USD' },
      priority,
    });

    this.submitting.set(false);

    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create goal.'));
      return;
    }

    this.form.reset({ name: '', type: 'other', targetAmount: 0, priority: 3 });
    this.goals.reload();
  }
}
