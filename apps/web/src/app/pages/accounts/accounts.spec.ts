import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { Accounts } from './accounts';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Accounts', () => {
  let apiMock: { listAccounts: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    apiMock = { listAccounts: vi.fn() };
    TestBed.configureTestingModule({
      imports: [Accounts],
      providers: [{ provide: ApiService, useValue: apiMock }],
    });
  });

  it('renders a row per account', async () => {
    apiMock.listAccounts.mockResolvedValue(
      response({
        accounts: [
          { id: 'a1', name: 'Checking', type: 'checking', balance: { amount_minor: 500_000, currency: 'USD' } },
          { id: 'a2', name: 'Savings', type: 'savings', balance: { amount_minor: 1_500_000, currency: 'USD' } },
        ],
      }),
    );

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Checking');
    expect(rows[0].textContent).toContain('USD 5,000.00');
  });

  it('renders an empty state when there are no accounts', async () => {
    apiMock.listAccounts.mockResolvedValue(response({ accounts: [] }));

    const fixture = TestBed.createComponent(Accounts);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const empty = (fixture.nativeElement as HTMLElement).querySelector('.page-empty');
    expect(empty?.textContent).toContain('No accounts yet');
  });
});
