import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Reports } from './reports';

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
    imports: [Reports],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Reports', () => {
  it('generates a weekly report for an owner', async () => {
    const apiMock = {
      listReports: vi.fn().mockResolvedValue(response({ reports: [] })),
      generateReport: vi.fn().mockResolvedValue(response({ id: 'r1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Reports);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    await fixture.componentInstance['generate']('weekly');
    expect(apiMock.generateReport).toHaveBeenCalledWith({ report_type: 'weekly' });
  });

  it('renders a report summary and hides generate controls for a viewer', async () => {
    const apiMock = {
      listReports: vi.fn().mockResolvedValue(
        response({
          reports: [
            {
              id: 'r1',
              report_type: 'weekly',
              period_start: '2026-06-29',
              period_end: '2026-07-05',
              explanation_text: 'Looking good.',
              explanation_source: 'deterministic_stub',
              generated_at: '2026-07-06T00:00:00Z',
              summary: {
                wins: ['Stayed within budget'],
                risks: [],
                unusual_spending: [],
                recommended_actions: [],
                goal_progress: [],
                net_cash_flow: { amount_minor: 12_345, currency: 'USD' },
                calculation_refs: [],
              },
            },
          ],
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Reports);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Stayed within budget');
    expect(text).toContain('USD 123.45');
    expect((fixture.nativeElement as HTMLElement).querySelector('.reports-actions')).toBeFalsy();
  });
});
