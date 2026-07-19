import { TitleCasePipe } from '@angular/common';
import { Component, computed, inject, resource, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import type { ReportType } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

@Component({
  selector: 'app-reports',
  imports: [TitleCasePipe, MatCardModule, MatButtonModule],
  templateUrl: './reports.html',
  styleUrl: './reports.scss',
})
export class Reports {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly formatMoney = formatMoney;
  protected readonly canGenerate = computed(() => {
    return this.auth.hasRight('reports.manage');
  });

  protected readonly reports = resource({
    loader: async () => {
      const { data, error } = await this.api.listReports();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load reports.'));
      }
      return data.reports;
    },
  });

  protected readonly generating = signal(false);
  protected readonly generateError = signal<string | null>(null);

  protected async generate(reportType: ReportType): Promise<void> {
    if (this.generating()) {
      return;
    }
    this.generating.set(true);
    this.generateError.set(null);
    const { error } = await this.api.generateReport({ report_type: reportType });
    this.generating.set(false);
    if (error) {
      this.generateError.set(apiErrorMessage(error, 'Failed to generate report.'));
      return;
    }
    this.reports.reload();
  }
}
