import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
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
      { provide: AuthService, useValue: authMock(role) },
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

  it('tags an account as vested RSUs with a boolean-only PATCH', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            {
              id: 'a1',
              name: 'Employer stock plan',
              type: 'brokerage',
              balance: { amount_minor: 8_400_000, currency: 'USD' },
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

    const toggle = host.querySelector('.accounts-table__rsu-toggle') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change'));
    await fixture.whenStable();

    // Only the tag is sent — nothing that could disturb other designations.
    expect(apiMock.updateAccount).toHaveBeenCalledWith('a1', { rsu_ready_to_sell: true });
    expect(apiMock.updateAccount).toHaveBeenCalledTimes(1);
  });
});

// --- #11: credit-card statements — the EXACT amount due, not the estimate ---

const CARD = {
  id: 'c1',
  name: 'Visa',
  type: 'credit_card',
  balance: { amount_minor: -120_000, currency: 'USD' },
};

function cardStatement(overrides: Record<string, unknown> = {}) {
  return {
    id: 's1',
    account_id: 'c1',
    account_name: 'Visa',
    statement_balance: { amount_minor: 84_215, currency: 'USD' },
    due_date: '2026-08-14',
    ...overrides,
  };
}

function cardApiMock(overrides: Record<string, unknown> = {}) {
  return {
    listAccounts: vi.fn().mockResolvedValue(response({ accounts: [CARD] })),
    listCardStatements: vi.fn().mockResolvedValue(response({ statements: [] })),
    recordCardStatement: vi.fn().mockResolvedValue(response(cardStatement())),
    markCardStatementPaid: vi.fn().mockResolvedValue(response(cardStatement())),
    deleteCardStatement: vi.fn().mockResolvedValue(response(undefined)),
    scanCardStatement: vi.fn(),
    ...overrides,
  };
}

/** Renders the page and opens the card's statement panel. */
async function openCardPanel(apiMock: Record<string, unknown>, role: string) {
  configure(apiMock, role);
  const fixture = TestBed.createComponent(Accounts);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();

  const host = fixture.nativeElement as HTMLElement;
  (host.querySelector('.accounts-table__statements') as HTMLButtonElement).click();
  await fixture.whenStable();
  fixture.detectChanges();
  return { fixture, host };
}

describe('Accounts #11: credit-card statements', () => {
  it('records a statement through the API and reloads the list', async () => {
    const apiMock = cardApiMock();
    const { fixture } = await openCardPanel(apiMock, 'owner');
    expect(apiMock.listCardStatements).toHaveBeenCalledWith('c1');

    const component = fixture.componentInstance as unknown as {
      accounts: { value(): { id: string; balance: { currency: string } }[] | undefined };
      statementForm: { setValue(v: unknown): void };
      submitStatement(account: unknown): Promise<void>;
    };
    const account = component.accounts.value()![0];
    component.statementForm.setValue({
      statementBalance: 842.15,
      dueDate: '2026-08-14',
      minimumDue: 35,
      periodStart: '2026-07-10',
      periodEnd: '2026-08-09',
    });
    await component.submitStatement(account);
    await fixture.whenStable();

    expect(apiMock.recordCardStatement).toHaveBeenCalledWith({
      account_id: 'c1',
      statement_balance: { amount_minor: 84_215, currency: 'USD' },
      due_date: '2026-08-14',
      minimum_due: { amount_minor: 3_500, currency: 'USD' },
      period_start: '2026-07-10',
      period_end: '2026-08-09',
    });
    // The list is re-read so the new cycle shows up.
    expect((apiMock.listCardStatements as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2);
  });

  it('reflects a re-recorded cycle as an update, not a second obligation', async () => {
    const listCardStatements = vi
      .fn()
      .mockResolvedValueOnce(response({ statements: [cardStatement()] }))
      .mockResolvedValue(
        response({
          statements: [
            cardStatement({ statement_balance: { amount_minor: 90_000, currency: 'USD' } }),
          ],
        }),
      );
    const apiMock = cardApiMock({ listCardStatements });
    const { fixture, host } = await openCardPanel(apiMock, 'owner');
    expect(host.querySelectorAll('.card-statements__item').length).toBe(1);
    expect(host.querySelector('.card-statements__amount')?.textContent).toContain('842.15');

    const component = fixture.componentInstance as unknown as {
      accounts: { value(): unknown[] | undefined };
      statementForm: { setValue(v: unknown): void };
      submitStatement(account: unknown): Promise<void>;
    };
    component.statementForm.setValue({
      statementBalance: 900,
      dueDate: '2026-08-14', // same card + due date = the same cycle
      minimumDue: null,
      periodStart: '',
      periodEnd: '',
    });
    await component.submitStatement(component.accounts.value()![0]);
    await fixture.whenStable();
    fixture.detectChanges();

    // Still one row — the cycle was updated in place.
    expect(host.querySelectorAll('.card-statements__item').length).toBe(1);
    expect(host.querySelector('.card-statements__amount')?.textContent).toContain('900.00');
  });

  it('marks a cycle paid, clears the mark, and deletes it', async () => {
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
    });
    const { fixture, host } = await openCardPanel(apiMock, 'owner');

    (host.querySelector('.card-statements__paid') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(apiMock.markCardStatementPaid).toHaveBeenCalledWith('s1', expect.any(String));

    // A paid cycle offers the inverse: clearing the mark sends null.
    (apiMock.listCardStatements as ReturnType<typeof vi.fn>).mockResolvedValue(
      response({ statements: [cardStatement({ paid_at: '2026-08-12' })] }),
    );
    const component = fixture.componentInstance as unknown as {
      cardStatements: { reload(): void };
      toggleStatementPaid(s: unknown): Promise<void>;
    };
    await component.toggleStatementPaid(cardStatement({ paid_at: '2026-08-12' }));
    expect(apiMock.markCardStatementPaid).toHaveBeenLastCalledWith('s1', null);

    vi.spyOn(window, 'confirm').mockReturnValue(true);
    (host.querySelector('.card-statements__delete') as HTMLButtonElement).click();
    await fixture.whenStable();
    expect(apiMock.deleteCardStatement).toHaveBeenCalledWith('s1');
  });

  it('prefills the form from a scan instead of saving it', async () => {
    const apiMock = cardApiMock({
      scanCardStatement: vi.fn().mockResolvedValue(
        response({
          statement_balance_minor: 84_215,
          minimum_due_minor: 3_500,
          due_date: '2026-08-14',
          period_start: '2026-07-10',
          period_end: '2026-08-09',
          note: 'Read by the on-box model.',
        }),
      ),
    });
    const { fixture } = await openCardPanel(apiMock, 'owner');

    const component = fixture.componentInstance as unknown as {
      scanCardStatementFile(file: File): Promise<void>;
      statementForm: { getRawValue(): Record<string, unknown> };
      statementScanNote(): string | null;
    };
    await component.scanCardStatementFile(new File(['stmt'], 's.png', { type: 'image/png' }));

    expect(apiMock.scanCardStatement).toHaveBeenCalledWith(expect.any(String), 'image/png');
    // Candidates only — the user still has to confirm.
    expect(apiMock.recordCardStatement).not.toHaveBeenCalled();
    expect(component.statementForm.getRawValue()).toMatchObject({
      statementBalance: 842.15,
      minimumDue: 35,
      dueDate: '2026-08-14',
      periodStart: '2026-07-10',
      periodEnd: '2026-08-09',
    });
    expect(component.statementScanNote()).toBe('Read by the on-box model.');
  });

  it('surfaces a scan failure (e.g. no vision model) without saving', async () => {
    const apiMock = cardApiMock({
      scanCardStatement: vi
        .fn()
        .mockResolvedValue(
          response(undefined, { error: { message: 'No vision model is serving.' } }),
        ),
    });
    const { fixture } = await openCardPanel(apiMock, 'owner');
    const component = fixture.componentInstance as unknown as {
      scanCardStatementFile(file: File): Promise<void>;
      submitError(): string | null;
    };
    await component.scanCardStatementFile(new File(['stmt'], 's.png', { type: 'image/png' }));

    expect(component.submitError()).toBe('No vision model is serving.');
    expect(apiMock.recordCardStatement).not.toHaveBeenCalled();
  });

  it('lets a viewer read the cycles but offers no actions', async () => {
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
    });
    const { host } = await openCardPanel(apiMock, 'viewer');

    expect(host.querySelector('.card-statements__item')?.textContent).toContain('842.15');
    expect(host.querySelector('.card-statement-form')).toBeFalsy();
    expect(host.querySelector('.card-statements__paid')).toBeFalsy();
    expect(host.querySelector('.card-statements__delete')).toBeFalsy();
  });
});
