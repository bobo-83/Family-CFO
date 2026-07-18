import { TestBed } from '@angular/core/testing';
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

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Transactions],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
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
});
