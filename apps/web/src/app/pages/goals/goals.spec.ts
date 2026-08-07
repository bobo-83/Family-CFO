import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Goals } from './goals';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Goals', () => {
  let apiMock: { listGoals: ReturnType<typeof vi.fn>; createGoal: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    apiMock = { listGoals: vi.fn(), createGoal: vi.fn() };
  });

  it('shows the create form for an owner and creates a goal', async () => {
    apiMock.listGoals.mockResolvedValue(response({ goals: [] }));
    apiMock.createGoal.mockResolvedValue(
      response({
        id: 'g1',
        name: 'New car fund',
        type: 'vehicle',
        target: { amount_minor: 500_000, currency: 'USD' },
        current: { amount_minor: 0, currency: 'USD' },
        priority: 3,
      }),
    );

    TestBed.configureTestingModule({
      imports: [Goals],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Goals);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const form = (fixture.nativeElement as HTMLElement).querySelector('.goal-form');
    expect(form).toBeTruthy();

    const component = fixture.componentInstance;
    component['form'].setValue({
      name: 'New car fund',
      type: 'vehicle',
      targetAmount: 5000,
      priority: 2,
      monthlyContribution: 0,
    });
    await component['submit']();

    expect(apiMock.createGoal).toHaveBeenCalledWith({
      name: 'New car fund',
      type: 'vehicle',
      target: { amount_minor: 500_000, currency: 'USD' },
      priority: 2,
    });
  });

  // #4: each goal says what the ledger shows funding it — including that
  // "unfunded" means no linked transfers, which for retirement is expected.
  it('renders the funding line for every status, with the retirement caveat', async () => {
    const money = (minor: number) => ({ amount_minor: minor, currency: 'USD' });
    apiMock.listGoals.mockResolvedValue(
      response({
        goals: [
          {
            id: 'g1',
            name: 'College fund',
            type: 'college',
            target: money(2_000_000),
            current: money(500_000),
            priority: 1,
            target_date: '2028-08-01',
            funding: {
              monthly_equivalent: money(50_000),
              funded_by: [
                {
                  contribution_id: 'sc1',
                  destination_name: 'College 529',
                  amount: money(50_000),
                  frequency: 'monthly',
                },
              ],
              projected_completion: '2028-03-01',
              status: 'on_track',
            },
          },
          {
            id: 'g2',
            name: 'New roof',
            type: 'renovation',
            target: money(3_000_000),
            current: money(200_000),
            priority: 2,
            target_date: '2026-12-01',
            funding: {
              monthly_equivalent: money(10_000),
              funded_by: [
                {
                  contribution_id: 'sc2',
                  destination_name: 'Rainy Day Savings',
                  amount: money(120_000),
                  frequency: 'annual',
                },
              ],
              projected_completion: '2027-06-01',
              status: 'behind',
            },
          },
          {
            id: 'g3',
            name: 'Someday boat',
            type: 'other',
            target: money(5_000_000),
            current: money(100_000),
            priority: 4,
            funding: {
              monthly_equivalent: money(25_000),
              funded_by: [
                {
                  contribution_id: 'sc3',
                  destination_name: 'Fidelity Brokerage',
                  amount: money(25_000),
                  frequency: 'monthly',
                },
              ],
              projected_completion: null,
              status: 'funded_no_date',
            },
          },
          {
            id: 'g4',
            name: 'Hawaii trip',
            type: 'vacation',
            target: money(800_000),
            current: money(0),
            priority: 3,
            funding: {
              monthly_equivalent: money(0),
              funded_by: [],
              projected_completion: null,
              status: 'unfunded',
            },
          },
          {
            id: 'g5',
            name: 'Retire at 60',
            type: 'retirement',
            target: money(100_000_000),
            current: money(20_000_000),
            priority: 1,
            funding: {
              monthly_equivalent: money(0),
              funded_by: [],
              projected_completion: null,
              status: 'unfunded',
            },
          },
        ],
      }),
    );

    TestBed.configureTestingModule({
      imports: [Goals],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Goals);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('On track — USD 500.00/mo going in · projected Mar 2028');
    expect(text).toContain(
      "Behind — USD 100.00/mo won't reach the target by Dec 2026 (projected Jun 2027)",
    );
    expect(text).toContain('USD 250.00/mo going in');
    expect(text).toContain('Nothing is currently funding this goal');
    // Retirement's unfunded wording carries the payroll caveat instead.
    expect(text).toContain(
      "No linked transfers — 401(k) payroll deductions don't appear here.",
    );
    // The funded_by rows read "destination · amount cadence".
    expect(text).toContain('College 529 · USD 500.00 monthly');
    expect(text).toContain('Rainy Day Savings · USD 1,200.00 yearly');
    // The loud unfunded style applies to the vacation goal, not the retirement caveat.
    const unfunded = host.querySelectorAll('.goal-list__funding--unfunded');
    expect(unfunded.length).toBe(2);
    expect(host.querySelectorAll('.goal-list__funding--payroll').length).toBe(1);
  });

  it('hides the create form for a viewer', async () => {
    apiMock.listGoals.mockResolvedValue(response({ goals: [] }));

    TestBed.configureTestingModule({
      imports: [Goals],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('viewer') },
      ],
    });
    const fixture = TestBed.createComponent(Goals);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const form = (fixture.nativeElement as HTMLElement).querySelector('.goal-form');
    expect(form).toBeFalsy();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Only the household owner or an adult member can add goals.');
  });
});
