import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Memory } from './memory';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

function memoryDto(id: string, value: string, source = 'chat') {
  return {
    id,
    key: `k_${id}`,
    value,
    source,
    created_at: '2026-07-10T00:00:00Z',
    updated_at: '2026-07-10T00:00:00Z',
  };
}

function configure(apiMock: Record<string, unknown>, role: string) {
  TestBed.configureTestingModule({
    imports: [Memory],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Memory', () => {
  it('renders a row per remembered fact with its source', async () => {
    const apiMock = {
      listMemories: vi.fn().mockResolvedValue(
        response({
          memories: [
            memoryDto('m1', 'Lives in San Jose.'),
            memoryDto('m2', 'We rent our home.', 'manual'),
          ],
        }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Memory);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll('.memory-list__item');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Lives in San Jose.');
    expect(rows[0].textContent).toContain('Learned from chat');
    expect(rows[1].textContent).toContain('Taught directly');
  });

  it('hides the teach form and forget button for a viewer', async () => {
    const apiMock = {
      listMemories: vi
        .fn()
        .mockResolvedValue(response({ memories: [memoryDto('m1', 'Has two kids.')] })),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Memory);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.memory-form')).toBeNull();
    expect(host.querySelector('.memory-list__delete')).toBeNull();
  });

  it('teaches a fact for an owner and reloads the list', async () => {
    const apiMock = {
      listMemories: vi
        .fn()
        .mockResolvedValueOnce(response({ memories: [] }))
        .mockResolvedValueOnce(
          response({ memories: [memoryDto('m1', 'We eat out three times a week.', 'manual')] }),
        ),
      createMemory: vi.fn().mockResolvedValue(response(memoryDto('m1', 'x', 'manual'))),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Memory);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const input = host.querySelector('input[formcontrolname="value"]') as HTMLInputElement;
    input.value = 'We eat out three times a week.';
    input.dispatchEvent(new Event('input'));

    host.querySelector('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.createMemory).toHaveBeenCalledWith({
      value: 'We eat out three times a week.',
    });
    expect(apiMock.listMemories).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('We eat out three times a week.');
  });

  it('forgets a fact after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const apiMock = {
      listMemories: vi
        .fn()
        .mockResolvedValueOnce(response({ memories: [memoryDto('m1', 'Lives in San Jose.')] }))
        .mockResolvedValueOnce(response({ memories: [] })),
      deleteMemory: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Memory);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.memory-list__delete') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.deleteMemory).toHaveBeenCalledWith('m1');
    expect(host.textContent).toContain('Nothing remembered yet.');
  });
});
