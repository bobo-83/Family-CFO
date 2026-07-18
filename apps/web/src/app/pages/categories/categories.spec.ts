import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { authMock } from '../../shared/testing-auth';
import { Categories } from './categories';

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
    imports: [Categories],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: authMock(role) },
    ],
  });
}

describe('Categories', () => {
  it('lists categories', async () => {
    const apiMock = {
      listCategories: vi.fn().mockResolvedValue(
        response({ categories: [{ id: 'c1', name: 'Dining' }, { id: 'c2', name: 'Transport' }] }),
      ),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Categories);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelectorAll('.category-list__item').length).toBe(2);
    expect(host.textContent).toContain('Dining');
    // Viewer sees no create form.
    expect(host.querySelector('.category-form')).toBeNull();
  });

  it('creates a category and reloads', async () => {
    const apiMock = {
      listCategories: vi
        .fn()
        .mockResolvedValueOnce(response({ categories: [] }))
        .mockResolvedValueOnce(response({ categories: [{ id: 'c1', name: 'Dining' }] })),
      createCategory: vi.fn().mockResolvedValue(response({ id: 'c1', name: 'Dining' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Categories);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const input = host.querySelector('input[formcontrolname="name"]') as HTMLInputElement;
    input.value = 'Dining';
    input.dispatchEvent(new Event('input'));
    host.querySelector('form')!.dispatchEvent(new Event('submit'));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.createCategory).toHaveBeenCalledWith({ name: 'Dining' });
    expect(host.textContent).toContain('Dining');
  });

  it('deletes a category after confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const apiMock = {
      listCategories: vi
        .fn()
        .mockResolvedValueOnce(response({ categories: [{ id: 'c1', name: 'Dining' }] }))
        .mockResolvedValueOnce(response({ categories: [] })),
      deleteCategory: vi.fn().mockResolvedValue(response(undefined)),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Categories);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    (host.querySelector('.category-list__delete') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiMock.deleteCategory).toHaveBeenCalledWith('c1');
    expect(host.textContent).toContain('No categories yet.');
  });
});
