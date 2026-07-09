import { DecimalPipe } from '@angular/common';
import { Component, inject, resource } from '@angular/core';
import { RouterLink } from '@angular/router';
import type { EmergencyFundSummary } from '../../api-client';
import { ApiService } from '../../core/api.service';
import { apiErrorMessage } from '../../shared/api-error';
import { formatMoney } from '../../shared/format-money';

const EF_STATUS_LABELS: Record<EmergencyFundSummary['status'], string> = {
  no_bills: 'Add bills to measure',
  no_fund: 'Not started',
  getting_started: 'Getting started',
  on_track: 'On track',
  fully_funded: 'Fully funded',
};

const CATEGORY_LABELS: Record<string, string> = {
  liquid: 'Cash',
  investments: 'Investments',
  retirement: 'Retirement',
  education: 'Education',
  property: 'Property',
};

@Component({
  selector: 'app-overview',
  imports: [DecimalPipe, RouterLink],
  templateUrl: './overview.html',
  styleUrl: './overview.scss',
})
export class Overview {
  private readonly api = inject(ApiService);

  protected readonly household = resource({
    loader: async () => {
      const { data, error } = await this.api.getHouseholdContext();
      if (error) {
        throw new Error(apiErrorMessage(error, 'Failed to load household overview.'));
      }
      return data;
    },
  });

  protected readonly formatMoney = formatMoney;

  protected efStatusLabel(fund: EmergencyFundSummary): string {
    return EF_STATUS_LABELS[fund.status];
  }

  protected categoryLabel(category: string): string {
    return CATEGORY_LABELS[category] ?? category;
  }
}
