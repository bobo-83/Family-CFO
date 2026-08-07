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
  weekly: $localize`:Funding cadence|How often a contribution repeats:weekly`,
  biweekly: $localize`:Funding cadence|How often a contribution repeats:every two weeks`,
  semimonthly: $localize`:Funding cadence|How often a contribution repeats:twice a month`,
  monthly: $localize`:Funding cadence|How often a contribution repeats:monthly`,
  quarterly: $localize`:Funding cadence|How often a contribution repeats:quarterly`,
  semiannual: $localize`:Funding cadence|How often a contribution repeats:twice a year`,
  annual: $localize`:Funding cadence|How often a contribution repeats:yearly`,
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
      emergency_fund: $localize`:Goal type|Savings goal category:Emergency fund`,
      vacation: $localize`:Goal type|Savings goal category:Vacation`,
      retirement: $localize`:Goal type|Savings goal category:Retirement`,
      college: $localize`:Goal type|Savings goal category:College`,
      vehicle: $localize`:Goal type|Savings goal category:Vehicle`,
      renovation: $localize`:Goal type|Savings goal category:Renovation`,
      other: $localize`:Goal type|Savings goal category:Other`,
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
        throw new Error(apiErrorMessage(error, $localize`Failed to load goals.`));
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
    const monthly = $localize`:Funding rate|Money going into a goal each month, e.g. "USD 500.00/mo":${formatMoney(funding.monthly_equivalent)}:amount:/mo`;
    switch (funding.status) {
      case 'on_track': {
        const projected = funding.projected_completion
          ? $localize`:Funding line fragment|Appended when a completion month is projected: · projected ${this.monthLabel(funding.projected_completion)}:month:`
          : '';
        return $localize`:Goal funding status|The goal is on track:On track — ${monthly}:rate: going in${projected}:projected:`;
      }
      case 'behind': {
        const target = goal.target_date
          ? $localize`:Funding line fragment|Appended when the goal has a target date: by ${this.monthLabel(goal.target_date)}:month:`
          : '';
        const projected = funding.projected_completion
          ? $localize`:Funding line fragment|Appended in parentheses when a completion month is projected: (projected ${this.monthLabel(funding.projected_completion)}:month:)`
          : '';
        return $localize`:Goal funding status|The current rate will not reach the target:Behind — ${monthly}:rate: won't reach the target${target}:targetDate:${projected}:projected:`;
      }
      case 'funded_no_date':
        return $localize`:Goal funding status|Money is going in but no completion month is projected:${monthly}:rate: going in`;
      case 'unfunded':
        return goal.type === 'retirement'
          ? $localize`:Goal funding status|Retirement goals are fed by payroll deductions the ledger never sees:No linked transfers — 401(k) payroll deductions don't appear here.`
          : $localize`:Goal funding status|No linked transfers feed this goal:Nothing is currently funding this goal`;
      default:
        return null;
    }
  }

  /** Compact "College 529 · USD 500.00 monthly" row under the funding line. */
  protected fundingSourceLine(source: GoalFundingSource): string {
    const cadence = CADENCE_WORDS[source.frequency] ?? source.frequency;
    return $localize`:Goal funding source|One contribution feeding a goal, e.g. "College 529 · USD 500.00 monthly":${source.destination_name}:destination: · ${formatMoney(source.amount)}:amount: ${cadence}:cadence:`;
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
    if (!confirm($localize`:Confirmation|Browser confirm before a goal is deleted:Delete the goal "${name}:name:"?`)) {
      return;
    }
    const { error } = await this.api.deleteGoal(goalId);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to delete the goal.`));
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
      this.submitError.set(apiErrorMessage(error, $localize`Failed to save the contribution.`));
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
      this.submitError.set(apiErrorMessage(error, $localize`Failed to create goal.`));
      return;
    }

    this.form.reset({
      name: '', type: 'other', targetAmount: 0, priority: 3, monthlyContribution: 0,
    });
    this.goals.reload();
  }
}
