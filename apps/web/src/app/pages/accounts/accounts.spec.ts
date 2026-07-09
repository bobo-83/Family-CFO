import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Accounts } from './accounts';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Accounts],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Accounts', () => {
  it('renders a row per account', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            {
              id: 'a1',
              name: 'Checking',
              type: 'checking',
              balance: { amount_minor: 500_000, currency: 'USD' },
            },
            {
              id: 'a2',
              name: 'Savings',
              type: 'savings',
              balance: { amount_minor: 1_500_000, currency: 'USD' },
            },
          ],
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Checking');
    expect(rows[0].textContent).toContain('USD 5,000.00');
  });

  it('hides the create form for a viewer', async () => {
    const apiMock = { listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })) };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.account-form')).toBeFalsy();
  });

  it('creates an account with an opening balance for an owner', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      createAccount: vi
        .fn()
        .mockResolvedValue(
          response({
            id: 'a9',
            name: 'Brokerage',
            type: 'brokerage',
            balance: { amount_minor: 0, currency: 'USD' },
          }),
        ),
      recordAccountBalance: vi.fn().mockResolvedValue(response({})),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.account-form')).toBeTruthy();

    const component = fixture.componentInstance;
    component['form'].setValue({
      name: 'Brokerage',
      type: 'brokerage',
      currency: 'USD',
      openingBalance: 2500,
    });
    await component['submit']();

    expect(apiMock.createAccount).toHaveBeenCalledWith({
      name: 'Brokerage',
      type: 'brokerage',
      currency: 'USD',
    });
    expect(apiMock.recordAccountBalance).toHaveBeenCalledWith('a9', 250_000, 'USD');
  });
});

describe('Accounts M36: emergency fund + group rollups', () => {
  it('shows group rollups and the emergency-fund total, and patches a designation', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            {
              id: 'a1',
              name: 'Checking',
              type: 'checking',
              balance: { amount_minor: 500_000, currency: 'USD' },
            },
            {
              id: 'a2',
              name: 'HY Savings',
              type: 'savings',
              balance: { amount_minor: 1_000_000, currency: 'USD' },
              emergency_fund_percent: 50,
              emergency_fund_reserved: { amount_minor: 500_000, currency: 'USD' },
            },
            {
              id: 'a3',
              name: 'Visa',
              type: 'credit_card',
              balance: { amount_minor: -200_000, currency: 'USD' },
            },
          ],
        }),
      ),
      updateAccount: vi.fn().mockResolvedValue(response({})),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    // Rollups: Cash = 5,000 + 10,000; Debts = -2,000.
    const titles = [...host.querySelectorAll('.accounts-group__title')].map(
      (t) => t.textContent ?? '',
    );
    expect(titles.find((t) => t.includes('Cash'))).toContain('USD 15,000.00');
    expect(titles.find((t) => t.includes('Debts'))).toContain('-USD 2,000.00');

    // Emergency fund: page total + per-row reservation are shown.
    expect(host.querySelector('.accounts-ef-total')?.textContent).toContain('USD 5,000.00');

    // Changing the fixed amount patches through the API.
    const row = [...host.querySelectorAll('tbody tr')].find((r) =>
      r.textContent?.includes('Checking'),
    )!;
    const mode = row.querySelector('.accounts-table__ef-mode') as HTMLSelectElement;
    const value = row.querySelector('.accounts-table__ef-value') as HTMLInputElement;
    mode.value = 'amount';
    value.value = '1000';
    value.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    expect(apiMock.updateAccount).toHaveBeenCalledWith('a1', {
      emergency_fund_amount: { amount_minor: 100_000, currency: 'USD' },
    });
  });
});
