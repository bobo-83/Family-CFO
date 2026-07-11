import { DatePipe, PercentPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import type { IncomeAnalysisResponse, IncomeAnalysisTransaction } from '../../api-client';
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

  protected addAsIncome(transaction: IncomeAnalysisTransaction): Promise<void> {
    return this.override(transaction, 'include');
  }

  protected restore(transaction: IncomeAnalysisTransaction): Promise<void> {
    return this.override(transaction, 'clear');
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
    });
    this.busy.set(null);
    if (error) {
      this.actionError.set(apiErrorMessage(error, 'Failed to save tax settings.'));
      return;
    }
    await this.load();
  }
}
