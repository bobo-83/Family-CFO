import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
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
    name: 'SUBARU LEASE (3290)',
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
      { provide: AuthService, useValue: { role: () => role } },
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
    expect(text).toContain('SUBARU LEASE (3290)');
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
    cmp.form.name = '2022 Ascent — Subaru lease';
    cmp.form.balanceOwed = 10_000;
    cmp.form.monthlyPayment = 611.51;
    cmp.form.endMode = 'payments';
    cmp.form.paymentsLeft = 24;
    await cmp.save();

    expect(apiMock.createAccount).toHaveBeenCalledWith(
      expect.objectContaining({
        name: '2022 Ascent — Subaru lease',
        maturity_date: dateAfterPayments(24),
        minimum_payment: { amount_minor: 61_151, currency: 'USD' },
      }),
    );
    // A liability carries a NEGATIVE balance — the amount owed.
    expect(apiMock.recordAccountBalance).toHaveBeenCalledWith('new1', -1_000_000, 'USD');
  });

  it('pastes a statement into the scan while the form is open (ADR 0028)', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      scanLoanStatement: vi.fn().mockResolvedValue(
        response({
          name: 'SUBARU LEASE',
          monthly_payment_minor: 42_828,
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
    expect(cmp.form.monthlyPayment).toBeCloseTo(428.28);
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
