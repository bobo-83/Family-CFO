import { formatDate } from '@angular/common';
import { Component, LOCALE_ID, inject, resource, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type { Goal, GoalFundingSource, GoalType, RecurringFrequency } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

// #201's cadence words, so a funding row reads "College 529 · USD 500.00 monthly".
const CADENCE_WORDS: Record<RecurringFrequency, string> = {
  weekly: 'weekly',
  biweekly: 'every two weeks',
  semimonthly: 'twice a month',
  monthly: 'monthly',
  quarterly: 'quarterly',
  semiannual: 'twice a year',
  annual: 'yearly',
};

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
  imports: [
    FormsModule,
    ReactiveFormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './goals.html',
  styleUrl: './goals.scss',
})
export class Goals {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly formBuilder = inject(FormBuilder);
  private readonly locale = inject(LOCALE_ID);

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
    return this.auth.hasRight('goals.manage');
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

  // --- #4: what the ledger shows filling each goal ---------------------------

  /**
   * One line per goal on where its money is (or isn't) coming from.
   * "Unfunded" means no linked transfers, not no money — a 401(k) is withheld
   * before pay ever lands in the feed, so retirement goals get the payroll
   * caveat instead of the loud "nothing is funding this".
   */
  protected fundingLine(goal: Goal): string | null {
    const funding = goal.funding;
    if (!funding) {
      return null;
    }
    const monthly = `${formatMoney(funding.monthly_equivalent)}/mo`;
    switch (funding.status) {
      case 'on_track': {
        const projected = funding.projected_completion
          ? ` · projected ${this.monthLabel(funding.projected_completion)}`
          : '';
        return `On track — ${monthly} going in${projected}`;
      }
      case 'behind': {
        const target = goal.target_date ? ` by ${this.monthLabel(goal.target_date)}` : '';
        const projected = funding.projected_completion
          ? ` (projected ${this.monthLabel(funding.projected_completion)})`
          : '';
        return `Behind — ${monthly} won't reach the target${target}${projected}`;
      }
      case 'funded_no_date':
        return `${monthly} going in`;
      case 'unfunded':
        return goal.type === 'retirement'
          ? "No linked transfers — 401(k) payroll deductions don't appear here."
          : 'Nothing is currently funding this goal';
      default:
        return null;
    }
  }

  /** Compact "College 529 · USD 500.00 monthly" row under the funding line. */
  protected fundingSourceLine(source: GoalFundingSource): string {
    const cadence = CADENCE_WORDS[source.frequency] ?? source.frequency;
    return `${source.destination_name} · ${formatMoney(source.amount)} ${cadence}`;
  }

  private monthLabel(date: string): string {
    return formatDate(date, 'MMM y', this.locale);
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    type: ['other' as GoalType, Validators.required],
    targetAmount: [0, [Validators.required, Validators.min(0.01)]],
    priority: [3, [Validators.required, Validators.min(1), Validators.max(5)]],
    // M118: planned monthly contribution — 0 = no plan declared.
    monthlyContribution: [0, [Validators.min(0)]],
  });

  // M118: per-goal inline edit of the planned monthly contribution.
  protected readonly editingContributionId = signal<string | null>(null);
  protected contributionInput: number | null = null;
  protected readonly savingContribution = signal(false);

  protected async removeGoal(goalId: string, name: string): Promise<void> {
    if (!confirm(`Delete the goal "${name}"?`)) {
      return;
    }
    const { error } = await this.api.deleteGoal(goalId);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to delete the goal.'));
      return;
    }
    this.goals.reload();
  }

  protected startEditContribution(goalId: string, currentMinor: number | null): void {
    this.contributionInput = currentMinor != null ? currentMinor / 100 : null;
    this.editingContributionId.set(goalId);
  }

  protected async saveContribution(goalId: string): Promise<void> {
    if (this.savingContribution()) {
      return;
    }
    this.savingContribution.set(true);
    const value = this.contributionInput;
    // null clears the plan; a value sets it. The generated type drops the
    // contract's nullability ($ref-sibling nullable), hence the cast — the
    // API accepts and distinguishes an explicit null.
    const contribution = (
      value && value > 0
        ? { amount_minor: Math.round(value * 100), currency: 'USD' }
        : null
    ) as unknown as undefined;
    const { error } = await this.api.updateGoal(goalId, {
      monthly_contribution: contribution,
    });
    this.savingContribution.set(false);
    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to save the contribution.'));
      return;
    }
    this.editingContributionId.set(null);
    this.goals.reload();
  }

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.submitError.set(null);

    const { name, type, targetAmount, priority, monthlyContribution } = this.form.getRawValue();
    const { error } = await this.api.createGoal({
      name,
      type,
      target: { amount_minor: Math.round(targetAmount * 100), currency: 'USD' },
      priority,
      ...(monthlyContribution > 0
        ? {
            monthly_contribution: {
              amount_minor: Math.round(monthlyContribution * 100),
              currency: 'USD',
            },
          }
        : {}),
    });

    this.submitting.set(false);

    if (error) {
      this.submitError.set(apiErrorMessage(error, 'Failed to create goal.'));
      return;
    }

    this.form.reset({
      name: '', type: 'other', targetAmount: 0, priority: 3, monthlyContribution: 0,
    });
    this.goals.reload();
  }
}
