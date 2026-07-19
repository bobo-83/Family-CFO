import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { Roles } from './roles';

function response(data: unknown, error?: unknown) {
  return {
    data,
    error,
    request: new Request('http://localhost/'),
    response: new Response(),
  } as never;
}

const ROLES_PAYLOAD = {
  all_rights: ['finances.view', 'accounts.manage', 'roles.manage'],
  roles: [
    {
      id: 'r1',
      name: 'Admin',
      built_in: true,
      rights: ['roles.manage', 'accounts.manage', 'finances.view'],
      member_count: 1,
    },
    {
      id: 'r2',
      name: 'Teen',
      built_in: false,
      rights: ['finances.view'],
      member_count: 0,
    },
  ],
};

describe('Roles', () => {
  let apiMock: {
    listRoles: ReturnType<typeof vi.fn>;
    createRole: ReturnType<typeof vi.fn>;
    updateRole: ReturnType<typeof vi.fn>;
    deleteRole: ReturnType<typeof vi.fn>;
  };

  async function render() {
    TestBed.configureTestingModule({
      imports: [Roles],
      providers: [{ provide: ApiService, useValue: apiMock }],
    });
    const fixture = TestBed.createComponent(Roles);
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve)); // flush the resource loader
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  beforeEach(() => {
    apiMock = {
      listRoles: vi.fn().mockResolvedValue(response(ROLES_PAYLOAD)),
      createRole: vi.fn().mockResolvedValue(response({})),
      updateRole: vi.fn().mockResolvedValue(response({})),
      deleteRole: vi.fn().mockResolvedValue(response({})),
    };
  });

  it('drills down to a role’s rights, sorted, for the read-only built-in role', async () => {
    const host = await render();

    const adminItem = host.querySelectorAll('.role-list__item')[0];
    const rights = [...adminItem.querySelectorAll('.role-rights__item code')].map(
      (c) => c.textContent,
    );
    // Every right is shown (not just a count), and grouped by area (sorted).
    expect(rights).toEqual(['accounts.manage', 'finances.view', 'roles.manage']);
    // Human labels ride alongside the raw right.
    expect(adminItem.textContent).toContain('Accounts — manage');
  });

  it('keeps built-in (default) roles read-only — no edit or delete', async () => {
    const host = await render();

    const adminItem = host.querySelectorAll('.role-list__item')[0];
    expect(adminItem.textContent).toContain('built-in');
    expect(adminItem.querySelector('.role-list__actions')).toBeNull();
  });

  it('offers edit and delete on custom roles', async () => {
    const host = await render();

    const customItem = host.querySelectorAll('.role-list__item')[1];
    const actions = customItem.querySelector('.role-list__actions');
    expect(actions).not.toBeNull();
    expect(actions?.textContent).toContain('Edit');
    expect(actions?.textContent).toContain('Delete');
  });
});
