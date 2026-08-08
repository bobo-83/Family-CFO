import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Transactions } from './transactions';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function configure(
  apiMock: Record<string, unknown>,
  role: string,
  queryParams: Record<string, string> = {},
) {
  TestBed.configureTestingModule({
    imports: [Transactions],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
      provideRouter([]),
      // #25 links here from an unmatched statement line, carrying its values.
      { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap(queryParams) } } },
    ],
  });
}

describe('Transactions', () => {
  it('creates a transaction converting major units to minor', async () => {
    const apiMock = {
      listAccounts: vi
        .fn()
        .mockResolvedValue(
          response({
            accounts: [
              {
                id: 'a1',
                name: 'Checking',
                type: 'checking',
                balance: { amount_minor: 0, currency: 'USD' },
              },
            ],
          }),
        ),
      listTransactions: vi.fn().mockResolvedValue(response({ transactions: [] })),
      listCategories: vi.fn().mockResolvedValue(response({ categories: [] })),
      createTransaction: vi.fn().mockResolvedValue(response({ id: 't1' })),
    };
    configure(apiMock, 'adult');

    const fixture = TestBed.createComponent(Transactions);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    component['form'].setValue({
      accountId: 'a1',
      occurredAt: '2026-07-01',
      amount: -35.5,
      merchant: 'Grocer',
      description: '',
      categoryId: '',
    });
    await component['submit']();

    expect(apiMock.createTransaction).toHaveBeenCalledWith({
      account_id: 'a1',
      occurred_at: '2026-07-01',
      amount: { amount_minor: -3550, currency: 'USD' },
      merchant: 'Grocer',
      description: undefined,
      category_id: undefined,
    });
  });

  it('hides the create form for a viewer', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listTransactions: vi.fn().mockResolvedValue(response({ transactions: [] })),
      listCategories: vi.fn().mockResolvedValue(response({ categories: [] })),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Transactions);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.txn-form')).toBeFalsy();
  });

  it('#25: prefills from an unmatched statement line without recording it', async () => {
    const apiMock = {
      listAccounts: vi.fn().mockResolvedValue(
        response({
          accounts: [
            {
              id: 'c1',
              name: 'Visa',
              type: 'credit_card',
              balance: { amount_minor: 0, currency: 'USD' },
            },
          ],
        }),
      ),
      listTransactions: vi.fn().mockResolvedValue(response({ transactions: [] })),
      listCategories: vi.fn().mockResolvedValue(response({ categories: [] })),
      createTransaction: vi.fn().mockResolvedValue(response({ id: 't1' })),
    };
    configure(apiMock, 'adult', {
      account: 'c1',
      date: '2026-08-14',
      amount: '-182.40',
      merchant: 'COSTCO WHSE #1102',
    });

    const fixture = TestBed.createComponent(Transactions);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    expect(component['form'].getRawValue()).toMatchObject({
      accountId: 'c1',
      occurredAt: '2026-08-14',
      amount: -182.4,
      merchant: 'COSTCO WHSE #1102',
    });
    // Prefill ONLY: reconciliation must never move money on its own.
    expect(apiMock.createTransaction).not.toHaveBeenCalled();
    expect((fixture.nativeElement as HTMLElement).querySelector('.txn-form__prefilled')).toBeTruthy();
  });
});
