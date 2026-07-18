import { provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
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
    getCashOutlook: ReturnType<typeof vi.fn>;
    getSpendingPlan: ReturnType<typeof vi.fn>;
  };

  function configure(role = 'owner') {
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock(role) },
      ],
    });
  }

  beforeEach(() => {
    apiMock = {
      getHouseholdContext: vi.fn(),
      updateHousehold: vi.fn().mockResolvedValue(response({})),
      // M112: every overview load also fetches the cash outlook; default to
      // "no data" so existing tests render without the card.
      getCashOutlook: vi.fn().mockResolvedValue(response(null)),
      getSpendingPlan: vi.fn().mockResolvedValue(response(null)),
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

  it('renders the cash outlook with the lowest point and day-by-day rows (M112)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
      }),
    );
    apiMock.getCashOutlook.mockResolvedValue(
      response({
        starting_cash: { amount_minor: 1_632_600, currency: 'USD' },
        events: [
          {
            occurred_on: '2026-07-21',
            name: 'Costco Visa',
            amount: { amount_minor: -717_624, currency: 'USD' },
            kind: 'credit_card',
          },
          {
            occurred_on: '2026-07-30',
            name: 'Paycheck',
            amount: { amount_minor: 283_078, currency: 'USD' },
            kind: 'income',
          },
        ],
        ending_cash: { amount_minor: 1_198_054, currency: 'USD' },
        lowest_balance: { amount_minor: 914_976, currency: 'USD' },
        lowest_date: '2026-07-21',
        expected_income: { amount_minor: 283_078, currency: 'USD' },
        obligations: { amount_minor: 717_624, currency: 'USD' },
        horizon_days: 30,
        due_soon: { amount_minor: 825_400, currency: 'USD' },
        due_soon_covered: true,
        due_soon_window_days: 14,
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Cash outlook');
    // Verdict tracks the 30-day projection: a positive lowest point => positive.
    expect(text).toContain('stays positive over the next 30 days');
    expect(text).toContain('USD 9,149.76'); // the lowest point
    // Day-by-day rows carry the running balance beside each event.
    const rows = host.querySelectorAll('.outlook-card__table tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Costco Visa');
    expect(rows[0].textContent).toContain('USD 9,149.76');
    // Safe-to-spend is reframed as the stress test, not a spending allowance.
    expect(text).not.toContain('Safe to spend');
  });

  it('shows a shortfall verdict — never "covered" — when the outlook goes negative', async () => {
    // Regression: the card once read "covered ✓" (a 14-day due-vs-cash check)
    // while the 30-day math projected the balance thousands negative, because a
    // large credit-card payment landed 15-30 days out. The verdict must track
    // the projection's own lowest point.
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
      }),
    );
    apiMock.getCashOutlook.mockResolvedValue(
      response({
        starting_cash: { amount_minor: 1_957_745, currency: 'USD' },
        events: [
          {
            occurred_on: '2026-08-14',
            name: 'Amex Platinum',
            amount: { amount_minor: -1_218_241, currency: 'USD' },
            kind: 'credit_card',
          },
          {
            occurred_on: '2026-08-14',
            name: 'Paycheck',
            amount: { amount_minor: 283_079, currency: 'USD' },
            kind: 'income',
          },
        ],
        ending_cash: { amount_minor: -177_932, currency: 'USD' },
        lowest_balance: { amount_minor: -418_183, currency: 'USD' },
        lowest_date: '2026-08-14',
        expected_income: { amount_minor: 647_110, currency: 'USD' },
        obligations: { amount_minor: 2_782_787, currency: 'USD' },
        horizon_days: 30,
        // The 14-day check still reports "covered" — the card must NOT trust it.
        due_soon: { amount_minor: 1_144_257, currency: 'USD' },
        due_soon_covered: true,
        due_soon_window_days: 14,
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('runs short over the next 30 days');
    expect(text).not.toContain('covered');
    expect(text).toContain('-USD 4,181.83'); // the lowest point, shown negative
  });

  it('renders the month spending plan (M113)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
      }),
    );
    apiMock.getSpendingPlan.mockResolvedValue(
      response({
        month: '2026-07',
        income_received: { amount_minor: 401_000, currency: 'USD' },
        income_projected: { amount_minor: 324_100, currency: 'USD' },
        expected_income: { amount_minor: 725_100, currency: 'USD' },
        spent: { amount_minor: 300_000, currency: 'USD' },
        bills_remaining: { amount_minor: 3_800, currency: 'USD' },
        account_obligations: { amount_minor: 100_000, currency: 'USD' },
        planned_savings: { amount_minor: 0, currency: 'USD' },
        left_to_spend: { amount_minor: 321_300, currency: 'USD' },
        per_day: { amount_minor: 21_420, currency: 'USD' },
        days_remaining: 15,
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Left to spend this month');
    expect(text).toContain('USD 3,213.00');
    expect(text).toContain('USD 214.20/day for the remaining 15 days');
    expect(text).toContain('USD 4,010.00 received');
  });
});
