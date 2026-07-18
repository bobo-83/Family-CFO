import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Goals } from './goals';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

describe('Goals', () => {
  let apiMock: { listGoals: ReturnType<typeof vi.fn>; createGoal: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    apiMock = { listGoals: vi.fn(), createGoal: vi.fn() };
  });

  it('shows the create form for an owner and creates a goal', async () => {
    apiMock.listGoals.mockResolvedValue(response({ goals: [] }));
    apiMock.createGoal.mockResolvedValue(
      response({
        id: 'g1',
        name: 'New car fund',
        type: 'vehicle',
        target: { amount_minor: 500_000, currency: 'USD' },
        current: { amount_minor: 0, currency: 'USD' },
        priority: 3,
      }),
    );

    TestBed.configureTestingModule({
      imports: [Goals],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('owner') },
      ],
    });
    const fixture = TestBed.createComponent(Goals);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const form = (fixture.nativeElement as HTMLElement).querySelector('.goal-form');
    expect(form).toBeTruthy();

    const component = fixture.componentInstance;
    component['form'].setValue({
      name: 'New car fund',
      type: 'vehicle',
      targetAmount: 5000,
      priority: 2,
      monthlyContribution: 0,
    });
    await component['submit']();

    expect(apiMock.createGoal).toHaveBeenCalledWith({
      name: 'New car fund',
      type: 'vehicle',
      target: { amount_minor: 500_000, currency: 'USD' },
      priority: 2,
    });
  });

  it('hides the create form for a viewer', async () => {
    apiMock.listGoals.mockResolvedValue(response({ goals: [] }));

    TestBed.configureTestingModule({
      imports: [Goals],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: authMock('viewer') },
      ],
    });
    const fixture = TestBed.createComponent(Goals);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const form = (fixture.nativeElement as HTMLElement).querySelector('.goal-form');
    expect(form).toBeFalsy();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Only the household owner or an adult member can add goals.');
  });
});
