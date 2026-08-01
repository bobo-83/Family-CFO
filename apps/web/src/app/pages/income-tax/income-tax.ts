import { DatePipe, PercentPipe } from '@angular/common';
import { Component, HostListener, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type {
  Category,
  IncomeAnalysisResponse,
  IncomeAnalysisTransaction,
  IncomeEarner,
  IncomeEarnerCreateRequest,
  RsuGrant,
  RsuGrantsResponse,
  RsuVestEvent,
  Transaction,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-income-tax',
  imports: [
    DatePipe,
    PercentPipe,
    FormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatButtonModule,
  ],
  templateUrl: './income-tax.html',
  styleUrl: './income-tax.scss',
})
export class IncomeTax {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = () => {
    return this.auth.hasRight('income.manage');
  };

  protected readonly analysis = signal<IncomeAnalysisResponse | null>(null);
  // ADR 0049: transfers that look like misfiled income, awaiting the user's
  // confirm-as-income / keep-as-transfer decision.
  protected readonly suspectedIncome = signal<Transaction[]>([]);
  protected readonly categories = signal<Category[]>([]);
  protected readonly loading = signal(true);
  protected readonly loadError = signal<string | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly actionError = signal<string | null>(null);

  // Tax settings form state (mirrors the loaded analysis).
  protected filingStatus = 'married_joint';
  protected treatedAsNet = true;
  // M65: only the state is asked for — a street address has no use here.
  protected state = '';
  protected readonly states = [
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI',
    'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN',
    'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH',
    'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA',
    'WV', 'WI', 'WY',
  ];

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    this.loadError.set(null);
    const { data, error } = await this.api.getIncomeAnalysis();
    this.loading.set(false);
    if (error || !data) {
      this.loadError.set(apiErrorMessage(error, 'Failed to load the income analysis.'));
      return;
    }
    this.analysis.set(data);
    this.filingStatus = data.tax.filing_status;
    this.treatedAsNet = data.tax.income_treated_as_net;
    this.state = data.tax.state ?? '';
    // ADR 0049: the suspected-income review queue + categories load alongside.
    // M-rsu-grants: grant schedules + live quotes load with them.
    const [review, cats, grants] = await Promise.all([
      this.api.listTransactionsForReview('suspected_income'),
      this.api.listCategories(),
      this.api.listRsuGrants(),
    ]);
    const items = review.data?.transactions ?? [];
    this.suspectedIncome.set(
      [...items].sort((a, b) => Math.abs(b.amount.amount_minor) - Math.abs(a.amount.amount_minor)),
    );
    this.categories.set(cats.data?.categories ?? []);
    this.rsuGrants.set(grants.data ?? null);
  }

  // ADR 0049: confirm a suspected transfer really is income — refile it under the
  // Income category (creating one if the household has none), which also clears
  // the flag. "Keep as transfer" records an exclude override so it's not re-flagged.
  protected async confirmAsIncome(transaction: Transaction): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(transaction.id);
    this.actionError.set(null);
    let incomeId = this.categories().find((c) => c.name.toLowerCase() === 'income')?.id;
    if (!incomeId) {
      const { data, error } = await this.api.createCategory({ name: 'Income' });
      if (error || !data) {
        this.busy.set(null);
        this.actionError.set(apiErrorMessage(error, 'Failed to create the Income category.'));
        return;
      }
      incomeId = data.id;
    }
    const { error } = await this.api.updateTransaction(transaction.id, { category_id: incomeId });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to confirm as income.'));
      return;
    }
    await this.load();
  }

  protected async keepAsTransfer(transaction: Transaction): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(transaction.id);
    this.actionError.set(null);
    const { error } = await this.api.setIncomeOverride(transaction.id, 'exclude');
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save the change.'));
      return;
    }
    await this.load();
  }

  private async override(
    transaction: IncomeAnalysisTransaction,
    verdict: 'include' | 'exclude' | 'clear',
  ): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(transaction.transaction_id);
    this.actionError.set(null);
    const { error } = await this.api.setIncomeOverride(transaction.transaction_id, verdict);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save the change.'));
      return;
    }
    await this.load();
  }

  // ADR 0055: reclassify a counted deposit from the income page — e.g. a
  // transfer of already-counted RSU proceeds double-counted as income. Moving it
  // off the Income category drops it from the rollup.
  protected async recategorizeDeposit(
    transaction: IncomeAnalysisTransaction,
    categoryId: string,
  ): Promise<void> {
    if (this.busy() || !categoryId) {
      return;
    }
    this.busy.set(transaction.transaction_id);
    this.actionError.set(null);
    const { error } = await this.api.updateTransaction(transaction.transaction_id, {
      category_id: categoryId,
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to recategorize.'));
      return;
    }
    await this.load();
  }

  protected remove(transaction: IncomeAnalysisTransaction): Promise<void> {
    return this.override(transaction, 'exclude');
  }

  // M63: unclassified deposits split into an active list and a collapsed
  // rejected list (excluded by the user, restorable).
  protected activeOther(): IncomeAnalysisTransaction[] {
    return (this.analysis()?.other_inflows ?? []).filter((t) => !t.excluded);
  }

  protected rejectedOther(): IncomeAnalysisTransaction[] {
    return (this.analysis()?.other_inflows ?? []).filter((t) => t.excluded);
  }

  protected addAsIncome(transaction: IncomeAnalysisTransaction): Promise<void> {
    return this.override(transaction, 'include');
  }

  protected restore(transaction: IncomeAnalysisTransaction): Promise<void> {
    return this.override(transaction, 'clear');
  }

  // --- M73: compensation profile ---
  protected earnerForm: {
    label: string;
    baseSalary: number | null;
    rsuAnnual: number | null;
    rsuFrequency: '' | 'monthly' | 'quarterly' | 'semiannual' | 'annual';
    rsuNextVest: string;
    bonusPercent: number | null;
    bonusMonth: number | null;
    w2Year: number | null;
    w2Wages: number | null;
    w2Withheld: number | null;
  } = this.emptyEarnerForm();
  protected readonly scanNote = signal<string | null>(null);
  protected readonly scanning = signal(false);

  private emptyEarnerForm() {
    return {
      label: '',
      baseSalary: null,
      rsuAnnual: null,
      rsuFrequency: '' as const,
      rsuNextVest: '',
      bonusPercent: null,
      bonusMonth: null,
      w2Year: null,
      w2Wages: null,
      w2Withheld: null,
    };
  }

  protected async addEarner(): Promise<void> {
    if (this.busy() || !this.earnerForm.label.trim()) {
      return;
    }
    this.busy.set('earner');
    this.actionError.set(null);
    const f = this.earnerForm;
    const body: IncomeEarnerCreateRequest = {
      label: f.label.trim(),
      base_salary_minor: Math.round((f.baseSalary ?? 0) * 100),
      rsu_annual_minor: Math.round((f.rsuAnnual ?? 0) * 100),
      ...(f.rsuFrequency ? { rsu_frequency: f.rsuFrequency } : {}),
      ...(f.rsuNextVest ? { rsu_next_vest_date: f.rsuNextVest } : {}),
      bonus_percent: f.bonusPercent ?? 0,
      ...(f.bonusMonth ? { bonus_month: f.bonusMonth } : {}),
      ...(f.w2Year ? { w2_year: f.w2Year } : {}),
      ...(f.w2Wages ? { w2_wages_minor: Math.round(f.w2Wages * 100) } : {}),
      ...(f.w2Withheld ? { w2_withheld_minor: Math.round(f.w2Withheld * 100) } : {}),
    };
    const { error } = await this.api.createIncomeEarner(body);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save the earner.'));
      return;
    }
    this.earnerForm = this.emptyEarnerForm();
    this.scanNote.set(null);
    await this.load();
  }

  protected async removeEarner(id: string): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(id);
    this.actionError.set(null);
    const { error } = await this.api.deleteIncomeEarner(id);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to remove the earner.'));
      return;
    }
    await this.load();
  }

  protected async onW2Selected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    await this.scanW2File(file);
  }

  /**
   * M114 (ADR 0028): every statement input accepts paste. Ctrl/Cmd+V anywhere
   * on this page feeds a copied screenshot or PDF into the same scan path as
   * the file picker.
   */
  @HostListener('window:paste', ['$event'])
  async onPaste(event: ClipboardEvent): Promise<void> {
    if (!this.canWrite()) {
      return;
    }
    const items = event.clipboardData?.items ?? [];
    for (const item of Array.from(items)) {
      if (item.kind !== 'file') {
        continue;
      }
      const file = item.getAsFile();
      if (file && /^(image\/|application\/pdf)/.test(file.type)) {
        event.preventDefault();
        await this.scanW2File(file);
        return;
      }
    }
  }

  protected async scanW2File(file: File | undefined | null): Promise<void> {
    if (!file || this.scanning()) {
      return;
    }
    this.scanning.set(true);
    this.scanNote.set(null);
    this.actionError.set(null);
    const dataUrl: string = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const [meta, base64] = dataUrl.split(',', 2);
    const mediaType = /data:([^;]+)/.exec(meta)?.[1] ?? 'image/jpeg';
    const { data, error } = await this.api.scanW2({
      image_base64: base64,
      image_media_type: mediaType as 'image/jpeg' | 'image/png' | 'image/webp' | 'application/pdf',
    });
    this.scanning.set(false);
    if (error || !data) {
      this.actionError.set(apiErrorMessage(error, 'W2 scan failed.'));
      return;
    }
    // Prefill only — the user confirms every value before saving.
    if (data.year) this.earnerForm.w2Year = data.year;
    if (data.wages_minor) this.earnerForm.w2Wages = data.wages_minor / 100;
    if (data.federal_withheld_minor)
      this.earnerForm.w2Withheld = data.federal_withheld_minor / 100;
    if (data.employer && !this.earnerForm.label) this.earnerForm.label = data.employer;
    this.scanNote.set(data.note);
  }

  // --- M-rsu-grants: grant-based RSU schedules priced by a live quote ---
  protected readonly rsuGrants = signal<RsuGrantsResponse | null>(null);
  protected readonly quoteBusy = signal(false);

  protected grantForm: {
    earnerId: string;
    ticker: string;
    units: number | null;
    grantDate: string;
    vestYears: number | null;
    frequency: 'monthly' | 'quarterly' | 'semiannual' | 'annual';
  } = this.emptyGrantForm();

  private emptyGrantForm() {
    return {
      earnerId: '',
      ticker: '',
      units: null,
      grantDate: '',
      vestYears: 2,
      frequency: 'quarterly' as const,
    };
  }

  protected earners(): IncomeEarner[] {
    return this.analysis()?.profile?.earners ?? [];
  }

  protected vestFrequencyLabel(frequency: RsuGrant['frequency']): string {
    return (
      { monthly: 'monthly', quarterly: 'quarterly', semiannual: 'twice a year', annual: 'annually' }[
        frequency
      ] ?? frequency
    );
  }

  protected async refreshQuotes(): Promise<void> {
    if (this.quoteBusy()) {
      return;
    }
    this.quoteBusy.set(true);
    this.actionError.set(null);
    const { data, error } = await this.api.refreshRsuQuotes();
    this.quoteBusy.set(false);
    if (error || !data) {
      this.actionError.set(apiErrorMessage(error, 'Failed to refresh the share price.'));
      return;
    }
    this.rsuGrants.set(data);
  }

  protected async addGrant(): Promise<void> {
    const f = this.grantForm;
    if (this.busy() || !f.earnerId || !f.ticker.trim() || !f.units || !f.grantDate) {
      return;
    }
    this.busy.set('grant');
    this.actionError.set(null);
    const { error } = await this.api.createRsuGrant({
      earner_id: f.earnerId,
      ticker: f.ticker.trim().toUpperCase(),
      units: f.units,
      grant_date: f.grantDate,
      vest_years: f.vestYears ?? 2,
      frequency: f.frequency,
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to add the grant.'));
      return;
    }
    this.grantForm = this.emptyGrantForm();
    // The grant changes the derived RSU annual value server-side — reload
    // the analysis (and the grants alongside it).
    await this.load();
  }

  protected async removeGrant(grantId: string): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(grantId);
    this.actionError.set(null);
    const { error } = await this.api.deleteRsuGrant(grantId);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete the grant.'));
      return;
    }
    await this.load();
  }

  protected async addVestEvent(grant: RsuGrant, date: string, units: string): Promise<void> {
    const parsedUnits = Number(units);
    if (this.busy() || !date || !units || !Number.isFinite(parsedUnits) || parsedUnits <= 0) {
      return;
    }
    this.busy.set(grant.id);
    this.actionError.set(null);
    const { error } = await this.api.addRsuVestEvent(grant.id, {
      vest_date: date,
      units: parsedUnits,
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to add the vest.'));
      return;
    }
    await this.load();
  }

  protected async saveVestEvent(event: RsuVestEvent, date: string, units: string): Promise<void> {
    const parsedUnits = Number(units);
    if (this.busy() || !date || !units || !Number.isFinite(parsedUnits) || parsedUnits <= 0) {
      return;
    }
    this.busy.set(event.id);
    this.actionError.set(null);
    const { error } = await this.api.updateRsuVestEvent(event.id, {
      vest_date: date,
      units: parsedUnits,
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save the vest.'));
      return;
    }
    await this.load();
  }

  protected async removeVestEvent(eventId: string): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(eventId);
    this.actionError.set(null);
    const { error } = await this.api.deleteRsuVestEvent(eventId);
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to delete the vest.'));
      return;
    }
    await this.load();
  }

  protected async saveSettings(): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set('settings');
    this.actionError.set(null);
    const { error } = await this.api.updateIncomeTaxSettings({
      tax_filing_status: this.filingStatus as 'single' | 'married_joint' | 'head_of_household',
      income_treated_as_net: this.treatedAsNet,
      ...(this.state ? { state: this.state } : {}),
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save tax settings.'));
      return;
    }
    await this.load();
  }
}
