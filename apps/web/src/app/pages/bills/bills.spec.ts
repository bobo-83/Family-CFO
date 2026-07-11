import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Bills } from './bills';

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
    imports: [Bills],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

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
    await fixture.whenStable();
    fixture.detectChanges();

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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.bill-list__delete') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Suggested from your transactions');
    expect(host.textContent).toContain('4 charges');
    (host.querySelector('.bill-list__confirm') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const buttons = host.querySelectorAll('.bill-list--suggestions button');
    (buttons[1] as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.dismissBillSuggestion).toHaveBeenCalledWith('gym co');
    expect(host.querySelector('.bill-list--suggestions')).toBeNull();
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
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('Suggested updates');
    expect(host.textContent).toContain('USD 15.49');
    expect(host.textContent).toContain('USD 17.99');
    (host.querySelector('.bill-list__confirm') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.updateBill).toHaveBeenCalledWith('b1', {
      amount: { amount_minor: 1_799, currency: 'USD' },
      frequency: 'monthly',
      next_due_date: '2026-08-01',
    });
    expect(host.textContent).not.toContain('Suggested updates');
  });
});
