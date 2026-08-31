import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Loans, dateAfterPayments, monthsLeft } from './loans';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function loan(overrides: Record<string, unknown> = {}) {
  return {
    id: 'l1',
    name: 'ACME LEASE (0042)',
    type: 'auto_loan',
    currency: 'USD',
    balance: { amount_minor: 0, currency: 'USD' },
    minimum_payment: { amount_minor: 61_151, currency: 'USD' },
    annual_interest_rate: 0,
    maturity_date: null,
    ...overrides,
  };
}

function configure(apiMock: Record<string, unknown>, role = 'owner') {
  TestBed.configureTestingModule({
    imports: [Loans],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

async function stabilize(fixture: { detectChanges(): void; whenStable(): Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  await new Promise((resolve) => setTimeout(resolve));
  fixture.detectChanges();
}

describe('Loans', () => {
  it('payments-left and the derived date are exact inverses (M115)', () => {
    expect(monthsLeft(dateAfterPayments(36))).toBe(36);
    expect(monthsLeft(dateAfterPayments(1))).toBe(1);
  });

  // The case above runs against whatever today happens to be, so it only
  // exercises a month end four days a month — which is how an overflow that
  // mis-derives every loan edited on the 31st survived until a CI run at
  // 00:12 UTC on 31 August. "Today" is pinned here so the edges are checked
  // on every run, not when the calendar volunteers them.
  describe('the inverse holds on month ends too (M115)', () => {
    afterEach(() => vi.useRealTimers());

    const on = (year: number, month: number, day: number) => {
      // Only Date is faked: this file's async tests await real setTimeout.
      vi.useFakeTimers({ toFake: ['Date'] });
      vi.setSystemTime(new Date(year, month, day, 12, 0, 0));
    };

    const cases: [string, number, number, number][] = [
      ['mid-month', 2026, 5, 15],
      ['31 Aug — September has no 31st', 2026, 7, 31],
      ['31 Jan — the shortest month follows', 2026, 0, 31],
      ['30 Sep — a 30-day month end', 2026, 8, 30],
      ['29 Feb — a leap day, one year out', 2028, 1, 29],
    ];

    for (const [label, year, month, day] of cases) {
      it(`holds on ${label}`, () => {
        on(year, month, day);
        for (const payments of [1, 2, 3, 12, 36]) {
          expect(monthsLeft(dateAfterPayments(payments))).toBe(payments);
        }
      });
    }

    it('never derives a date in a month the payment count did not ask for', () => {
      on(2026, 0, 31); // 31 Jan
      expect(dateAfterPayments(1)).toBe('2026-02-28');
      expect(dateAfterPayments(3)).toBe('2026-04-30');
      on(2026, 7, 31); // 31 Aug
      expect(dateAfterPayments(1)).toBe('2026-09-30');
    });
  });

  it('lists loans with the summary, excluding 401(k) loans from totals', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            loan({ id: 'l1', balance: { amount_minor: -1_000_000, currency: 'USD' } }),
            loan({
              id: 'l2',
              name: '401k Loan',
              type: '401k_loan',
              balance: { amount_minor: -500_000, currency: 'USD' },
              minimum_payment: { amount_minor: 20_000, currency: 'USD' },
            }),
            loan({ id: 'c1', name: 'Checking', type: 'checking' }),
          ],
        }),
      ),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Loans);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('ACME LEASE (0042)');
    expect(text).toContain('401k Loan');
    expect(text).not.toContain('Checking'); // only loan types listed
    // Totals exclude the 401(k) loan.
    expect(text).toContain('USD 10,000.00'); // total owed
    expect(text).toContain('USD 611.51'); // monthly payments
    expect(text).toContain('payroll-deducted');
  });

  it('saves a loan entered by payments-left with the derived maturity and a negative balance', async () => {
    const apiMock = {
      listAccounts: vi
        .fn()
        .mockResolvedValue(response({ accounts: [] })),
      createAccount: vi.fn().mockResolvedValue(response({ id: 'new1' })),
      recordAccountBalance: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Loans);
    await stabilize(fixture);

    const cmp = fixture.componentInstance as unknown as {
      startAdd(): void;
      form: {
        name: string;
        balanceOwed: number | null;
        monthlyPayment: number | null;
        endMode: string;
        paymentsLeft: number | null;
      };
      save(): Promise<void>;
    };
    cmp.startAdd();
    cmp.form.name = '2022 sedan lease';
    cmp.form.balanceOwed = 10_000;
    cmp.form.monthlyPayment = 611.51;
    cmp.form.endMode = 'payments';
    cmp.form.paymentsLeft = 24;
    await cmp.save();

    expect(apiMock.createAccount).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '2022 sedan lease',
        maturity_date: dateAfterPayments(24),
        minimum_payment: { amount_minor: 61_151, currency: 'USD' },
      }),
    );
    // A liability carries a NEGATIVE balance — the amount owed.
    expect(apiMock.recordAccountBalance).toHaveBeenCalledWith('new1', -1_000_000, 'USD');
  });

  it('stores the entered APR percent as a decimal fraction (ADR 0042)', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      createAccount: vi.fn().mockResolvedValue(response({ id: 'new1' })),
      recordAccountBalance: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Loans);
    await stabilize(fixture);

    const cmp = fixture.componentInstance as unknown as {
      startAdd(): void;
      form: { name: string; balanceOwed: number | null; monthlyPayment: number | null; apr: number | null };
      save(): Promise<void>;
    };
    cmp.startAdd();
    cmp.form.name = 'Card';
    cmp.form.balanceOwed = 5_000;
    cmp.form.monthlyPayment = 150;
    cmp.form.apr = 9.5; // the user types a percent…

    await cmp.save();

    expect(apiMock.createAccount).toHaveBeenCalledWith(
      // …and it is stored as a fraction the engine can use.
      expect.objectContaining({ annual_interest_rate: 0.095 }),
    );
  });

  it('pastes a statement into the scan while the form is open (ADR 0028)', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      scanLoanStatement: vi.fn().mockResolvedValue(
        response({
          name: 'ACME LEASE',
          monthly_payment_minor: 41_285,
          payments_remaining: 18,
          is_lease: true,
          note: 'Read by the on-box model.',
        }),
      ),
    };
    configure(apiMock);
    const fixture = TestBed.createComponent(Loans);
    await stabilize(fixture);

    const cmp = fixture.componentInstance as unknown as {
      startAdd(): void;
      onPaste(event: ClipboardEvent): Promise<void>;
      form: { monthlyPayment: number | null; endMode: string; paymentsLeft: number | null; type: string };
    };
    const file = new File(['stmt'], 's.png', { type: 'image/png' });
    const pasteEvent = {
      clipboardData: { items: [{ kind: 'file', getAsFile: () => file }] },
      preventDefault: () => {},
    } as unknown as ClipboardEvent;

    // Form closed: paste is ignored.
    await cmp.onPaste(pasteEvent);
    expect(apiMock.scanLoanStatement).not.toHaveBeenCalled();

    // Form open: paste feeds the scan and prefills — including payments left.
    cmp.startAdd();
    await cmp.onPaste(pasteEvent);
    expect(apiMock.scanLoanStatement).toHaveBeenCalledWith(expect.any(String), 'image/png');
    expect(cmp.form.monthlyPayment).toBeCloseTo(412.85);
    expect(cmp.form.endMode).toBe('payments');
    expect(cmp.form.paymentsLeft).toBe(18);
    expect(cmp.form.type).toBe('auto_loan'); // is_lease flipped the default
  });

  it('hides editing from a viewer', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [loan()] })),
    };
    configure(apiMock, 'viewer');
    const fixture = TestBed.createComponent(Loans);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.bill-list__confirm')).toBeNull();
    expect(host.textContent).toContain('Only the household owner or an adult member');
  });
});
