import { provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
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
  let apiMock: { getHouseholdContext: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    apiMock = { getHouseholdContext: vi.fn() };
    TestBed.configureTestingModule({
      imports: [Overview],
      providers: [provideRouter([]), { provide: ApiService, useValue: apiMock }],
    });
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
});
