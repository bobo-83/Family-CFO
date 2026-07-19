import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type {
  Bill as BillDto,
  BillSuggestion,
  BillUpdateSuggestion,
  PaymentTimelineItem,
  PaymentTimelineResponse,
  RecurringFrequency,
} from '../../api-client';
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

// M111 (ADR 0024): bill-paying order — the same grouping the iOS tab renders.
const TIMELINE_GROUPS: { status: PaymentTimelineItem['status']; title: string }[] = [
  { status: 'overdue', title: 'Overdue' },
  { status: 'due_soon', title: 'Due soon' },
  { status: 'no_date', title: 'No due date yet' },
  { status: 'paid', title: 'Paid this cycle' },
  { status: 'upcoming', title: 'Upcoming' },
];

@Component({
  selector: 'app-bills',
  imports: [
    ReactiveFormsModule,
    DatePipe,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
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
    return this.auth.hasRight('bills.manage');
  };

  protected readonly bills = signal<BillDto[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);

  // M111 (ADR 0024): the payment timeline is the page's primary view.
  protected readonly timeline = signal<PaymentTimelineResponse | null>(null);
  protected readonly timelineSections = computed(() => {
    const data = this.timeline();
    if (!data) {
      return [];
    }
    return TIMELINE_GROUPS.map(({ status, title }) => ({
      title,
      status,
      items: data.items.filter((item) => item.status === status),
    })).filter((section) => section.items.length > 0);
  });

  protected timelineStatusLine(item: PaymentTimelineItem): string {
    const short = (iso: string | null | undefined): string => {
      if (!iso) {
        return '—';
      }
      const parsed = new Date(`${iso.slice(0, 10)}T00:00:00`);
      return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    };
    switch (item.status) {
      case 'paid': {
        const paid = item.paid_with;
        if (!paid) {
          return 'Paid';
        }
        const next = item.due_date ? ` · next ${short(item.due_date)}` : '';
        return `Paid ${short(paid.occurred_at)} · ${formatMoney(paid.amount)}${next}`;
      }
      case 'overdue':
        return `Was due ${short(item.due_date)} · no payment seen`;
      case 'no_date':
        return item.kind === 'credit_card'
          ? 'Current balance · due date unknown'
          : 'Due date unknown';
      default: {
        const days = item.days_until;
        if (days === 0) {
          return 'Due today';
        }
        if (days === 1) {
          return 'Due tomorrow';
        }
        if (days != null && days > 1 && days <= 14) {
          return `Due ${short(item.due_date)} · in ${days} days`;
        }
        return `Due ${short(item.due_date)}`;
      }
    }
  }

  protected timelineKindLabel(kind: PaymentTimelineItem['kind']): string {
    switch (kind) {
      case 'credit_card':
        return 'Card';
      case 'mortgage':
        return 'Mortgage';
      case 'loan':
        return 'Loan';
      case 'lease':
        return 'Lease';
      default:
        return 'Bill';
    }
  }

  // M58: recurring charges detected in checking/credit-card transactions.
  protected readonly suggestions = signal<BillSuggestion[]>([]);
  // M59: existing bills whose live charge pattern drifted (price changes).
  protected readonly updates = signal<BillUpdateSuggestion[]>([]);
  protected readonly suggestionError = signal<string | null>(null);
  protected readonly suggestionBusy = signal<string | null>(null);

  constructor() {
    void this.load();
    void this.loadSuggestions();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set(null);
    const [billsResult, timelineResult] = await Promise.all([
      this.api.listBills(),
      this.api.getPaymentTimeline(),
    ]);
    this.loading.set(false);
    if (billsResult.error || !billsResult.data) {
      this.loadError.set(apiErrorMessage(billsResult.error, 'Failed to load bills.'));
      return;
    }
    this.bills.set(billsResult.data.bills);
    // The timeline degrades gracefully: the manage list still works without it.
    this.timeline.set(timelineResult.data ?? null);
  }

  private async loadSuggestions(): Promise<void> {
    const { data, error } = await this.api.listBillSuggestions();
    if (error || !data) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to load suggestions.'));
      return;
    }
    this.suggestions.set(data.suggestions);
    this.updates.set(data.updates ?? []);
  }

  protected async applyUpdate(update: BillUpdateSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(update.dismiss_key);
    this.suggestionError.set(null);
    const { error } = await this.api.updateBill(update.bill_id, {
      amount: update.suggested_amount,
      frequency: update.frequency,
      next_due_date: update.next_due_date,
    });
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to update the bill.'));
      return;
    }
    await Promise.all([this.load(), this.loadSuggestions()]);
  }

  protected async dismissUpdate(update: BillUpdateSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(update.dismiss_key);
    this.suggestionError.set(null);
    const { error } = await this.api.dismissBillSuggestion(update.dismiss_key);
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to dismiss the update.'));
      return;
    }
    await this.loadSuggestions();
  }

  protected async confirmSuggestion(suggestion: BillSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(suggestion.merchant_key);
    this.suggestionError.set(null);
    const { error } = await this.api.createBill({
      name: suggestion.name,
      amount: suggestion.amount,
      frequency: suggestion.frequency,
      next_due_date: suggestion.next_due_date,
    });
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to create the bill.'));
      return;
    }
    await Promise.all([this.load(), this.loadSuggestions()]);
  }

  protected async dismissSuggestion(suggestion: BillSuggestion): Promise<void> {
    if (this.suggestionBusy()) {
      return;
    }
    this.suggestionBusy.set(suggestion.merchant_key);
    this.suggestionError.set(null);
    const { error } = await this.api.dismissBillSuggestion(suggestion.merchant_key);
    this.suggestionBusy.set(null);
    if (error) {
      this.suggestionError.set(apiErrorMessage(error, 'Failed to dismiss the suggestion.'));
      return;
    }
    await this.loadSuggestions();
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

  // --- Edit an existing bill (M110 parity with iOS, ADR 0022/0025) ---

  protected readonly editingId = signal<string | null>(null);
  protected readonly editError = signal<string | null>(null);
  protected readonly editForm = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    amount: [0, [Validators.required, Validators.min(0.01)]],
    frequency: ['monthly' as RecurringFrequency, Validators.required],
    nextDueDate: [''],
  });

  protected startEdit(bill: BillDto): void {
    this.editError.set(null);
    this.editForm.reset({
      name: bill.name,
      amount: bill.amount.amount_minor / 100,
      frequency: bill.frequency,
      nextDueDate: bill.next_due_date ?? '',
    });
    this.editingId.set(bill.id);
  }

  protected cancelEdit(): void {
    this.editingId.set(null);
  }

  protected async saveEdit(): Promise<void> {
    const id = this.editingId();
    if (!id || this.editForm.invalid) {
      this.editForm.markAllAsTouched();
      return;
    }
    this.editError.set(null);
    const { name, amount, frequency, nextDueDate } = this.editForm.getRawValue();
    const { error } = await this.api.updateBill(id, {
      name,
      amount: { amount_minor: Math.round(amount * 100), currency: 'USD' },
      frequency,
      ...(nextDueDate ? { next_due_date: nextDueDate } : {}),
    });
    if (error) {
      this.editError.set(apiErrorMessage(error, 'Failed to update the bill.'));
      return;
    }
    this.editingId.set(null);
    await this.load();
  }
}
