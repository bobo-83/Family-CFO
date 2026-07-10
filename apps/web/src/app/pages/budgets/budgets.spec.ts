import { provideRouter } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Budgets } from './budgets';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

const BUDGETS = [
  {
    id: 'b1',
    category_id: 'c1',
    category_name: 'Dining',
    limit: { amount_minor: 50_000, currency: 'USD' },
    spent: { amount_minor: 60_000, currency: 'USD' },
    remaining: { amount_minor: -10_000, currency: 'USD' },
    percent_used: 120,
    status: 'over',
  },
  {
    id: 'b2',
    category_id: 'c2',
    category_name: 'Groceries',
    limit: { amount_minor: 80_000, currency: 'USD' },
    spent: { amount_minor: 20_000, currency: 'USD' },
    remaining: { amount_minor: 60_000, currency: 'USD' },
    percent_used: 25,
    status: 'under',
  },
];

async function settle(fixture: { detectChanges(): void; whenStable(): Promise<unknown> }) {
  // The page loads budgets + categories via Promise.all; give the extra
  // microtask hop time to land before asserting.
  for (let i = 0; i < 3; i++) {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  fixture.detectChanges();
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Budgets],
    providers: [
      provideRouter([]),
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Budgets', () => {
  it('renders envelopes with status and a capped progress bar', async () => {
    const apiMock = {
      listBudgets: vi.fn().mockResolvedValue(response({ budgets: BUDGETS })),
      listCategories: vi.fn().mockResolvedValue(
        response({ categories: [{ id: 'c1', name: 'Dining' }, { id: 'c2', name: 'Groceries' }] }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Budgets);
    await settle(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const text = host.textContent ?? '';
    expect(text).toContain('Over budget');
    expect(text).toContain('over by USD 100.00');
    expect(text).toContain('On track');
    // 120% renders a full (100%) bar, never overflowing.
    const overFill = host.querySelector('.budget__bar-fill--over') as HTMLElement;
    expect(overFill.style.width).toBe('100%');
  });

  it('creates a budget for a category without one', async () => {
    const apiMock = {
      listBudgets: vi
        .fn()
        .mockResolvedValueOnce(response({ budgets: [BUDGETS[0]] }))
        .mockResolvedValueOnce(response({ budgets: BUDGETS })),
      listCategories: vi.fn().mockResolvedValue(
        response({ categories: [{ id: 'c1', name: 'Dining' }, { id: 'c2', name: 'Groceries' }] }),
      ),
      createBudget: vi.fn().mockResolvedValue(response(BUDGETS[1])),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Budgets);
    await settle(fixture);

    const component = fixture.componentInstance;
    // Only the category without an envelope is offered.
    expect(component['availableCategories']().map((c) => c.id)).toEqual(['c2']);

    component['form'].setValue({ categoryId: 'c2', limit: 800 });
    await component['submit']();
    expect(apiMock.createBudget).toHaveBeenCalledWith({
      category_id: 'c2',
      limit: { amount_minor: 80_000, currency: 'USD' },
    });
  });

  it('deletes an envelope after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const apiMock = {
      listBudgets: vi
        .fn()
        .mockResolvedValueOnce(response({ budgets: [BUDGETS[0]] }))
        .mockResolvedValueOnce(response({ budgets: [] })),
      listCategories: vi.fn().mockResolvedValue(response({ categories: [] })),
      deleteBudget: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Budgets);
    await settle(fixture);

    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('.budget__delete')!
      .click();
    await settle(fixture);

    expect(apiMock.deleteBudget).toHaveBeenCalledWith('b1');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('No budgets yet.');
  });

  it('hides the create form for a viewer', async () => {
    const apiMock = {
      listBudgets: vi.fn().mockResolvedValue(response({ budgets: [] })),
      listCategories: vi.fn().mockResolvedValue(response({ categories: [] })),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Budgets);
    await settle(fixture);

    expect((fixture.nativeElement as HTMLElement).querySelector('.budget-form')).toBeNull();
  });
});
