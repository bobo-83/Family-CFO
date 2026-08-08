import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
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
      // #25 sends an unmatched statement line to the add-transaction form.
      provideRouter([]),
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
    replaceStatementLines: vi.fn().mockResolvedValue(response(reconciliation())),
    getStatementReconciliation: vi.fn().mockResolvedValue(response(reconciliation())),
    ...overrides,
  };
}

function reconciliation(overrides: Record<string, unknown> = {}) {
  return {
    statement_id: 's1',
    account_name: 'Visa',
    period_label: 'August 2026',
    lines: [],
    unaccounted: [],
    matched_count: 0,
    missing_from_sync_count: 0,
    not_on_statement_count: 0,
    amount_differs_count: 0,
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

// --- #25: reconciling the statement's LINE ITEMS against synced transactions --

function statementLine(overrides: Record<string, unknown> = {}) {
  return {
    id: 'l1',
    occurred_on: '2026-08-03',
    description: 'BLUE BOTTLE COFFEE',
    amount: { amount_minor: -675, currency: 'USD' },
    matched_transaction_id: 't1',
    match_kind: 'exact',
    ...overrides,
  };
}

/** Opens the card panel and then the reconciliation view for statement s1. */
async function openReconciliation(apiMock: Record<string, unknown>, role: string) {
  const opened = await openCardPanel(apiMock, role);
  (opened.host.querySelector('.card-statements__reconcile') as HTMLButtonElement).click();
  await opened.fixture.whenStable();
  opened.fixture.detectChanges();
  return opened;
}

describe('Accounts #25: statement reconciliation', () => {
  it('leads with the charges the feed never delivered', async () => {
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
      getStatementReconciliation: vi.fn().mockResolvedValue(
        response(
          reconciliation({
            lines: [
              statementLine(),
              statementLine({ id: 'l2', description: 'COSTCO', matched_transaction_id: null, match_kind: null }),
              statementLine({ id: 'l3', description: 'TIP ADJUSTED', match_kind: 'amount_differs' }),
            ],
            matched_count: 2,
            missing_from_sync_count: 1,
            amount_differs_count: 1,
            not_on_statement_count: 0,
          }),
        ),
      ),
    });
    const { host } = await openReconciliation(apiMock, 'owner');

    expect(apiMock.getStatementReconciliation).toHaveBeenCalledWith('s1');
    const coverage = host.querySelector('.statement-recon__coverage')?.textContent ?? '';
    expect(coverage).toContain('2 of 3');
    expect(coverage).toContain('1 not synced');
    expect(coverage).toContain('1 amount differs');

    // The gap is FIRST, not buried under the rows that are fine.
    const rows = Array.from(host.querySelectorAll('.statement-recon__line'));
    expect(rows[0].textContent).toContain('COSTCO');
    expect(rows[0].classList).toContain('statement-recon__line--missing');
    expect(rows[1].classList).toContain('statement-recon__line--differs');
  });

  it('names the synced transactions the statement never listed', async () => {
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
      getStatementReconciliation: vi.fn().mockResolvedValue(
        response(
          reconciliation({
            lines: [statementLine()],
            matched_count: 1,
            unaccounted: [
              {
                transaction_id: 't9',
                occurred_at: '2026-08-11',
                merchant: 'POSTED LATE LLC',
                amount: { amount_minor: -4_200, currency: 'USD' },
              },
            ],
            not_on_statement_count: 1,
          }),
        ),
      ),
    });
    const { host } = await openReconciliation(apiMock, 'owner');

    expect(host.querySelector('.statement-recon__coverage')?.textContent).toContain(
      '1 posted after close',
    );
    expect(host.querySelector('.statement-recon__subtitle')).toBeTruthy();
    expect(host.textContent).toContain('POSTED LATE LLC');
  });

  it('offers the scanned line items but stores nothing until asked', async () => {
    const apiMock = cardApiMock({
      scanCardStatement: vi.fn().mockResolvedValue(
        response({
          statement_balance_minor: 84_215,
          due_date: '2026-08-14',
          // Already in the ledger's convention: a charge is negative.
          lines: [
            { occurred_on: '2026-08-03', description: 'BLUE BOTTLE', amount_minor: -675 },
            { occurred_on: '2026-08-20', description: 'PAYMENT THANK YOU', amount_minor: 25_000 },
            // A row the reader could not date is dropped, never guessed at.
            { occurred_on: null, description: 'UNDATED', amount_minor: -100 },
          ],
          note: 'Read by the on-box model.',
        }),
      ),
    });
    const { fixture, host } = await openCardPanel(apiMock, 'owner');
    const component = fixture.componentInstance as unknown as {
      accounts: { value(): { id: string; balance: { currency: string } }[] | undefined };
      scanCardStatementFile(file: File): Promise<void>;
      statementForm: { setValue(v: unknown): void };
      submitStatement(account: unknown): Promise<void>;
    };
    await component.scanCardStatementFile(new File(['s'], 's.pdf', { type: 'application/pdf' }));
    fixture.detectChanges();

    // Scanning alone stores nothing — there is not even a statement to hang on yet.
    expect(apiMock.replaceStatementLines).not.toHaveBeenCalled();
    expect(host.querySelector('.statement-lines-offer__store')).toBeFalsy();

    const account = component.accounts.value()![0];
    component.statementForm.setValue({
      statementBalance: 842.15,
      dueDate: '2026-08-14',
      minimumDue: null,
      periodStart: '',
      periodEnd: '',
    });
    await component.submitStatement(account);
    await fixture.whenStable();
    fixture.detectChanges();

    // Now the offer appears, and it says plainly that storing overwrites.
    const offer = host.querySelector('.statement-lines-offer__text')?.textContent ?? '';
    expect(offer).toContain('2 line items');
    expect(offer).toContain('REPLACES');
    expect(apiMock.replaceStatementLines).not.toHaveBeenCalled();

    (host.querySelector('.statement-lines-offer__store') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.replaceStatementLines).toHaveBeenCalledWith('s1', [
      {
        occurred_on: '2026-08-03',
        description: 'BLUE BOTTLE',
        amount: { amount_minor: -675, currency: 'USD' },
      },
      {
        occurred_on: '2026-08-20',
        description: 'PAYMENT THANK YOU',
        amount: { amount_minor: 25_000, currency: 'USD' },
      },
    ]);
    // The offer is consumed, so a second click cannot double-store.
    expect(host.querySelector('.statement-lines-offer__store')).toBeFalsy();
  });

  it('prefills the add-transaction form from an unmatched line, never writing', async () => {
    const createTransaction = vi.fn();
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
      createTransaction,
      getStatementReconciliation: vi.fn().mockResolvedValue(
        response(
          reconciliation({
            lines: [
              statementLine({
                id: 'l2',
                description: 'COSTCO WHSE #1102',
                amount: { amount_minor: -18_240, currency: 'USD' },
                matched_transaction_id: null,
                match_kind: null,
              }),
            ],
            missing_from_sync_count: 1,
          }),
        ),
      ),
    });
    const { fixture, host } = await openReconciliation(apiMock, 'owner');
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);

    (host.querySelector('.statement-recon__add') as HTMLButtonElement).click();
    await fixture.whenStable();

    expect(navigate).toHaveBeenCalledWith(['/transactions'], {
      queryParams: {
        account: 'c1',
        date: '2026-08-03',
        amount: '-182.40',
        merchant: 'COSTCO WHSE #1102',
      },
    });
    // READ-ONLY on the ledger: the household confirms on the other page.
    expect(createTransaction).not.toHaveBeenCalled();
  });

  it('lets a viewer read the check but never store or add', async () => {
    const apiMock = cardApiMock({
      listCardStatements: vi.fn().mockResolvedValue(response({ statements: [cardStatement()] })),
      getStatementReconciliation: vi.fn().mockResolvedValue(
        response(
          reconciliation({
            lines: [statementLine({ matched_transaction_id: null, match_kind: null })],
            missing_from_sync_count: 1,
          }),
        ),
      ),
    });
    const { host } = await openReconciliation(apiMock, 'viewer');

    expect(host.querySelector('.statement-recon__line--missing')).toBeTruthy();
    expect(host.querySelector('.statement-recon__add')).toBeFalsy();
    expect(host.querySelector('.statement-lines-offer')).toBeFalsy();
  });
});
