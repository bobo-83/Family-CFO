import { Router, provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Overview } from './overview';
import { handleSessionResponse } from '../../core/api-client-setup';
import { HouseholdCurrencyService } from '../../core/household-currency.service';
import { authState, clearAuthState, setAuthState } from '../../core/token-store';
import { TIMEZONE_BOX_DEFAULT } from '../../shared/timezones';
import { qualifyApiFixture } from '../../shared/testing-qualified-fixtures';

function response(data: unknown, error?: unknown) {
  return {
    data: qualifyApiFixture(data),
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
    getHouseholdKeyStatus: ReturnType<typeof vi.fn>;
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
      // #96: the sealed-mode offer only appears when the box answers; default
      // to "no answer" so every other test renders without it.
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
    };
    localStorage.removeItem('family-cfo.hideSealedModeOffer');
    setAuthState({ accessToken: 'token-a', householdId: 'hh-a', userId: 'u1', role: 'owner' });
    configure();
  });

  afterEach(() => clearAuthState());

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
          gross_income: { amount_minor: 850_000, currency: 'USD' },
          transfers: { amount_minor: 50_000, currency: 'USD' },
          payroll_deductions: { amount_minor: 250_000, currency: 'USD' },
          residual: { amount_minor: 30_000, currency: 'USD' },
          total_saved: { amount_minor: 330_000, currency: 'USD' },
          payroll_profile_present: true,
          declared_transfers_present: true,
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
    // #6: savings rate on the cash-flow card, as observed saving from three
    // sources with the total saved and each non-zero component.
    expect(text).toContain('Saving 65%');
    expect(text).toContain('USD 3,300.00/mo saved');
    expect(text).toContain('USD 500.00 transfers');
    expect(text).toContain('USD 2,500.00 payroll (401k/HSA)');
    expect(text).toContain('USD 300.00 residual');
    // Budget summary: over-budget count leads.
    expect(text).toContain('1 over budget');
    expect(text).toContain('3 budgets');
  });

  // #6: the observed savings rate — three sources, and an honest note when
  // pre-tax payroll saving is undeclared and so invisible to the rate.
  describe('observed savings rate (#6)', () => {
    function contextWithSavingsRate(rate: Record<string, unknown>) {
      return response({
        household_id: 'h1',
        display_name: 'The Demo Family',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        monthly_cash_flow: {
          income: { amount_minor: 600_000, currency: 'USD' },
          spending: { amount_minor: 210_000, currency: 'USD' },
          net: { amount_minor: 390_000, currency: 'USD' },
        },
        savings_rate: rate,
      });
    }

    it('shows the payroll-absent note when payroll_profile_present is false', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWithSavingsRate({
          percent: 40,
          monthly_income: { amount_minor: 600_000, currency: 'USD' },
          average_monthly_spending: { amount_minor: 210_000, currency: 'USD' },
          transfers: { amount_minor: 50_000, currency: 'USD' },
          payroll_deductions: { amount_minor: 0, currency: 'USD' },
          residual: { amount_minor: 30_000, currency: 'USD' },
          total_saved: { amount_minor: 80_000, currency: 'USD' },
          payroll_profile_present: false,
          declared_transfers_present: true,
        }),
      );

      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const host = fixture.nativeElement as HTMLElement;
      const note = host.querySelector('.overview__savings-note');
      expect(note).toBeTruthy();
      expect(note?.textContent).toContain('Payroll 401(k)/HSA not declared');
      // The zero payroll component is dropped from the breakdown.
      expect(host.textContent).toContain('USD 500.00 transfers');
      expect(host.textContent).not.toContain('payroll (401k/HSA)');
    });

    it('hides the payroll-absent note when payroll_profile_present is true', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWithSavingsRate({
          percent: 65,
          monthly_income: { amount_minor: 600_000, currency: 'USD' },
          average_monthly_spending: { amount_minor: 210_000, currency: 'USD' },
          transfers: { amount_minor: 50_000, currency: 'USD' },
          payroll_deductions: { amount_minor: 250_000, currency: 'USD' },
          residual: { amount_minor: 30_000, currency: 'USD' },
          total_saved: { amount_minor: 330_000, currency: 'USD' },
          payroll_profile_present: true,
          declared_transfers_present: true,
        }),
      );

      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__savings-note')).toBeNull();
      expect(host.textContent).toContain('USD 2,500.00 payroll (401k/HSA)');
    });
  });

  // #201: detected recurring saving, below the savings-rate line it qualifies.
  it('renders detected savings contributions without recomputing a client total', async () => {
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
    expect(text).not.toContain('About USD 1,000.00 a month');
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

  // #96 (ADR 0072 Phase 3): the sealed-mode offer on the household card.
  describe('sealed mode offer (#96)', () => {
    function keyStatus(overrides: Record<string, unknown> = {}) {
      return {
        encryption_enabled: true,
        member_wraps: 2,
        device_wraps: 1,
        has_recovery_key: true,
        recovery_key_created_at: '2026-07-01T12:00:00Z',
        mode: 'convenient',
        unlocked: true,
        ...overrides,
      };
    }

    async function render() {
      apiMock.getHouseholdContext.mockResolvedValue(
        response({
          household_id: 'h1',
          display_name: 'Home',
          currency: 'USD',
          net_worth: { amount_minor: 0, currency: 'USD' },
          emergency_fund_months: null,
        }),
      );
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    afterEach(() => {
      localStorage.removeItem('family-cfo.hideSealedModeOffer');
    });

    it('offers sealing, with the trade stated, when the household could seal', async () => {
      apiMock.getHouseholdKeyStatus.mockResolvedValue(response(keyStatus()));

      const fixture = await render();

      const offer = (fixture.nativeElement as HTMLElement).querySelector('.overview__seal');
      expect(offer).not.toBeNull();
      const text = offer?.textContent ?? '';
      // What it does, why you'd want it, and why it is not the default.
      expect(text).toContain('a stolen disk, a backup archive, a snapshot');
      expect(text).toContain('without a session');
      expect(text).toContain('off by default');
      expect(text).toContain('bank sync');
      // No unmet precondition, so no "before you can seal" line.
      expect(offer?.querySelector('.overview__seal-todo')).toBeNull();
      // It links to where sealing actually happens.
      expect(offer?.querySelector('a')?.getAttribute('href')).toBe('/backups');
    });

    it('says which precondition is missing instead of hiding the offer', async () => {
      apiMock.getHouseholdKeyStatus.mockResolvedValue(
        response(keyStatus({ has_recovery_key: false, recovery_key_created_at: null, member_wraps: 0 })),
      );

      const fixture = await render();

      const todos = Array.from(
        (fixture.nativeElement as HTMLElement).querySelectorAll('.overview__seal-todo'),
      ).map((node) => node.textContent ?? '');
      expect(todos.length).toBe(2);
      expect(todos.join(' ')).toContain('create a recovery key');
      expect(todos.join(' ')).toContain('sign in with your password once');
    });

    it('stays quiet for a household that is already sealed', async () => {
      apiMock.getHouseholdKeyStatus.mockResolvedValue(response(keyStatus({ mode: 'sealed' })));

      const fixture = await render();

      expect((fixture.nativeElement as HTMLElement).querySelector('.overview__seal')).toBeNull();
    });

    it('stays quiet when the box does not encrypt per household', async () => {
      apiMock.getHouseholdKeyStatus.mockResolvedValue(
        response(keyStatus({ encryption_enabled: false, has_recovery_key: false, member_wraps: 0 })),
      );

      const fixture = await render();

      expect((fixture.nativeElement as HTMLElement).querySelector('.overview__seal')).toBeNull();
    });

    it('never asks a member who cannot seal', async () => {
      TestBed.resetTestingModule();
      configure('adult');
      apiMock.getHouseholdKeyStatus.mockResolvedValue(response(keyStatus()));

      const fixture = await render();

      expect((fixture.nativeElement as HTMLElement).querySelector('.overview__seal')).toBeNull();
      expect(apiMock.getHouseholdKeyStatus).not.toHaveBeenCalled();
    });

    it('stays dismissed on this device once dismissed', async () => {
      apiMock.getHouseholdKeyStatus.mockResolvedValue(response(keyStatus()));

      const fixture = await render();
      const dismiss = (fixture.nativeElement as HTMLElement).querySelectorAll(
        '.overview__seal-actions button',
      )[0] as HTMLButtonElement;
      dismiss.click();
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).querySelector('.overview__seal')).toBeNull();
      expect(localStorage.getItem('family-cfo.hideSealedModeOffer')).toBe('true');

      // A fresh render on the same device — the reload a nag would survive.
      TestBed.resetTestingModule();
      configure();
      const again = await render();
      expect((again.nativeElement as HTMLElement).querySelector('.overview__seal')).toBeNull();
    });
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

  // #41: the household's own zone decides what "today" means.
  describe('household time zone (#41)', () => {
    function contextInZone(timezone: string | null) {
      return response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        language: 'en',
        timezone,
      });
    }

    async function render() {
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('changes the zone and reloads the overview', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextInZone('America/New_York'));

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__timezone')).toBeTruthy();
      expect(host.textContent).toContain(
        'Bills, due dates and Safe to Spend use this zone to decide what “today” means.',
      );

      await fixture.componentInstance['changeTimezone']('Europe/London');
      await fixture.whenStable();

      expect(apiMock.updateHousehold).toHaveBeenCalledWith({ timezone: 'Europe/London' });
      // Every date on the page was computed in the old zone.
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('surfaces the server message and reverts when the zone is unknown', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextInZone('America/New_York'));
      apiMock.updateHousehold.mockResolvedValue(
        response(undefined, { error: { message: 'Unknown timezone' } }),
      );

      const fixture = await render();
      const component = fixture.componentInstance;
      await component['changeTimezone']('Mars/Olympus_Mons');
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain('Unknown timezone');
      // The field falls back to the context value — the change never took.
      expect(component['timezoneValue']({ timezone: 'America/New_York' } as never)).toBe(
        'America/New_York',
      );
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('shows the zone read-only for a viewer', async () => {
      TestBed.resetTestingModule();
      configure('viewer');
      apiMock.getHouseholdContext.mockResolvedValue(contextInZone('Europe/London'));

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__timezone')).toBeNull();
      expect(host.querySelector('.overview__timezone-readonly')?.textContent).toContain(
        'Europe/London',
      );
    });

    it('says so, rather than showing a blank, when no zone is set', async () => {
      TestBed.resetTestingModule();
      configure('viewer');
      apiMock.getHouseholdContext.mockResolvedValue(contextInZone(null));

      const fixture = await render();
      expect(
        (fixture.nativeElement as HTMLElement).querySelector('.overview__timezone-readonly')
          ?.textContent,
      ).toContain("the box's own zone");
    });

    it('clears the zone with the flag, not a null timezone', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(contextInZone('Europe/London'));
      const fixture = await render();
      const component = fixture.componentInstance;

      await component['changeTimezone'](TIMEZONE_BOX_DEFAULT);
      await fixture.whenStable();

      // A null `timezone` would read as "field omitted" on the server.
      expect(apiMock.updateHousehold).toHaveBeenCalledWith({ clear_timezone: true });
      expect(component['timezoneValue']({ timezone: 'Europe/London' } as never)).toBe('');
      // Every date on the page was computed in the old zone.
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

  });

  // #5: committed savings — shown beside Safe to Spend, or reserved like a bill.
  describe('committed savings (#5)', () => {
    function contextWith(safeToSpendExtra: Record<string, unknown>) {
      return response({
        household_id: 'h1',
        display_name: 'Home',
        currency: 'USD',
        net_worth: { amount_minor: 0, currency: 'USD' },
        emergency_fund_months: null,
        language: 'en',
        safe_to_spend: {
          safe_to_spend: { amount_minor: 120_000, currency: 'USD' },
          liquid_balance: { amount_minor: 500_000, currency: 'USD' },
          emergency_fund_reserved: { amount_minor: 200_000, currency: 'USD' },
          bills_due: { amount_minor: 100_000, currency: 'USD' },
          minimum_debt_payments: { amount_minor: 80_000, currency: 'USD' },
          total_debt: { amount_minor: 0, currency: 'USD' },
          warnings: [],
          ...safeToSpendExtra,
        },
      });
    }

    async function render() {
      const fixture = TestBed.createComponent(Overview);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('shows the informational line, not subtracted, when not reserved', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith({
          committed_savings: { amount_minor: 50_000, currency: 'USD' },
          committed_savings_items: [
            { name: 'College 529 — due Aug 12', amount: { amount_minor: 50_000, currency: 'USD' } },
          ],
          committed_savings_reserved: false,
        }),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      const text = host.textContent ?? '';
      expect(host.querySelector('.overview__committed-savings')).toBeTruthy();
      expect(text).toContain('Committed savings (shown, not subtracted): USD 500.00');
      expect(text).toContain('College 529 — due Aug 12');
      expect(text).toContain('Not part of the number above.');
      // Never part of the headline math: the stress-test figure is unchanged
      // and the formula line omits the term.
      expect(text).toContain('USD 1,200.00');
      const formula = Array.from(host.querySelectorAll('.overview__detail')).find((el) =>
        el.textContent?.includes('liquid'),
      );
      expect(formula?.textContent).not.toContain('committed savings');
    });

    it('renders committed savings as a reserved line when reserved', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith({
          committed_savings: { amount_minor: 50_000, currency: 'USD' },
          committed_savings_items: [
            { name: 'College 529 — due Aug 12', amount: { amount_minor: 50_000, currency: 'USD' } },
          ],
          committed_savings_reserved: true,
        }),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      const text = host.textContent ?? '';
      // No informational companion block when reserved.
      expect(host.querySelector('.overview__committed-savings')).toBeNull();
      // Subtracted in the headline formula, and named in the drill-down.
      const formula = Array.from(host.querySelectorAll('.overview__detail')).find((el) =>
        el.textContent?.includes('liquid'),
      );
      expect(formula?.textContent).toContain('− USD 500.00 committed savings');
      expect(text).toContain('Committed savings, reserved like a bill:');
      expect(text).toContain('College 529 — due Aug 12');
    });

    it('toggles the reservation on and reloads', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith({ committed_savings_reserved: false }),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__committed-toggle')).toBeTruthy();

      await fixture.componentInstance['toggleCommittedReserve'](true);
      await fixture.whenStable();

      expect(apiMock.updateHousehold).toHaveBeenCalledWith({ reserve_committed_savings: true });
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(2);
    });

    it('surfaces the server message and reverts when the toggle fails', async () => {
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith({ committed_savings_reserved: false }),
      );
      apiMock.updateHousehold.mockResolvedValue(
        response(undefined, { error: { message: 'Cannot reserve committed savings right now.' } }),
      );

      const fixture = await render();
      const component = fixture.componentInstance;
      await component['toggleCommittedReserve'](true);
      fixture.detectChanges();

      expect((fixture.nativeElement as HTMLElement).textContent).toContain(
        'Cannot reserve committed savings right now.',
      );
      // Reverted: the value falls back to the context's (off).
      expect(
        component['committedReserveValue']({
          safe_to_spend: { committed_savings_reserved: false },
        } as never),
      ).toBe(false);
      expect(apiMock.getHouseholdContext).toHaveBeenCalledTimes(1);
    });

    it('shows the reservation read-only for a viewer', async () => {
      TestBed.resetTestingModule();
      configure('viewer');
      apiMock.getHouseholdContext.mockResolvedValue(
        contextWith({ committed_savings_reserved: true }),
      );

      const fixture = await render();
      const host = fixture.nativeElement as HTMLElement;
      expect(host.querySelector('.overview__committed-toggle')).toBeNull();
      expect(host.querySelector('.overview__committed-readonly')?.textContent).toContain(
        'reserved like a bill',
      );
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
    // Event rows come from the server; the client no longer reconstructs running balances.
    const rows = host.querySelectorAll('.outlook-card__table tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Costco Visa');
    expect(rows[0].textContent).not.toContain('USD 9,149.76');
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

  it('marks only statement-backed outlook rows as exact (#30)', async () => {
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
        starting_cash: { amount_minor: 2_000_000, currency: 'USD' },
        events: [
          {
            occurred_on: '2026-07-21',
            name: 'Costco Visa',
            amount: { amount_minor: -717_624, currency: 'USD' },
            kind: 'credit_card',
            source: 'statement',
          },
          {
            // A running balance with an inferred due day — NOT the final bill.
            occurred_on: '2026-07-24',
            name: 'Amex Platinum',
            amount: { amount_minor: -128_450, currency: 'USD' },
            kind: 'credit_card',
            source: 'estimate',
          },
          {
            // An older box may not send `source` at all: still an estimate.
            occurred_on: '2026-07-30',
            name: 'Paycheck',
            amount: { amount_minor: 251_234, currency: 'USD' },
            kind: 'income',
          },
        ],
        ending_cash: { amount_minor: 1_405_160, currency: 'USD' },
        lowest_balance: { amount_minor: 1_153_926, currency: 'USD' },
        lowest_date: '2026-07-24',
        expected_income: { amount_minor: 251_234, currency: 'USD' },
        obligations: { amount_minor: 846_074, currency: 'USD' },
        horizon_days: 30,
        due_soon: { amount_minor: 846_074, currency: 'USD' },
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
    const rows = Array.from(host.querySelectorAll('.outlook-card__table tr'));
    expect(rows.length).toBe(3);

    // Exactly one chip — dressing an estimate up as exact is the one thing
    // this must never do.
    expect(host.querySelectorAll('.outlook-card__table .from-statement').length).toBe(1);
    const exact = rows.find((row) => row.textContent?.includes('Costco Visa'))!;
    expect(exact.querySelector('.from-statement')).toBeTruthy();
    for (const name of ['Amex Platinum', 'Paycheck']) {
      const estimated = rows.find((row) => row.textContent?.includes(name))!;
      expect(estimated.querySelector('.from-statement')).toBeFalsy();
    }

    // The footnote points at the chip instead of hedging about every card.
    const text = host.textContent ?? '';
    expect(text).toContain('Rows marked from statement are exact');
    expect(text).not.toContain('A card with a recorded statement shows its exact amount due');
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


// --- #156 (ADR 0075): what the base-currency total leaves out -------------------

describe('Overview #156: accounts outside the base currency', () => {
  afterEach(() => clearAuthState());

  let apiMock: Record<string, ReturnType<typeof vi.fn>>;

  function minimalContext(extra: Record<string, unknown>) {
    return {
      household_id: 'h1',
      display_name: 'The Demo Family',
      currency: 'USD',
      net_worth: { amount_minor: -298_000_000, currency: 'USD' },
      emergency_fund_months: 0,
      ...extra,
    };
  }

  async function render(context: Record<string, unknown>) {
    setAuthState({ accessToken: 'token-a', householdId: 'hh-a', userId: 'u1', role: 'owner' });
    apiMock = {
      getHouseholdContext: vi.fn().mockResolvedValue(response(context)),
      updateHousehold: vi.fn().mockResolvedValue(response({})),
      getCashOutlook: vi.fn().mockResolvedValue(response(null)),
      getSpendingPlan: vi.fn().mockResolvedValue(response(null)),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listGoals: vi.fn().mockResolvedValue(response({ goals: [] })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
    };
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const note = (host: HTMLElement) => host.querySelector('[data-testid="overview-outside-base-currency"]');

  it('names each excluded account with its own currency under the net worth', async () => {
    const host = await render(
      minimalContext({
        accounts_outside_base_currency: [
          { name: 'Euro Savings', balance: { amount_minor: 400_000, currency: 'EUR' } },
          { name: 'Euro Pension', balance: { amount_minor: 900_000, currency: 'EUR' } },
        ],
      }),
    );

    const text = note(host)?.textContent ?? '';
    expect(text).toContain('Not counted in USD totals');
    expect(text).toContain('Euro Savings (EUR 4,000.00)');
    expect(text).toContain('Euro Pension (EUR 9,000.00)');
    // The EUR balances are never shown as USD.
    expect(text).not.toContain('USD 4,000.00');
  });

  it('renders nothing for a single-currency household (an empty list)', async () => {
    const host = await render(minimalContext({ accounts_outside_base_currency: [] }));
    expect(note(host)).toBeNull();
  });

  it('renders nothing for a past month, where the list is null (unknown, not none)', async () => {
    const host = await render(minimalContext({ accounts_outside_base_currency: null }));
    expect(note(host)).toBeNull();
  });
});


// --- #158 review: a delayed Overview response must not cross a session switch ---

describe('Overview qualified aggregate presentation', () => {
  afterEach(() => clearAuthState());

  function yearOverview(year: number) {
    return {
      year,
      months: [],
      total_income: {
        value: { amount_minor: 0, currency: 'USD' },
        incomplete_count: 0,
      },
      total_spending: {
        value: { amount_minor: 0, currency: 'USD' },
        incomplete_count: 0,
      },
      total_net: {
        value: { amount_minor: 0, currency: 'USD' },
        incomplete_count: 0,
      },
      top_categories: [],
      review: null,
    };
  }

  function configureQualified(apiMock: Record<string, unknown>) {
    if (!authState()) {
      setAuthState({ accessToken: 'token-a', householdId: 'hh-a', userId: 'u1', role: 'owner' });
    }
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('viewer') },
      ],
    });
  }

  async function stabilizeQualified(fixture: { detectChanges(): void; whenStable(): Promise<unknown> }) {
    for (let i = 0; i < 3; i++) {
      fixture.detectChanges();
      await fixture.whenStable();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  it('keeps current-page siblings and partial zero leaves without decision styling', async () => {
    const apiMock = {
      getHouseholdContext: vi.fn().mockResolvedValue(response({
        household_id: 'h1',
        display_name: 'Readable household',
        currency: 'USD',
        net_worth: {
          value: { amount_minor: 0, currency: 'USD' },
          incomplete_count: 2,
        },
        emergency_fund_months: null,
        monthly_cash_flow: {
          income: {
            value: { amount_minor: 0, currency: 'USD' },
            incomplete_count: 1,
          },
          spending: {
            value: { amount_minor: 25_000, currency: 'USD' },
            incomplete_count: 0,
          },
          net: null,
        },
        savings_contributions: {
          contributions: [],
          detection: { status: 'unavailable', incomplete_count: 1 },
        },
        spending_by_category: {
          month: '2026-09',
          month_label: 'September 2026',
          categories: [],
          categorized_total: {
            value: { amount_minor: 0, currency: 'USD' },
            incomplete_count: 0,
          },
          uncategorized: {
            value: { amount_minor: 0, currency: 'USD' },
            incomplete_count: 2,
          },
          total: {
            value: { amount_minor: 0, currency: 'USD' },
            incomplete_count: 2,
          },
        },
      })),
      updateHousehold: vi.fn(),
      getCashOutlook: vi.fn().mockResolvedValue(response({
        starting_cash: { amount_minor: 100_000, currency: 'USD' },
        events: [{
          occurred_on: '2026-09-10',
          name: 'Readable bill',
          amount: { amount_minor: -10_000, currency: 'USD' },
          kind: 'payment',
        }],
        ending_cash: null,
        lowest_balance: null,
        lowest_date: null,
        expected_income: null,
        income_projection: { status: 'unavailable', incomplete_count: 1 },
        obligations: {
          value: { amount_minor: 10_000, currency: 'USD' },
          incomplete_count: 1,
        },
        horizon_days: 30,
        due_soon: {
          value: { amount_minor: 10_000, currency: 'USD' },
          incomplete_count: 1,
        },
        due_soon_covered: null,
        due_soon_window_days: 14,
      })),
      getSpendingPlan: vi.fn().mockResolvedValue(response({
        month: '2026-09',
        income_received: {
          value: { amount_minor: 0, currency: 'USD' },
          incomplete_count: 1,
        },
        income_projected: null,
        expected_income: null,
        income_projection: { status: 'unavailable', incomplete_count: 1 },
        spent: {
          value: { amount_minor: 25_000, currency: 'USD' },
          incomplete_count: 0,
        },
        bills_remaining: {
          value: { amount_minor: 10_000, currency: 'USD' },
          incomplete_count: 1,
        },
        account_obligations: { amount_minor: 0, currency: 'USD' },
        planned_savings: { amount_minor: 0, currency: 'USD' },
        left_to_spend: null,
        per_day: null,
        days_remaining: 20,
      })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
    };
    configureQualified(apiMock);
    const fixture = TestBed.createComponent(Overview);
    await stabilizeQualified(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Readable household');
    expect(text).toContain('USD 0.00');
    expect(text).toContain('Partial total—2 stored amounts');
    expect(text).toContain('Spending by category');
    expect(text).toContain('Readable bill');
    expect(text).toContain('Left to spend this month');
    expect(text).toContain('Unavailable');
    expect(host.querySelector('.outlook-card__balance')).toBeNull();
    expect(text).not.toContain('No income sources recorded yet.');
    expect(text).not.toContain('Nothing detected.');
    expect(host.querySelector('.overview__value--positive')).toBeNull();
  });

  it('shows partial yearly leaves while suppressing net and ranking decisions', async () => {
    const apiMock = {
      getHouseholdContext: vi.fn().mockResolvedValue(response({
        household_id: 'h1',
        display_name: 'Readable household',
        currency: 'USD',
        net_worth: {
          value: { amount_minor: 0, currency: 'USD' },
          incomplete_count: 0,
        },
        emergency_fund_months: null,
        savings_contributions: {
          contributions: [],
          detection: { status: 'complete', incomplete_count: 0 },
        },
      })),
      updateHousehold: vi.fn(),
      getCashOutlook: vi.fn().mockResolvedValue(response(null)),
      getSpendingPlan: vi.fn().mockResolvedValue(response(null)),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
      getYearlyOverview: vi.fn().mockResolvedValue(response({
        year: 2026,
        months: [{
          month: '2026-09',
          income: {
            value: { amount_minor: 0, currency: 'USD' },
            incomplete_count: 1,
          },
          spending: {
            value: { amount_minor: 50_000, currency: 'USD' },
            incomplete_count: 0,
          },
          net: null,
          net_worth_eom: null,
        }],
        total_income: {
          value: { amount_minor: 0, currency: 'USD' },
          incomplete_count: 1,
        },
        total_spending: {
          value: { amount_minor: 50_000, currency: 'USD' },
          incomplete_count: 0,
        },
        total_net: null,
        top_categories: null,
        review: null,
      })),
    };
    configureQualified(apiMock);
    const fixture = TestBed.createComponent(Overview);
    await stabilizeQualified(fixture);
    const buttons = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button'));
    buttons.find((button) => button.textContent?.trim() === 'Year')!.click();
    await stabilizeQualified(fixture);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('In USD 0.00');
    expect(text).toContain('Kept Unavailable');
    expect(text).toContain('Partial total—1 stored amount');
    expect((fixture.nativeElement as HTMLElement).querySelector('.yearly__chart')?.getAttribute('role')).toBe('group');
    const partialMonth = (fixture.nativeElement as HTMLElement).querySelector('.yearly__partial');
    expect(partialMonth?.textContent).toContain('Partial data');
    expect(partialMonth?.closest('button')?.textContent).toContain('Partial data');
    expect((fixture.nativeElement as HTMLElement).querySelector('.yearly__focus')).toBeNull();
    expect(text).not.toContain('Where it went');
  });

  it('keeps the newest year when yearly requests complete in reverse order', async () => {
    clearAuthState();
    let resolve2025!: (value: unknown) => void;
    let resolve2026!: (value: unknown) => void;
    const apiMock = {
      getYearlyOverview: vi.fn((year: number) => new Promise((resolve) => {
        if (year === 2025) resolve2025 = resolve;
        else resolve2026 = resolve;
      })),
    };
    configureQualified(apiMock);
    const component = TestBed.createComponent(Overview).componentInstance;

    const older = component['loadYear'](2025);
    const newer = component['loadYear'](2026);
    resolve2026(response(yearOverview(2026)));
    await newer;
    resolve2025(response(yearOverview(2025)));
    await older;

    expect(component['yearData']()?.year).toBe(2026);
    expect(component['yearLoading']()).toBe(false);
  });

  it('does not attach a completed review to a newly selected year', async () => {
    clearAuthState();
    let resolveReview!: (value: unknown) => void;
    const apiMock = {
      getYearlyOverview: vi.fn((year: number) => Promise.resolve(response(yearOverview(year)))),
      generateYearlyReview: vi.fn().mockReturnValue(new Promise((resolve) => (resolveReview = resolve))),
    };
    configureQualified(apiMock);
    const component = TestBed.createComponent(Overview).componentInstance;
    component['yearData'].set(yearOverview(2025) as never);

    const generating = component['generateYearReview']();
    await component['loadYear'](2026);
    resolveReview(response({
      summary: 'Review of 2025',
      suggestions: [],
      months_covered: 12,
      model: null,
      generated_at: '2026-09-09T00:00:00Z',
    }));
    await generating;

    expect(apiMock.generateYearlyReview).toHaveBeenCalledWith(2025);
    expect(component['yearData']()?.year).toBe(2026);
    expect(component['yearData']()?.review).toBeNull();
  });

  it('ignores a yearly response completed after the household session changes', async () => {
    setAuthState({ accessToken: 'token-a', householdId: 'hh-a', userId: 'u1', role: 'owner' });
    let resolveYear!: (value: unknown) => void;
    const apiMock = {
      getYearlyOverview: vi.fn().mockReturnValue(new Promise((resolve) => (resolveYear = resolve))),
    };
    configureQualified(apiMock);
    const component = TestBed.createComponent(Overview).componentInstance;

    const loading = component['loadYear'](2025);
    clearAuthState();
    setAuthState({ accessToken: 'token-b', householdId: 'hh-b', userId: 'u2', role: 'owner' });
    resolveYear(response(yearOverview(2025)));
    await loading;

    expect(component['yearData']()).toBeNull();
    clearAuthState();
  });
});

describe('Overview #156: seeding the currency across a session switch', () => {
  afterEach(() => clearAuthState());

  it('does not seed household A\'s currency into household B\'s session', async () => {
    setAuthState({ accessToken: 'token-a', householdId: 'hh-a', userId: 'u1', role: 'owner' });
    let release!: (value: unknown) => void;
    const apiMock = {
      getHouseholdContext: vi.fn().mockReturnValue(new Promise((r) => (release = r))),
      updateHousehold: vi.fn().mockResolvedValue(response({})),
      getCashOutlook: vi.fn().mockResolvedValue(response(null)),
      getSpendingPlan: vi.fn().mockResolvedValue(response(null)),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listGoals: vi.fn().mockResolvedValue(response({ goals: [] })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
    };
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    const service = TestBed.inject(HouseholdCurrencyService);

    // A's Overview request is still in flight when A logs out and B logs in.
    clearAuthState();
    setAuthState({ accessToken: 'token-b', householdId: 'hh-b', userId: 'u2', role: 'owner' });
    release(
      response({
        household_id: 'hh-a',
        display_name: 'A',
        currency: 'EUR',
        net_worth: { amount_minor: 0, currency: 'EUR' },
        emergency_fund_months: 0,
      }),
    );
    await fixture.whenStable();

    // B's Accounts and Goals forms must not inherit EUR from A's response.
    expect(service.currency()).toBeNull();
  });
});

describe('Overview session ownership', () => {
  const sessionA = { accessToken: 'token-a', householdId: 'hh-a', userId: 'user-a', role: 'owner' };
  const sessionB = { accessToken: 'token-b', householdId: 'hh-b', userId: 'user-b', role: 'owner' };

  function deferred<T = unknown>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((resolver) => (resolve = resolver));
    return { promise, resolve };
  }

  function context(householdId: string, displayName: string, currency = 'USD') {
    return {
      household_id: householdId,
      display_name: displayName,
      currency,
      net_worth: { amount_minor: 0, currency },
      emergency_fund_months: null,
      savings_contributions: {
        contributions: [],
        detection: { status: 'complete', incomplete_count: 0 },
      },
    };
  }

  function yearOverview(year: number) {
    return {
      year,
      months: [],
      total_income: { value: { amount_minor: 0, currency: 'USD' }, incomplete_count: 0 },
      total_spending: { value: { amount_minor: 0, currency: 'USD' }, incomplete_count: 0 },
      total_net: { value: { amount_minor: 0, currency: 'USD' }, incomplete_count: 0 },
      top_categories: [],
      review: null,
    };
  }

  function apiDefaults(overrides: Record<string, unknown> = {}) {
    return {
      getHouseholdContext: vi.fn().mockResolvedValue(response(context('hh-a', 'Household A'))),
      updateHousehold: vi.fn().mockResolvedValue(response({})),
      getCashOutlook: vi.fn().mockResolvedValue(response(null)),
      getSpendingPlan: vi.fn().mockResolvedValue(response(null)),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      declareSavingsContribution: vi.fn().mockResolvedValue(response({})),
      deleteSavingsContribution: vi.fn().mockResolvedValue(response(undefined)),
      dismissSavingsContribution: vi.fn().mockResolvedValue(response(undefined)),
      updateSavingsContribution: vi.fn().mockResolvedValue(response({})),
      listGoals: vi.fn().mockResolvedValue(response({ goals: [] })),
      getHouseholdKeyStatus: vi.fn().mockResolvedValue(response(null)),
      getYearlyOverview: vi.fn().mockResolvedValue(response(yearOverview(2026))),
      generateYearlyReview: vi.fn().mockResolvedValue(response({})),
      ...overrides,
    };
  }

  function configureSession(apiMock: Record<string, unknown>) {
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [
        provideRouter([]),
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
  }

  async function stabilize(fixture: { detectChanges(): void; whenStable(): Promise<unknown> }) {
    for (let i = 0; i < 3; i++) {
      fixture.detectChanges();
      await fixture.whenStable();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  beforeEach(() => {
    clearAuthState();
    setAuthState(sessionA);
    localStorage.removeItem('family-cfo.hideSealedModeOffer');
  });

  afterEach(() => clearAuthState());

  it('clears settled owner resources synchronously on logout', async () => {
    configureSession(apiDefaults());
    const fixture = TestBed.createComponent(Overview);
    await stabilize(fixture);
    const component = fixture.componentInstance;
    expect(component['household'].value()?.display_name).toBe('Household A');
    expect(component['outlook'].value()).toBeNull();
    expect(component['plan'].value()).toBeNull();

    clearAuthState();

    expect(component['household'].value()).toBeUndefined();
    expect(component['outlook'].value()).toBeUndefined();
    expect(component['plan'].value()).toBeUndefined();
  });

  it('clears the same settled state synchronously for an interceptor-driven 401', async () => {
    configureSession(apiDefaults());
    const fixture = TestBed.createComponent(Overview);
    await stabilize(fixture);
    const component = fixture.componentInstance;
    component['yearData'].set(yearOverview(2025) as never);

    const handling = handleSessionResponse(new Response('{}', { status: 401 }));

    expect(component['household'].value()).toBeUndefined();
    expect(component['outlook'].value()).toBeUndefined();
    expect(component['plan'].value()).toBeUndefined();
    expect(component['yearData']()).toBeNull();
    await handling;
  });

  it('still clears Overview synchronously when 401 persistence removal throws', async () => {
    configureSession(apiDefaults());
    const fixture = TestBed.createComponent(Overview);
    await stabilize(fixture);
    const component = fixture.componentInstance;
    expect(component['household'].value()?.display_name).toBe('Household A');
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });

    try {
      const handling = handleSessionResponse(new Response('{}', { status: 401 }));
      expect(component['household'].value()).toBeUndefined();
      expect(component['outlook'].value()).toBeUndefined();
      expect(component['plan'].value()).toBeUndefined();
      await expect(handling).resolves.toBeInstanceOf(Response);
    } finally {
      removeItem.mockRestore();
    }
  });

  it('clears settled A immediately on direct A to B replacement, then shows only B', async () => {
    const getHouseholdContext = vi.fn().mockImplementation(() => {
      const current = authState();
      const isB = current?.householdId === 'hh-b';
      return Promise.resolve(response(context(
        current?.householdId ?? 'none',
        isB ? 'Household B' : 'Household A',
        isB ? 'EUR' : 'USD',
      )));
    });
    configureSession(apiDefaults({ getHouseholdContext }));
    const fixture = TestBed.createComponent(Overview);
    await stabilize(fixture);
    const component = fixture.componentInstance;
    expect(component['household'].value()?.display_name).toBe('Household A');

    setAuthState(sessionB);

    expect(component['household'].value()).toBeUndefined();
    expect(component['outlook'].value()).toBeUndefined();
    expect(component['plan'].value()).toBeUndefined();
    await stabilize(fixture);
    expect(component['household'].value()?.display_name).toBe('Household B');
    expect(TestBed.inject(HouseholdCurrencyService).currency()).toBe('EUR');
  });

  it('keeps B when in-flight A and B household loads complete in reverse order', async () => {
    const pendingA = deferred();
    const pendingB = deferred();
    let call = 0;
    const getHouseholdContext = vi.fn(() => (call++ === 0 ? pendingA.promise : pendingB.promise));
    configureSession(apiDefaults({ getHouseholdContext }));
    const fixture = TestBed.createComponent(Overview);
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(getHouseholdContext).toHaveBeenCalledTimes(1);

    setAuthState(sessionB);
    expect(fixture.componentInstance['household'].value()).toBeUndefined();
    fixture.detectChanges();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(getHouseholdContext).toHaveBeenCalledTimes(2);

    pendingB.resolve(response(context('hh-b', 'Household B', 'EUR')));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(fixture.componentInstance['household'].value()?.display_name).toBe('Household B');

    pendingA.resolve(response(context('hh-a', 'Household A', 'USD')));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(fixture.componentInstance['household'].value()?.display_name).toBe('Household B');
    expect(TestBed.inject(HouseholdCurrencyService).currency()).toBe('EUR');
  });

  it('resets the complete manual, lazy, edit, error, and loading inventory', () => {
    configureSession(apiDefaults());
    const component = TestBed.createComponent(Overview).componentInstance;
    component['household'].set(context('hh-a', 'Household A') as never);
    component['outlook'].set({ household: 'A' } as never);
    component['plan'].set({ household: 'A' } as never);
    component['keyStatus'].set({ household: 'A' } as never);
    component['accounts'].set([{ id: 'account-a' }] as never);
    component['goalNames'].set({ 'goal-a': 'A goal' });
    component['yearMode'].set(true);
    component['yearData'].set(yearOverview(2025) as never);
    component['yearLoading'].set(true);
    component['yearGenerating'].set(true);
    component['yearError'].set('A year error');
    component['yearFocusMonth'].set('2025-06');
    component['editingTarget'].set(true);
    component['targetInput'].set(9);
    component['savingTarget'].set(true);
    component['declaring'].set(true);
    component['savingsSubmitting'].set(true);
    component['savingsError'].set('A savings error');
    component['declareForm'].setValue({
      sourceAccountId: 'account-a',
      destinationAccountId: 'account-b',
      amount: 500,
      frequency: 'weekly',
    });
    component['languageInput'].set('vi');
    component['savingLanguage'].set(true);
    component['languageError'].set('A language error');
    component['timezoneInput'].set('Europe/London');
    component['savingTimezone'].set(true);
    component['timezoneError'].set('A timezone error');
    component['committedReserveInput'].set(true);
    component['savingCommittedReserve'].set(true);
    component['committedReserveError'].set('A reserve error');
    component['sealOfferDismissed'].set(true);

    setAuthState(sessionB);

    for (const resource of [
      component['household'], component['outlook'], component['plan'], component['keyStatus'],
      component['accounts'], component['goalNames'],
    ]) {
      expect(resource.value()).toBeUndefined();
      expect(resource.error()).toBeUndefined();
    }
    expect(component['yearMode']()).toBe(false);
    expect(component['yearData']()).toBeNull();
    expect(component['yearLoading']()).toBe(false);
    expect(component['yearGenerating']()).toBe(false);
    expect(component['yearError']()).toBeNull();
    expect(component['yearFocusMonth']()).toBeNull();
    expect(component['editingTarget']()).toBe(false);
    expect(component['targetInput']()).toBeNull();
    expect(component['savingTarget']()).toBe(false);
    expect(component['declaring']()).toBe(false);
    expect(component['savingsSubmitting']()).toBe(false);
    expect(component['savingsError']()).toBeNull();
    expect(component['declareForm'].getRawValue()).toEqual({
      sourceAccountId: '', destinationAccountId: '', amount: 0, frequency: 'monthly',
    });
    expect(component['languageInput']()).toBeNull();
    expect(component['savingLanguage']()).toBe(false);
    expect(component['languageError']()).toBeNull();
    expect(component['timezoneInput']()).toBeNull();
    expect(component['savingTimezone']()).toBe(false);
    expect(component['timezoneError']()).toBeNull();
    expect(component['committedReserveInput']()).toBeNull();
    expect(component['savingCommittedReserve']()).toBe(false);
    expect(component['committedReserveError']()).toBeNull();
    expect(component['sealOfferDismissed']()).toBe(true);
  });

  it('does not let a stale A mutation clear B progress or reload B', async () => {
    const pendingA = deferred();
    const pendingB = deferred();
    let call = 0;
    const updateHousehold = vi.fn(() => (call++ === 0 ? pendingA.promise : pendingB.promise));
    configureSession(apiDefaults({ updateHousehold }));
    const component = TestBed.createComponent(Overview).componentInstance;
    const reload = vi.spyOn(component['household'], 'reload');

    const savingA = component['changeLanguage']('vi');
    expect(component['savingLanguage']()).toBe(true);
    setAuthState(sessionB);
    expect(component['savingLanguage']()).toBe(false);
    const savingB = component['changeLanguage']('lt');
    expect(component['savingLanguage']()).toBe(true);
    expect(component['languageInput']()).toBe('lt');

    pendingA.resolve(response({}));
    await savingA;
    expect(component['savingLanguage']()).toBe(true);
    expect(component['languageInput']()).toBe('lt');
    expect(component['languageError']()).toBeNull();
    expect(reload).not.toHaveBeenCalled();

    pendingB.resolve(response({}));
    await savingB;
    expect(component['savingLanguage']()).toBe(false);
    expect(component['languageInput']()).toBe('lt');
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
