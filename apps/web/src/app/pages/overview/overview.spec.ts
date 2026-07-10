import { provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Overview } from './overview';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Overview', () => {
  let apiMock: {
    getHouseholdContext: ReturnType<typeof vi.fn>;
    updateHousehold: ReturnType<typeof vi.fn>;
  };

  function configure(role = 'owner') {
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => role } },
      ],
    });
  }

  beforeEach(() => {
    apiMock = {
      getHouseholdContext: vi.fn(),
      updateHousehold: vi.fn().mockResolvedValue(response({})),
    };
    configure();
  });

  it('renders the enriched summary cards (M38)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 97_927_848, currency: 'USD' },
        emergency_fund_months: 0.96,
        emergency_fund: {
          months: 0.96,
          reserved: { amount_minor: 200_000, currency: 'USD' },
          using_designations: true,
          monthly_expenses: { amount_minor: 208_000, currency: 'USD' },
          target_months_min: 3,
          target_months_recommended: 6,
          gap_to_recommended: { amount_minor: 1_048_000, currency: 'USD' },
          status: 'getting_started',
        },
        monthly_cash_flow: {
          income: { amount_minor: 600_000, currency: 'USD' },
          bills: { amount_minor: 208_000, currency: 'USD' },
          net: { amount_minor: 392_000, currency: 'USD' },
        },
        asset_breakdown: [
          { category: 'liquid', total: { amount_minor: 1_500_000, currency: 'USD' } },
          { category: 'retirement', total: { amount_minor: 80_000_000, currency: 'USD' } },
        ],
        total_debt: { amount_minor: 30_000_000, currency: 'USD' },
        upcoming_bills: [
          {
            id: 'b1',
            name: 'Internet',
            amount: { amount_minor: 8_000, currency: 'USD' },
            due_date: '2026-07-12',
            days_until: 3,
          },
        ],
        net_worth_history: [
          { as_of: '2026-07-07', net_worth: { amount_minor: 90_000_000, currency: 'USD' } },
          { as_of: '2026-07-08', net_worth: { amount_minor: 95_000_000, currency: 'USD' } },
          { as_of: '2026-07-09', net_worth: { amount_minor: 97_927_848, currency: 'USD' } },
        ],
        top_goal: {
          id: 'g1',
          name: 'Emergency fund',
          type: 'emergency_fund',
          current: { amount_minor: 1_500_000, currency: 'USD' },
          target: { amount_minor: 1_800_000, currency: 'USD' },
          percent_complete: 83,
          target_date: null,
        },
        spending_insights: {
          this_month: { amount_minor: 45_000, currency: 'USD' },
          last_month: { amount_minor: 30_000, currency: 'USD' },
          change_percent: 50,
          top_merchants: [
            { merchant: 'Whole Foods', amount: { amount_minor: 25_000, currency: 'USD' } },
            { merchant: 'Other', amount: { amount_minor: 20_000, currency: 'USD' } },
          ],
        },
        savings_rate: {
          percent: 65,
          monthly_income: { amount_minor: 600_000, currency: 'USD' },
          average_monthly_spending: { amount_minor: 210_000, currency: 'USD' },
        },
        budget_summary: {
          envelope_count: 3,
          over_count: 1,
          warning_count: 1,
          total_budgeted: { amount_minor: 200_000, currency: 'USD' },
          total_spent: { amount_minor: 150_000, currency: 'USD' },
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('The Demo Family');
    // Emergency fund: coverage vs target, status, and the dollar gap.
    expect(text).toContain('1.0 months');
    expect(text).toContain('of 6 recommended');
    expect(text).toContain('Getting started');
    expect(text).toContain('USD 10,480.00 more to reach the 6-month goal');
    // Cash flow, assets, and debt cards.
    expect(text).toContain('USD 3,920.00');
    expect(text).toContain('USD 6,000.00 income');
    expect(text).toContain('Retirement');
    expect(text).toContain('USD 300,000.00');
    // Upcoming bills card.
    expect(text).toContain('Upcoming bills');
    expect(text).toContain('Internet');
    expect(text).toContain('Due in 3 days');
    // Net-worth sparkline + change over the snapshot window.
    const host = fixture.nativeElement as HTMLElement;
    const sparkline = host.querySelector('.overview__sparkline polyline');
    expect(sparkline?.getAttribute('points')?.split(' ').length).toBe(3);
    expect(text).toContain('over 3 snapshots');
    // Top-goal progress bar filled to percent_complete.
    expect(text).toContain('Emergency fund');
    const fill = host.querySelector('.overview__progress-fill') as HTMLElement;
    expect(fill.style.width).toBe('83%');
    // Spending insights: this-month total, % change, and top merchants.
    expect(text).toContain('Spending this month');
    expect(text).toContain('USD 450.00');
    expect(text).toContain('50% vs last month');
    expect(text).toContain('Whole Foods');
    // Savings rate on the cash-flow card.
    expect(text).toContain('Savings rate 65%');
    // Budget summary: over-budget count leads.
    expect(text).toContain('1 over budget');
    expect(text).toContain('3 envelopes');
  });

  it('links to the Bills page when there are no bills to measure against', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        emergency_fund: {
          months: null,
          reserved: { amount_minor: 200_000, currency: 'USD' },
          using_designations: true,
          monthly_expenses: { amount_minor: 0, currency: 'USD' },
          target_months_min: 3,
          target_months_recommended: 6,
          status: 'no_bills',
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Add bills to measure');
    const link = host.querySelector('a[href="/bills"]');
    expect(link?.textContent).toContain('Add your recurring bills');
  });

  it('renders an error message when the request fails', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response(undefined, { error: { code: 'http_error', message: 'Failed to load' } }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const errorEl = (fixture.nativeElement as HTMLElement).querySelector('.page-error');
    expect(errorEl?.textContent).toContain('Failed to load');
  });

  it('lets an owner adjust the emergency-fund target (M43)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: 4,
        emergency_fund: {
          months: 4,
          reserved: { amount_minor: 800_000, currency: 'USD' },
          using_designations: true,
          monthly_expenses: { amount_minor: 200_000, currency: 'USD' },
          target_months_min: 3,
          target_months_recommended: 6,
          gap_to_recommended: { amount_minor: 400_000, currency: 'USD' },
          status: 'on_track',
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    (host.querySelector('.overview__target-edit') as HTMLButtonElement).click();
    fixture.detectChanges();
    const input = host.querySelector('.overview__target-label input') as HTMLInputElement;
    input.value = '3';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    const saveBtn = [...host.querySelectorAll('.overview__target-editor button')].find(
      (b) => b.textContent?.trim() === 'Save',
    ) as HTMLButtonElement;
    saveBtn.click();
    await fixture.whenStable();

    expect(apiMock.updateHousehold).toHaveBeenCalledWith({ emergency_fund_target_months: 3 });
  });

  it('hides the target editor for a viewer', async () => {
    TestBed.resetTestingModule();
    configure('viewer');
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: 4,
        emergency_fund: {
          months: 4,
          reserved: { amount_minor: 800_000, currency: 'USD' },
          using_designations: true,
          monthly_expenses: { amount_minor: 200_000, currency: 'USD' },
          target_months_min: 3,
          target_months_recommended: 6,
          gap_to_recommended: { amount_minor: 400_000, currency: 'USD' },
          status: 'on_track',
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('.overview__target-edit'),
    ).toBeNull();
  });
});
