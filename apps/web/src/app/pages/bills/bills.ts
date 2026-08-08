import { DatePipe } from '@angular/common';
import { FREQUENCY_LABELS, labelFor } from '../../shared/enum-labels';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type {
  Bill as BillDto,
  BillCreditsResponse,
  Money,
  BillSuggestion,
  BillUpdateSuggestion,
  PaymentTimelineItem,
  PaymentTimelineResponse,
  RecurringFrequency,
  Transaction,
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
  'semiannual',
  'annual',
];

// M111 (ADR 0024): bill-paying order — the same grouping the iOS tab renders.
const TIMELINE_GROUPS: { status: PaymentTimelineItem['status']; title: string }[] = [
  {
    status: 'overdue',
    title: $localize`:Payment timeline section|Bills whose due date has already passed:Overdue`,
  },
  {
    status: 'due_soon',
    title: $localize`:Payment timeline section|Bills due within the next few days:Due soon`,
  },
  {
    status: 'no_date',
    title: $localize`:Payment timeline section|Bills whose due day cannot be inferred yet:No due date yet`,
  },
  {
    status: 'paid',
    title: $localize`:Payment timeline section|Bills already settled in the current cycle:Paid this cycle`,
  },
  {
    status: 'upcoming',
    title: $localize`:Payment timeline section|Bills due further out:Upcoming`,
  },
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
  /** #20: `biweekly` is a wire value; a person reads 'every two weeks'. */
  protected frequencyLabel(frequency: string): string {
    return labelFor(FREQUENCY_LABELS, frequency);
  }

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
          return $localize`:Payment timeline status|The bill is settled, with no receipt detail to show:Paid`;
        }
        const next = item.due_date
          ? $localize`:Payment timeline fragment|Appended when the next occurrence already has a due date: · next ${short(item.due_date)}:date:`
          : '';
        return $localize`:Payment timeline status|When the bill was paid and for how much:Paid ${short(paid.occurred_at)}:date: · ${formatMoney(paid.amount)}:amount:${next}:next:`;
      }
      case 'overdue':
        return $localize`:Payment timeline status|The due date passed with no matching charge:Was due ${short(item.due_date)}:date: · no payment seen`;
      case 'no_date':
        return item.kind === 'credit_card'
          ? $localize`:Payment timeline status|A card balance with no statement due date:Current balance · due date unknown`
          : $localize`:Payment timeline status|No due day could be inferred for this bill:Due date unknown`;
      default: {
        const days = item.days_until;
        if (days === 0) {
          return $localize`:Payment timeline status|The bill falls due today:Due today`;
        }
        if (days === 1) {
          return $localize`:Payment timeline status|The bill falls due tomorrow:Due tomorrow`;
        }
        if (days != null && days > 1 && days <= 14) {
          return $localize`:Payment timeline status|Due date plus how many days away it is; days is always more than one here:Due ${short(item.due_date)}:date: · in ${days}:days: days`;
        }
        return $localize`:Payment timeline status|Due date further out than a fortnight:Due ${short(item.due_date)}:date:`;
      }
    }
  }

  protected timelineKindLabel(kind: PaymentTimelineItem['kind']): string {
    switch (kind) {
      case 'credit_card':
        return $localize`:Payment kind|A credit-card payment:Card`;
      case 'mortgage':
        return $localize`:Payment kind|A mortgage payment:Mortgage`;
      case 'loan':
        return $localize`:Payment kind|A loan payment:Loan`;
      case 'lease':
        return $localize`:Payment kind|A lease payment:Lease`;
      default:
        return $localize`:Payment kind|An ordinary recurring bill:Bill`;
    }
  }

  // --- "I already paid this": link a bill occurrence to the charge that paid
  // it — the manual escape hatch when auto-matching misses a variable bill.
  // Only bill rows can be linked (the endpoint is bill-scoped); card/loan rows
  // carry account ids, not bill ids.

  protected readonly markPaidItem = signal<PaymentTimelineItem | null>(null);
  // null = still fetching; [] = fetched, nothing near the due date.
  protected readonly candidates = signal<Transaction[] | null>(null);
  protected readonly linkError = signal<string | null>(null);
  protected readonly linkBusy = signal(false);

  protected canMarkPaid(item: PaymentTimelineItem): boolean {
    return (
      this.canWrite() &&
      item.kind === 'bill' &&
      !!item.due_date &&
      (item.status === 'overdue' || item.status === 'due_soon' || item.status === 'upcoming')
    );
  }

  protected async openMarkPaid(item: PaymentTimelineItem): Promise<void> {
    if (!item.due_date) {
      return;
    }
    this.markPaidItem.set(item);
    this.candidates.set(null);
    this.linkError.set(null);
    const { data, error } = await this.api.listBillPaymentCandidates(item.id, item.due_date);
    // The user may have closed the picker (or opened another) while we fetched.
    if (this.markPaidItem() !== item) {
      return;
    }
    if (error || !data) {
      this.linkError.set(apiErrorMessage(error, $localize`Failed to load charges.`));
      this.candidates.set([]);
      return;
    }
    this.candidates.set(data.transactions);
  }

  protected closeMarkPaid(): void {
    this.markPaidItem.set(null);
    this.candidates.set(null);
    this.linkError.set(null);
  }

  protected async linkPayment(transaction: Transaction): Promise<void> {
    const item = this.markPaidItem();
    if (!item?.due_date || this.linkBusy()) {
      return;
    }
    this.linkBusy.set(true);
    this.linkError.set(null);
    // The row's OWN due date — the occurrence being settled.
    const { error } = await this.api.linkBillPayment(item.id, transaction.id, item.due_date);
    this.linkBusy.set(false);
    if (error) {
      this.linkError.set(apiErrorMessage(error, $localize`Failed to link the payment.`));
      return;
    }
    this.closeMarkPaid();
    await this.load();
  }

  // No confirm: unlinking is undoable (re-link from the same picker).
  protected async unlinkPayment(item: PaymentTimelineItem): Promise<void> {
    const linkId = item.paid_with?.link_id;
    if (!linkId || this.linkBusy()) {
      return;
    }
    this.linkBusy.set(true);
    this.linkError.set(null);
    const { error } = await this.api.unlinkBillPayment(item.id, linkId);
    this.linkBusy.set(false);
    if (error) {
      this.linkError.set(apiErrorMessage(error, $localize`Failed to unlink the payment.`));
      return;
    }
    await this.load();
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
    const [billsResult, timelineResult, creditsResult] = await Promise.all([
      this.api.listBills(),
      this.api.getPaymentTimeline(),
      this.api.listBillCredits(),
    ]);
    this.loading.set(false);
    if (billsResult.error || !billsResult.data) {
      this.loadError.set(apiErrorMessage(billsResult.error, $localize`Failed to load bills.`));
      return;
    }
    this.bills.set(billsResult.data.bills);
    // The timeline degrades gracefully: the manage list still works without it.
    this.timeline.set(timelineResult.data ?? null);
    // Credits too: the page works without the section.
    this.credits.set(creditsResult.data ?? null);
  }

  private async loadSuggestions(): Promise<void> {
    const { data, error } = await this.api.listBillSuggestions();
    if (error || !data) {
      this.suggestionError.set(apiErrorMessage(error, $localize`Failed to load suggestions.`));
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
      this.suggestionError.set(apiErrorMessage(error, $localize`Failed to update the bill.`));
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
      this.suggestionError.set(apiErrorMessage(error, $localize`Failed to dismiss the update.`));
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
      this.suggestionError.set(apiErrorMessage(error, $localize`Failed to create the bill.`));
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
      this.suggestionError.set(apiErrorMessage(error, $localize`Failed to dismiss the suggestion.`));
      return;
    }
    await this.loadSuggestions();
  }

  protected readonly form = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    // min 0, not 0.01: a net-metered bill legitimately saves with $0 due
    // (the statement credit is tracked separately — M-credits).
    amount: [0, [Validators.required, Validators.min(0)]],
    frequency: ['monthly' as RecurringFrequency, Validators.required],
    nextDueDate: [''],
  });

  protected readonly submitting = signal(false);
  protected readonly submitError = signal<string | null>(null);

  // --- M-credits: statement credits (net metering) per bill, with rollups ---

  protected readonly credits = signal<BillCreditsResponse | null>(null);
  protected readonly creditError = signal<string | null>(null);

  // A scanned statement's credit, waiting for the user to confirm. When the
  // scanned biller matches an existing bill, targetBill lets one click record
  // the credit there instead of creating a duplicate bill.
  protected readonly scannedCredit = signal<{
    amountMinor: number;
    targetBill: BillDto | null;
  } | null>(null);

  protected readonly creditBusy = signal(false);

  // "USD 115.66 in credits" on the bill's own row — the money owed back
  // belongs on the bill, not only in the Statement credits section.
  protected creditTotalFor(billId: string): Money | null {
    const group = this.credits()?.bills.find((g) => g.bill_id === billId);
    return group && group.total.amount_minor > 0 ? group.total : null;
  }

  protected async recordScannedCredit(): Promise<void> {
    const pending = this.scannedCredit();
    const target = pending?.targetBill;
    if (!pending || !target || this.creditBusy()) {
      return;
    }
    this.creditBusy.set(true);
    this.creditError.set(null);
    const { error } = await this.api.recordBillCredit(target.id, {
      amount: { amount_minor: pending.amountMinor, currency: target.amount.currency },
    });
    this.creditBusy.set(false);
    if (error) {
      this.creditError.set(apiErrorMessage(error, $localize`Failed to record the credit.`));
      return;
    }
    this.scannedCredit.set(null);
    this.scanNote.set(null);
    await this.load();
  }

  // --- Bill scan: photo/PDF → candidate values prefill the add form ---

  protected readonly scanning = signal(false);
  protected readonly scanNote = signal<string | null>(null);

  protected async onBillFileSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    await this.scanBillFile(file);
  }

  protected async scanBillFile(file: File | undefined | null): Promise<void> {
    if (!file || this.scanning()) {
      return;
    }
    this.scanning.set(true);
    this.scanNote.set(null);
    this.submitError.set(null);
    const dataUrl: string = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const [meta, base64] = dataUrl.split(',', 2);
    const mediaType = /data:([^;]+)/.exec(meta)?.[1] ?? 'image/jpeg';
    const { data, error } = await this.api.scanBill(base64, mediaType);
    this.scanning.set(false);
    if (error || !data) {
      this.submitError.set(apiErrorMessage(error, $localize`Bill scan failed.`));
      return;
    }
    // Prefill only — never overwrite what the user already typed.
    const current = this.form.getRawValue();
    if (data.name && !current.name.trim()) this.form.patchValue({ name: data.name });
    if (data.amount_minor && !current.amount) {
      this.form.patchValue({ amount: data.amount_minor / 100 });
    }
    if (data.frequency) this.form.patchValue({ frequency: data.frequency });
    if (data.next_due_date && !current.nextDueDate) {
      this.form.patchValue({ nextDueDate: data.next_due_date });
    }
    // A credit statement: hold the credit until the user confirms. If the
    // scanned biller matches an existing bill, offer to record it there.
    if (data.credit_minor) {
      const scannedName = (data.name ?? '').trim().toLowerCase();
      const match =
        this.bills().find((bill) => bill.name.trim().toLowerCase() === scannedName) ?? null;
      this.scannedCredit.set({ amountMinor: data.credit_minor, targetBill: match });
    } else {
      this.scannedCredit.set(null);
    }
    this.scanNote.set(data.note);
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    this.submitting.set(true);
    this.submitError.set(null);
    const { name, amount, frequency, nextDueDate } = this.form.getRawValue();
    const { data: created, error } = await this.api.createBill({
      name,
      amount: { amount_minor: Math.round(amount * 100), currency: 'USD' },
      frequency,
      ...(nextDueDate ? { next_due_date: nextDueDate } : {}),
    });
    if (error || !created) {
      this.submitting.set(false);
      this.submitError.set(apiErrorMessage(error, $localize`Failed to create bill.`));
      return;
    }
    // A scanned credit statement: the new bill starts with its credit recorded.
    const pending = this.scannedCredit();
    if (pending) {
      await this.api.recordBillCredit(created.id, {
        amount: { amount_minor: pending.amountMinor, currency: created.amount.currency },
      });
      this.scannedCredit.set(null);
    }
    this.submitting.set(false);
    this.form.reset({ name: '', amount: 0, frequency: 'monthly', nextDueDate: '' });
    this.scanNote.set(null);
    await this.load();
  }

  protected async remove(id: string): Promise<void> {
    if (!confirm($localize`:Confirmation|Browser confirm shown before a bill is deleted:Delete this bill?`)) {
      return;
    }
    const { error } = await this.api.deleteBill(id);
    if (error) {
      this.submitError.set(apiErrorMessage(error, $localize`Failed to delete bill.`));
      return;
    }
    await this.load();
  }

  // --- Edit an existing bill (M110 parity with iOS, ADR 0022/0025) ---

  protected readonly editingId = signal<string | null>(null);
  protected readonly editError = signal<string | null>(null);
  protected readonly editForm = this.formBuilder.nonNullable.group({
    name: ['', Validators.required],
    amount: [0, [Validators.required, Validators.min(0)]],
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
      this.editError.set(apiErrorMessage(error, $localize`Failed to update the bill.`));
      return;
    }
    this.editingId.set(null);
    await this.load();
  }
}
