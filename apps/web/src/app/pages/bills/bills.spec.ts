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
    const apiMock = { listBills: vi.fn().mockResolvedValue(response({ bills: [] })) };
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
});
