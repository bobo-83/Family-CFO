import { Router, provideRouter } from '@angular/router';
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
    listAccounts: ReturnType<typeof vi.fn>;
    declareSavingsContribution: ReturnType<typeof vi.fn>;
    deleteSavingsContribution: ReturnType<typeof vi.fn>;
    dismissSavingsContribution: ReturnType<typeof vi.fn>;
    updateSavingsContribution: ReturnType<typeof vi.fn>;
    listGoals: ReturnType<typeof vi.fn>;
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
      // #203: only fetched once the declare form opens.
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            { id: 'a1', name: 'Joint Checking', type: 'checking', balance: { amount_minor: 0, currency: 'USD' } },
            { id: 'a2', name: 'College 529', type: '529', balance: { amount_minor: 0, currency: 'USD' } },
          ],
        }),
      ),
      declareSavingsContribution: vi.fn().mockResolvedValue(response({})),
      deleteSavingsContribution: vi.fn().mockResolvedValue(response(undefined)),
      dismissSavingsContribution: vi.fn().mockResolvedValue(response(undefined)),
      // #4: linking a contribution to the goal it funds.
      updateSavingsContribution: vi.fn().mockResolvedValue(response({})),
      // #4: only fetched once a row is linked or carries a suggestion.
      listGoals: vi.fn().mockResolvedValue(
        response({
          goals: [
            {
              id: 'g-college',
              name: 'College fund',
              type: 'college',
              target: { amount_minor: 2_000_000, currency: 'USD' },
              current: { amount_minor: 500_000, currency: 'USD' },
              priority: 1,
            },
          ],
        }),
      ),
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
          spending: { amount_minor: 208_000, currency: 'USD' },
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
    expect(text).toContain('USD 6,000.00 in');
    expect(text).toContain('USD 2,080.00 spent');
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
    expect(text).toContain('3 budgets');
  });

  // #201: detected recurring saving, below the savings-rate line it qualifies.
  it('renders detected savings contributions with a normalised monthly total', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: [
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 4,
            last_seen: '2026-07-01',
          },
          {
            destination_name: 'Fidelity Brokerage',
            destination_type: 'brokerage',
            amount: { amount_minor: 120_000, currency: 'USD' },
            frequency: 'quarterly',
            monthly_equivalent: { amount_minor: 40_000, currency: 'USD' },
            occurrences: 3,
            last_seen: '2026-06-15',
          },
          {
            destination_name: 'Rainy Day Savings',
            destination_type: 'savings',
            amount: { amount_minor: 120_000, currency: 'USD' },
            frequency: 'annual',
            monthly_equivalent: { amount_minor: 10_000, currency: 'USD' },
            occurrences: 1,
            last_seen: '2026-01-04',
          },
        ],
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain("What you're saving");
    expect(text).toContain('College 529');
    expect(text).toContain('USD 500.00 monthly · seen 4 times');
    expect(text).toContain('USD 1,200.00 quarterly · seen 3 times');
    // Singular reads naturally, and "annual" is spoken as "yearly".
    expect(text).toContain('USD 1,200.00 yearly · seen 1 time');
    // 500 + 400 + 100 monthly-equivalent — never the raw 500 + 1200 + 1200.
    expect(text).toContain('About USD 1,000.00 a month');
    expect(text).toContain(
      "Detected from transfers between your accounts. Payroll deductions like a 401(k) don't appear here.",
    );
  });

  // #207: the destination account was never synced, so only the outflow was seen.
  it('marks inferred savings rows and explains the marker once (#207)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: [
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 4,
            last_seen: '2026-07-01',
            inferred: true,
          },
          {
            destination_name: 'Rainy Day Savings',
            destination_type: 'savings',
            amount: { amount_minor: 20_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 20_000, currency: 'USD' },
            occurrences: 6,
            last_seen: '2026-07-02',
            inferred: false,
          },
        ],
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const markers = host.querySelectorAll('.overview__inferred');
    expect(markers.length).toBe(1);
    expect(markers[0].textContent?.trim()).toBe('inferred');
    // The marker sits with the destination it qualifies, not the amount.
    expect(markers[0].closest('.overview__bill')?.textContent).toContain('College 529');
    const text = host.textContent ?? '';
    expect(text).toContain(
      "Rows marked inferred were matched from the money leaving your account — the destination isn't synced.",
    );
    // The payroll footnote is unchanged.
    expect(text).toContain(
      "Detected from transfers between your accounts. Payroll deductions like a 401(k) don't appear here.",
    );
  });

  it('shows no inferred marker or footnote when both legs were seen (#207)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: [
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 4,
            last_seen: '2026-07-01',
            inferred: false,
          },
          {
            destination_name: 'Rainy Day Savings',
            destination_type: 'savings',
            amount: { amount_minor: 20_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 20_000, currency: 'USD' },
            occurrences: 6,
            last_seen: '2026-07-02',
            inferred: false,
          },
        ],
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelectorAll('.overview__inferred').length).toBe(0);
    expect(host.textContent).not.toContain('the destination isn’t synced');
    expect(host.textContent).not.toContain("the destination isn't synced");
    expect(host.textContent).toContain("What you're saving");
  });

  /**
   * #203 replaces #201's hide-when-empty: two detector iterations found nothing
   * when the destination account never syncs, so an empty section
   * has to offer the declaration rather than vanish.
   */
  it('leads the empty savings section with the declare action (#203)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: [],
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain("What you're saving");
    expect(host.querySelector('.overview__declare-open')).toBeTruthy();
    expect(host.textContent).toContain('Nothing detected.');
    // Nothing was detected, so there is no run-rate to sum.
    expect(host.querySelector('.overview__saving-total')).toBeNull();
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

  it('hands the advisor a grounded month question from the year chart (ADR 0068)', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response(undefined, { error: { code: 'http_error', message: 'Failed to load' } }),
    );
    const fixture = TestBed.createComponent(Overview);
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);

    fixture.componentInstance['askAboutMonth']('2026-04', 'income');
    expect(navigate).toHaveBeenCalledWith(['/chat'], {
      queryParams: { ask: 'What made up my income in April 2026? List where the money came from.' },
    });

    fixture.componentInstance['askAboutMonth']('2026-06', 'spending');
    expect(navigate).toHaveBeenCalledWith(['/chat'], {
      queryParams: {
        ask: 'What made up my spending in June 2026? Break it down by category and biggest merchants.',
      },
    });
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

  // #10 phase 1: one language per household; the advisor answers in it.
  describe('household language (#10)', () => {
    function contextInEnglish() {
      return response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        language: 'en',
      });
    }

    async function render() {
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('changes the language and reloads the overview', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextInEnglish());

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__language')).toBeTruthy();
      expect(host.textContent).toContain(
        'The advisor answers in this language. Screens follow in a later update.',
      );

      await fixture.componentInstance['changeLanguage']('vi');
      await fixture.whenStable();

      expect(apiMock.updateHousehold).toHaveBeenCalledWith({ language: 'vi' });
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('surfaces the server message and reverts when the change fails', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextInEnglish());
      apiMock.updateHousehold.mockResolvedValue(
        response(undefined, { error: { message: 'Unsupported language; supported: en, vi, lt.' } }),
      );

      const fixture = await render();
      const component = fixture.componentInstance;
      await component['changeLanguage']('vi');
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain(
        'Unsupported language; supported: en, vi, lt.',
      );
      // The select falls back to the context value — the change never took.
      expect(component['languageValue']({ language: 'en' } as never)).toBe('en');
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('shows the language read-only for a viewer', async () => {
      TestBed.resetTestingModule();
      configure('viewer');
      apiMock.getHouseholdContext.mockResolvedValue(contextInEnglish());

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__language')).toBeNull();
      expect(host.querySelector('.overview__language-readonly')?.textContent).toContain('English');
    });
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
            amount: { amount_minor: 251_234, currency: 'USD' },
            kind: 'income',
          },
        ],
        ending_cash: { amount_minor: 1_198_054, currency: 'USD' },
        lowest_balance: { amount_minor: 914_976, currency: 'USD' },
        lowest_date: '2026-07-21',
        expected_income: { amount_minor: 251_234, currency: 'USD' },
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
    expect(text).toContain('Cash covers everything due in the next 30 days');
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
        first_shortfall_date: '2026-08-14',
        shortfall: { amount_minor: 418_183, currency: 'USD' },
        sell_by_date: '2026-08-10',
        runway_action: 'sell_rsus',
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
    expect(text).toContain('Sell RSUs by');
    expect(text).toContain('raise at least USD 4,181.83');
    expect(text).not.toContain('covered');
    expect(text).toContain('-USD 4,181.83'); // the lowest point, shown negative
  });

  it('shows the vested-RSU line beside the stress test when tagged accounts exist', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        safe_to_spend: {
          safe_to_spend: { amount_minor: 120_000, currency: 'USD' },
          liquid_balance: { amount_minor: 500_000, currency: 'USD' },
          emergency_fund_reserved: { amount_minor: 200_000, currency: 'USD' },
          bills_due: { amount_minor: 100_000, currency: 'USD' },
          minimum_debt_payments: { amount_minor: 80_000, currency: 'USD' },
          total_debt: { amount_minor: 0, currency: 'USD' },
          warnings: [],
          ready_to_sell: {
            value: { amount_minor: 8_400_000, currency: 'USD' },
            accounts: [
              { name: 'Employer stock plan', amount: { amount_minor: 8_400_000, currency: 'USD' } },
            ],
            sale_notice_business_days: 4,
          },
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Ready to sell: USD 84,000.00 in vested RSUs');
    expect(text).toContain('about 4 business days to become cash');
    expect(text).toContain('Employer stock plan');
    expect(text).toContain('Not part of the number above.');
  });

  it('hides the vested-RSU line when no account is tagged', async () => {
    apiMock.getHouseholdContext.mockResolvedValue(
      response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        safe_to_spend: {
          safe_to_spend: { amount_minor: 120_000, currency: 'USD' },
          liquid_balance: { amount_minor: 500_000, currency: 'USD' },
          emergency_fund_reserved: { amount_minor: 200_000, currency: 'USD' },
          bills_due: { amount_minor: 100_000, currency: 'USD' },
          minimum_debt_payments: { amount_minor: 80_000, currency: 'USD' },
          total_debt: { amount_minor: 0, currency: 'USD' },
          warnings: [],
        },
      }),
    );

    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Stress test');
    expect(host.querySelector('.overview__ready-to-sell')).toBeNull();
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

  // #203: a declaration outranks detection, and a detected route can be denied.
  describe('declaring and dismissing savings contributions (#203)', () => {
    function contextWith(contributions: unknown[]) {
      return response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: contributions,
      });
    }

    async function render() {
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('declares a contribution and reloads the overview', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextWith([]));

      const fixture = await render();
      const component = fixture.componentInstance;
      component['startDeclaring']();
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      // The account list is only fetched once the form is open.
      expect(apiMock.listAccounts).toHaveBeenCalled();
      expect((fixture.nativeElement as HTMLElement).querySelector('.overview__declare')).toBeTruthy();

      component['declareForm'].setValue({
        sourceAccountId: 'a1',
        destinationAccountId: 'a2',
        amount: 500,
        frequency: 'monthly',
      });
      await component['declareContribution']('USD');
      await fixture.whenStable();

      expect(apiMock.declareSavingsContribution).toHaveBeenCalledWith({
        source_account_id: 'a1',
        destination_account_id: 'a2',
        amount: { amount_minor: 50_000, currency: 'USD' },
        frequency: 'monthly',
      });
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('will not declare an incomplete contribution', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextWith([]));

      const fixture = await render();
      const component = fixture.componentInstance;
      component['startDeclaring']();
      await component['declareContribution']('USD');

      expect(apiMock.declareSavingsContribution).not.toHaveBeenCalled();
    });

    it('marks a declared row and stops tracking it on request', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 0,
            last_seen: '2026-08-01',
            declared: true,
            contribution_id: 'sc1',
            source_account_id: 'a1',
            destination_account_id: 'a2',
          },
        ]),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__declared')?.textContent?.trim()).toBe('you told us');
      // A declared row is a stated fact, so it never quotes an evidence count.
      expect(host.textContent).toContain('USD 500.00 monthly');
      expect(host.textContent).not.toContain('seen 0 times');

      const action = host.querySelector('.overview__saving-action') as HTMLButtonElement;
      expect(action.textContent?.trim()).toBe('Stop tracking');
      action.click();
      await fixture.whenStable();

      expect(apiMock.deleteSavingsContribution).toHaveBeenCalledWith('sc1');
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('dismisses a detected route that is not saving', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          {
            destination_name: 'Rainy Day Savings',
            destination_type: 'savings',
            amount: { amount_minor: 20_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 20_000, currency: 'USD' },
            occurrences: 6,
            last_seen: '2026-07-02',
            source_account_id: 'a1',
            destination_account_id: 'a3',
          },
        ]),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      const action = host.querySelector('.overview__saving-action') as HTMLButtonElement;
      expect(action.textContent?.trim()).toBe('Not saving');
      action.click();
      await fixture.whenStable();

      expect(apiMock.dismissSavingsContribution).toHaveBeenCalledWith({
        source_account_id: 'a1',
        destination_account_id: 'a3',
      });
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    // Only the arrival synced, so there is no route to name in the request.
    it('offers no dismissal when the funding side is unknown', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          {
            destination_name: 'Rainy Day Savings',
            destination_type: 'savings',
            amount: { amount_minor: 20_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 20_000, currency: 'USD' },
            occurrences: 6,
            last_seen: '2026-07-02',
            source_account_id: '',
            destination_account_id: 'a3',
          },
        ]),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__saving-action')).toBeNull();
    });

    it('surfaces the server message when declaring fails', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextWith([]));
      apiMock.declareSavingsContribution.mockResolvedValue(
        response(undefined, { error: { message: 'That account cannot fund itself.' } }),
      );

      const fixture = await render();
      const component = fixture.componentInstance;
      component['startDeclaring']();
      component['declareForm'].setValue({
        sourceAccountId: 'a1',
        destinationAccountId: 'a1',
        amount: 500,
        frequency: 'monthly',
      });
      await component['declareContribution']('USD');
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain(
        'That account cannot fund itself.',
      );
      // A failed declaration leaves the form open with the values intact.
      expect(component['declaring']()).toBe(true);
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('surfaces the server message when stopping tracking fails', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 0,
            last_seen: '2026-08-01',
            declared: true,
            contribution_id: 'sc1',
          },
        ]),
      );
      apiMock.deleteSavingsContribution.mockResolvedValue(
        response(undefined, { error: { message: 'That contribution is already gone.' } }),
      );

      const fixture = await render();
      const component = fixture.componentInstance;
      await component['stopTracking']({
        destination_name: 'College 529',
        destination_type: '529',
        amount: { amount_minor: 50_000, currency: 'USD' },
        frequency: 'monthly',
        monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
        occurrences: 0,
        last_seen: '2026-08-01',
        declared: true,
        contribution_id: 'sc1',
      });
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain(
        'That contribution is already gone.',
      );
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('hides both actions from a viewer', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          {
            destination_name: 'College 529',
            destination_type: '529',
            amount: { amount_minor: 50_000, currency: 'USD' },
            frequency: 'monthly',
            monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
            occurrences: 0,
            last_seen: '2026-08-01',
            declared: true,
            contribution_id: 'sc1',
            source_account_id: 'a1',
            destination_account_id: 'a2',
          },
        ]),
      );
      TestBed.resetTestingModule();
      configure('viewer');

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__saving-action')).toBeNull();
      expect(host.querySelector('.overview__declare-open')).toBeNull();
    });
  });

  // #4: contributions link to the goals they fund.
  describe('goal funding links (#4)', () => {
    function declaredRow(extra: Record<string, unknown>) {
      return {
        destination_name: 'College 529',
        destination_type: '529',
        amount: { amount_minor: 50_000, currency: 'USD' },
        frequency: 'monthly',
        monthly_equivalent: { amount_minor: 50_000, currency: 'USD' },
        occurrences: 0,
        last_seen: '2026-08-01',
        declared: true,
        contribution_id: 'sc1',
        source_account_id: 'a1',
        destination_account_id: 'a2',
        ...extra,
      };
    }

    function contextWith(contributions: unknown[]) {
      return response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        savings_contributions: contributions,
      });
    }

    async function render() {
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      // The goal-name lookup only starts once the context reveals a link or
      // suggestion, so flush a second round for its resource.
      await new Promise((resolve) => setTimeout(resolve));
      fixture.detectChanges();
      return fixture;
    }

    it('offers a one-tap chip on a suggested row, links it, and reloads', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([declaredRow({ suggested_goal_id: 'g-college' })]),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;

      // The name came from the lazily fetched goals list.
      expect(apiMock.listGoals).toHaveBeenCalled();
      const chip = host.querySelector('.overview__goal-chip') as HTMLButtonElement;
      expect(chip.textContent?.trim()).toBe('Fund College fund?');

      chip.click();
      await fixture.whenStable();

      expect(apiMock.updateSavingsContribution).toHaveBeenCalledWith('sc1', 'g-college');
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('shows the linked goal on the row and unlinks it', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([declaredRow({ goal_id: 'g-college' })]),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;

      const link = host.querySelector('.overview__goal-link');
      expect(link?.textContent).toContain('→ College fund');
      expect(link?.getAttribute('title')).toBe('funds College fund');
      // A linked row offers no suggestion chip.
      expect(host.querySelector('.overview__goal-chip')).toBeNull();

      const unlink = host.querySelector('.overview__goal-unlink') as HTMLButtonElement;
      expect(unlink.getAttribute('aria-label')).toBe('Unlink from College fund');
      unlink.click();
      await fixture.whenStable();

      expect(apiMock.updateSavingsContribution).toHaveBeenCalledWith('sc1', null);
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('surfaces the server message when linking fails', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([declaredRow({ suggested_goal_id: 'g-college' })]),
      );
      apiMock.updateSavingsContribution.mockResolvedValue(
        response(undefined, { error: { message: 'That goal is gone.' } }),
      );

      const fixture = await render();
      (
        (fixture.nativeElement as HTMLElement).querySelector(
          '.overview__goal-chip',
        ) as HTMLButtonElement
      ).click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain('That goal is gone.');
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('still names the linked goal for a viewer, but offers no chip or unlink', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith([
          declaredRow({ goal_id: 'g-college' }),
          declaredRow({ contribution_id: 'sc2', suggested_goal_id: 'g-college' }),
        ]),
      );
      TestBed.resetTestingModule();
      configure('viewer');

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__goal-link')?.textContent).toContain('→ College fund');
      expect(host.querySelector('.overview__goal-chip')).toBeNull();
      expect(host.querySelector('.overview__goal-unlink')).toBeNull();
    });
  });
});
