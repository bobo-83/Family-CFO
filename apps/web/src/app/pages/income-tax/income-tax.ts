import { DatePipe, PercentPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import type {
  IncomeAnalysisResponse,
  IncomeAnalysisTransaction,
  IncomeEarnerCreateRequest,
} from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-income-tax',
  imports: [DatePipe, PercentPipe, FormsModule],
  templateUrl: './income-tax.html',
  styleUrl: './income-tax.scss',
})
export class IncomeTax {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly formatMoney = formatMoney;
  protected readonly canWrite = () => {
    const role = this.auth.role();
    return role === 'owner' || role === 'adult';
  };

  protected readonly analysis = signal<IncomeAnalysisResponse | null>(null);
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
