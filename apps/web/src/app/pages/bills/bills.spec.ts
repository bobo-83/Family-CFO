import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Bills } from './bills';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

/// Render, let the constructor's load() settle (its await chain crosses more
/// microtasks than whenStable tracks in zoneless mode), and render again.
async function stabilize(fixture: { detectChanges(): void; whenStable(): Promise<unknown> }) {
  fixture.detectChanges();
  await fixture.whenStable();
  await new Promise((resolve) => setTimeout(resolve));
  fixture.detectChanges();
}

function configure(apiMock: Record<string, unknown>, role: string) {
  // Every load fetches the payment timeline (M111); default to "no data" so
  // pre-timeline tests keep exercising the manage list unchanged.
  apiMock['getPaymentTimeline'] ??= vi.fn().mockResolvedValue(response(null));
  // Same for statement credits (M-credits).
  apiMock['listBillCredits'] ??= vi.fn().mockResolvedValue(response(null));
  TestBed.configureTestingModule({
    imports: [Bills],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

describe('Bills', () => {
  it('renders a row per bill', async () => {
    const apiMock = {
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      listBills: vi.fn().mockResolvedValue(
        response({
          bills: [
            {
              id: 'b1',
              name: 'Rent',
              amount: { amount_minor: 250_000, currency: 'USD' },
              frequency: 'monthly',
            },
            {
              id: 'b2',
              name: 'Car insurance',
              amount: { amount_minor: 60_000, currency: 'USD' },
              frequency: 'quarterly',
            },
          ],
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.bill-list__item');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Rent');
    expect(rows[0].textContent).toContain('USD 2,500.00');
    expect(rows[0].textContent).toContain('monthly');
  });

  it('hides the create form and delete button for a viewer', async () => {
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.bill-form')).toBeNull();
    expect(host.textContent).toContain('Only the household owner or an adult member can add bills.');
  });

  it('creates a bill for an owner and reloads the list', async () => {
    const apiMock = {
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      listBills: vi
        .fn()
        .mockResolvedValueOnce(response({ bills: [] }))
        .mockResolvedValueOnce(
          response({
            bills: [
              {
                id: 'b1',
                name: 'Internet',
                amount: { amount_minor: 8_000, currency: 'USD' },
                frequency: 'monthly',
              },
            ],
          }),
        ),
      createBill: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const nameInput = host.querySelector('input[formcontrolname="name"]') as HTMLInputElement;
    const amountInput = host.querySelector('input[formcontrolname="amount"]') as HTMLInputElement;
    const dueInput = host.querySelector('input[formcontrolname="nextDueDate"]') as HTMLInputElement;
    nameInput.value = 'Internet';
    nameInput.dispatchEvent(new Event('input'));
    amountInput.value = '80';
    amountInput.dispatchEvent(new Event('input'));
    dueInput.value = '2026-07-20';
    dueInput.dispatchEvent(new Event('input'));

    host.querySelector('form')!.dispatchEvent(new Event('submit'));
    await stabilize(fixture);

    expect(apiMock.createBill).toHaveBeenCalledWith({
      name: 'Internet',
      amount: { amount_minor: 8_000, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-07-20',
    });
    expect(apiMock.listBills).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('Internet');
  });

  it('deletes a bill after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const apiMock = {
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      listBills: vi
        .fn()
        .mockResolvedValueOnce(
          response({
            bills: [
              {
                id: 'b1',
                name: 'Gym',
                amount: { amount_minor: 4_000, currency: 'USD' },
                frequency: 'monthly',
              },
            ],
          }),
        )
        .mockResolvedValueOnce(response({ bills: [] })),
      deleteBill: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.bill-list__delete') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.deleteBill).toHaveBeenCalledWith('b1');
    expect(host.textContent).toContain('No bills yet.');
  });

  it('confirms a suggested bill and reloads both lists', async () => {
    const suggestion = {
      merchant_key: 'netflix com',
      name: 'NETFLIX.COM',
      amount: { amount_minor: 1_549, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
      occurrences: 4,
      last_seen: '2026-07-01',
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi
        .fn()
        .mockResolvedValueOnce(response({ suggestions: [suggestion] }))
        .mockResolvedValueOnce(response({ suggestions: [] })),
      createBill: vi.fn().mockResolvedValue(response({ id: 'b9' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Suggested from your transactions');
    expect(host.textContent).toContain('4 charges');
    (host.querySelector('.bill-list__confirm') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.createBill).toHaveBeenCalledWith({
      name: 'NETFLIX.COM',
      amount: { amount_minor: 1_549, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
    });
    expect(host.querySelector('.bill-list--suggestions')).toBeNull();
  });

  it('dismisses a suggested bill', async () => {
    const suggestion = {
      merchant_key: 'gym co',
      name: 'GYM CO',
      amount: { amount_minor: 4_000, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-05',
      occurrences: 3,
      last_seen: '2026-07-05',
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi
        .fn()
        .mockResolvedValueOnce(response({ suggestions: [suggestion] }))
        .mockResolvedValueOnce(response({ suggestions: [] })),
      dismissBillSuggestion: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const buttons = host.querySelectorAll('.bill-list--suggestions button');
    (buttons[1] as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.dismissBillSuggestion).toHaveBeenCalledWith('gym co');
    expect(host.querySelector('.bill-list--suggestions')).toBeNull();
  });

  it('renders the payment timeline grouped in bill-paying order (M111)', async () => {
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(
        response({
          items: [
            {
              id: 'c1',
              kind: 'credit_card',
              name: 'Costco Visa',
              amount: { amount_minor: 717_624, currency: 'USD' },
              due_date: '2026-07-21',
              days_until: 4,
              status: 'due_soon',
            },
            {
              id: 'b1',
              kind: 'bill',
              name: 'Water',
              amount: { amount_minor: 6_000, currency: 'USD' },
              due_date: '2026-07-12',
              days_until: -5,
              status: 'overdue',
            },
            {
              id: 'm1',
              kind: 'mortgage',
              name: 'MORTGAGE (8953)',
              amount: { amount_minor: 334_387, currency: 'USD' },
              due_date: '2026-08-01',
              days_until: 15,
              status: 'paid',
              paid_with: {
                transaction_id: 't1',
                occurred_at: '2026-07-01',
                amount: { amount_minor: 334_387, currency: 'USD' },
                label: 'Payment',
              },
            },
          ],
          due_total: { amount_minor: 723_624, currency: 'USD' },
          liquid_balance: { amount_minor: 1_632_600, currency: 'USD' },
          covered: true,
          window_days: 14,
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    // Headline: due vs cash, covered.
    expect(host.textContent).toContain('USD 7,236.24');
    expect(host.textContent).toContain('cash on hand');
    expect(host.textContent).toContain('Covered');
    // Groups render in bill-paying order with the card as a first-class row.
    const headers = Array.from(host.querySelectorAll('h2')).map((h) => h.textContent);
    expect(headers.indexOf('Overdue')).toBeLessThan(headers.indexOf('Due soon'));
    expect(headers.indexOf('Due soon')).toBeLessThan(headers.indexOf('Paid this cycle'));
    expect(host.textContent).toContain('Costco Visa');
    // The paid row carries its receipt.
    expect(host.textContent).toContain('Paid Jul 1');
  });

  it('marks only statement-backed card figures as exact (#11)', async () => {
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(
        response({
          items: [
            {
              id: 'c1',
              kind: 'credit_card',
              name: 'Costco Visa',
              amount: { amount_minor: 84_215, currency: 'USD' },
              due_date: '2026-08-14',
              days_until: 4,
              status: 'due_soon',
              source: 'statement',
              statement_id: 's1',
            },
            {
              id: 'c2',
              kind: 'credit_card',
              name: 'Amex',
              amount: { amount_minor: 51_000, currency: 'USD' },
              due_date: '2026-08-18',
              days_until: 8,
              status: 'due_soon',
              source: 'estimate',
            },
          ],
          due_total: { amount_minor: 135_215, currency: 'USD' },
          liquid_balance: { amount_minor: 900_000, currency: 'USD' },
          covered: true,
          window_days: 14,
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const chips = host.querySelectorAll('.from-statement');
    // Exactly one chip — the estimated card must never look exact.
    expect(chips.length).toBe(1);
    const exactRow = Array.from(host.querySelectorAll('.bill-list__item')).find((row) =>
      row.textContent?.includes('Costco Visa'),
    )!;
    expect(exactRow.querySelector('.from-statement')).toBeTruthy();
    const estimatedRow = Array.from(host.querySelectorAll('.bill-list__item')).find((row) =>
      row.textContent?.includes('Amex'),
    )!;
    expect(estimatedRow.querySelector('.from-statement')).toBeFalsy();
  });

  it('links a candidate charge with the row’s own due date ("I already paid this")', async () => {
    const timeline = {
      items: [
        {
          id: 'b1',
          kind: 'bill',
          name: 'Metro Power',
          amount: { amount_minor: 14_000, currency: 'USD' },
          due_date: '2026-07-20',
          days_until: -3,
          status: 'overdue',
        },
      ],
      due_total: { amount_minor: 14_000, currency: 'USD' },
      liquid_balance: { amount_minor: 100_000, currency: 'USD' },
      covered: true,
      window_days: 14,
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(response(timeline)),
      listBillPaymentCandidates: vi.fn().mockResolvedValue(
        response({
          transactions: [
            {
              id: 't1',
              account_id: 'a1',
              occurred_at: '2026-07-19T00:00:00Z',
              amount: { amount_minor: -13_250, currency: 'USD' },
              merchant: 'METRO POWER CO',
              account_name: 'Everyday Checking',
            },
          ],
        }),
      ),
      linkBillPayment: vi.fn().mockResolvedValue(response({ id: 'l1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.timeline-list__mark-paid') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.listBillPaymentCandidates).toHaveBeenCalledWith('b1', '2026-07-20');
    expect(host.textContent).toContain('Pick the charge that paid this bill');
    expect(host.textContent).toContain('METRO POWER CO');
    expect(host.textContent).toContain('Everyday Checking');

    (host.querySelector('.paylink-candidate') as HTMLButtonElement).click();
    await stabilize(fixture);

    // POST carries the row's OWN due date, and the page reloads the timeline.
    expect(apiMock.linkBillPayment).toHaveBeenCalledWith('b1', 't1', '2026-07-20');
    expect(apiMock.getPaymentTimeline).toHaveBeenCalledTimes(2);
  });

  it('surfaces the server’s error detail verbatim when linking is refused', async () => {
    const timeline = {
      items: [
        {
          id: 'b1',
          kind: 'bill',
          name: 'Metro Power',
          amount: { amount_minor: 14_000, currency: 'USD' },
          due_date: '2026-07-20',
          days_until: 2,
          status: 'due_soon',
        },
      ],
      due_total: { amount_minor: 14_000, currency: 'USD' },
      liquid_balance: { amount_minor: 100_000, currency: 'USD' },
      covered: true,
      window_days: 14,
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(response(timeline)),
      listBillPaymentCandidates: vi.fn().mockResolvedValue(
        response({
          transactions: [
            {
              id: 't1',
              account_id: 'a1',
              occurred_at: '2026-07-19T00:00:00Z',
              amount: { amount_minor: -13_250, currency: 'USD' },
              merchant: 'METRO POWER CO',
            },
          ],
        }),
      ),
      linkBillPayment: vi.fn().mockResolvedValue(
        response(undefined, {
          error: { code: 'conflict', message: 'That charge already pays another bill.' },
        }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.timeline-list__mark-paid') as HTMLButtonElement).click();
    await stabilize(fixture);
    (host.querySelector('.paylink-candidate') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(host.textContent).toContain('That charge already pays another bill.');
  });

  it('says so plainly when no candidate charges are found', async () => {
    const timeline = {
      items: [
        {
          id: 'b1',
          kind: 'bill',
          name: 'Metro Power',
          amount: { amount_minor: 14_000, currency: 'USD' },
          due_date: '2026-07-20',
          days_until: 2,
          status: 'due_soon',
        },
      ],
      due_total: { amount_minor: 14_000, currency: 'USD' },
      liquid_balance: { amount_minor: 100_000, currency: 'USD' },
      covered: true,
      window_days: 14,
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(response(timeline)),
      listBillPaymentCandidates: vi.fn().mockResolvedValue(response({ transactions: [] })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.timeline-list__mark-paid') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(host.textContent).toContain(
      'No charges found near this due date — sync your accounts or add the transaction first.',
    );
  });

  it('marks a linked receipt and unlinks it without confirmation', async () => {
    const timeline = {
      items: [
        {
          id: 'b1',
          kind: 'bill',
          name: 'Metro Power',
          amount: { amount_minor: 14_000, currency: 'USD' },
          due_date: '2026-08-20',
          days_until: 25,
          status: 'paid',
          paid_with: {
            transaction_id: 't1',
            occurred_at: '2026-07-19',
            amount: { amount_minor: 13_250, currency: 'USD' },
            label: 'METRO POWER CO',
            source: 'linked',
            link_id: 'l1',
          },
        },
      ],
      due_total: { amount_minor: 0, currency: 'USD' },
      liquid_balance: { amount_minor: 100_000, currency: 'USD' },
      covered: true,
      window_days: 14,
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(response(timeline)),
      unlinkBillPayment: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('linked by you');
    (host.querySelector('.timeline-list__unlink') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.unlinkBillPayment).toHaveBeenCalledWith('b1', 'l1');
    expect(apiMock.getPaymentTimeline).toHaveBeenCalledTimes(2);
  });

  it('keeps auto-matched paid rows free of the unlink affordance', async () => {
    const timeline = {
      items: [
        {
          id: 'b1',
          kind: 'bill',
          name: 'Metro Power',
          amount: { amount_minor: 14_000, currency: 'USD' },
          due_date: '2026-08-20',
          days_until: 25,
          status: 'paid',
          paid_with: {
            transaction_id: 't1',
            occurred_at: '2026-07-19',
            amount: { amount_minor: 14_000, currency: 'USD' },
            label: 'METRO POWER CO',
            source: 'matched',
            link_id: null,
          },
        },
      ],
      due_total: { amount_minor: 0, currency: 'USD' },
      liquid_balance: { amount_minor: 100_000, currency: 'USD' },
      covered: true,
      window_days: 14,
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      getPaymentTimeline: vi.fn().mockResolvedValue(response(timeline)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).not.toContain('linked by you');
    expect(host.querySelector('.timeline-list__unlink')).toBeNull();
    expect(host.querySelector('.timeline-list__mark-paid')).toBeNull();
  });

  it('edits a bill inline and saves through updateBill (M110 parity)', async () => {
    const bill = {
      id: 'b1',
      name: 'Rent',
      amount: { amount_minor: 250_000, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
    };
    const apiMock = {
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [] })),
      listBills: vi.fn().mockResolvedValue(response({ bills: [bill] })),
      updateBill: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.bill-list__confirm') as HTMLButtonElement).click();
    fixture.detectChanges();

    const form = host.querySelector('.bill-form--inline')!;
    const amountInput = form.querySelector('input[formcontrolname="amount"]') as HTMLInputElement;
    amountInput.value = '2600';
    amountInput.dispatchEvent(new Event('input'));
    form.dispatchEvent(new Event('submit'));
    await stabilize(fixture);

    expect(apiMock.updateBill).toHaveBeenCalledWith('b1', {
      name: 'Rent',
      amount: { amount_minor: 260_000, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
    });
  });

  it('applies a drift update to the existing bill after confirmation', async () => {
    const update = {
      bill_id: 'b1',
      name: 'Netflix',
      dismiss_key: 'netflix@1799',
      current_amount: { amount_minor: 1_549, currency: 'USD' },
      suggested_amount: { amount_minor: 1_799, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
      occurrences: 3,
      last_seen: '2026-07-01',
    };
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(response({ bills: [] })),
      listBillSuggestions: vi
        .fn()
        .mockResolvedValueOnce(response({ suggestions: [], updates: [update] }))
        .mockResolvedValueOnce(response({ suggestions: [], updates: [] })),
      updateBill: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Suggested updates');
    expect(host.textContent).toContain('USD 15.49');
    expect(host.textContent).toContain('USD 17.99');
    (host.querySelector('.bill-list__confirm') as HTMLButtonElement).click();
    await stabilize(fixture);

    expect(apiMock.updateBill).toHaveBeenCalledWith('b1', {
      amount: { amount_minor: 1_799, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
    });
    expect(host.textContent).not.toContain('Suggested updates');
  });

  it('renders statement credits with yearly totals and per-bill history', async () => {
    const apiMock = {
      listBills: vi.fn().mockResolvedValue(
        response({
          bills: [
            {
              id: 'b1',
              name: 'nationalgrid',
              amount: { amount_minor: 0, currency: 'USD' },
              frequency: 'monthly',
              next_due_date: null,
              category_id: null,
              category_name: null,
            },
          ],
        }),
      ),
      listBillSuggestions: vi.fn().mockResolvedValue(response({ suggestions: [], updates: [] })),
      listBillCredits: vi.fn().mockResolvedValue(
        response({
          bills: [
            {
              bill_id: 'b1',
              name: 'nationalgrid',
              total: { amount_minor: 21_115, currency: 'USD' },
              credits: [
                {
                  id: 'c2',
                  bill_id: 'b1',
                  amount: { amount_minor: 12_340, currency: 'USD' },
                  statement_date: '2026-07-15',
                },
                {
                  id: 'c1',
                  bill_id: 'b1',
                  amount: { amount_minor: 8_775, currency: 'USD' },
                  statement_date: '2026-06-15',
                },
              ],
            },
          ],
          monthly: [
            { month: '2026-07', total: { amount_minor: 12_340, currency: 'USD' } },
            { month: '2026-06', total: { amount_minor: 8_775, currency: 'USD' } },
          ],
          yearly: [{ year: 2026, total: { amount_minor: 21_115, currency: 'USD' } }],
        }),
      ),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Bills);
    await stabilize(fixture);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Statement credits');
    expect(host.textContent).toContain('nationalgrid');
    expect(host.textContent).toContain('USD 211.15');
    expect(host.textContent).toContain('USD 123.40');
    expect(host.textContent).toContain('2026-06');
    // The bill's own row carries its credit total too.
    expect(host.textContent).toContain('USD 211.15 in credits');
  });
});
