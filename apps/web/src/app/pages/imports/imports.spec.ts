import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Imports } from './imports';

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
    imports: [Imports],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Imports', () => {
  it('registers then uploads a selected file', async () => {
    const apiMock = {
      listConnections: vi.fn().mockResolvedValue(response({ connections: [] })),
      createConnection: vi.fn(),
      deleteConnection: vi.fn(),
      syncConnection: vi.fn(),
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
      listImports: vi.fn().mockResolvedValue(response({ imports: [] })),
      createImport: vi.fn().mockResolvedValue(response({ id: 'i1', status: 'pending' })),
      uploadImportFile: vi.fn().mockResolvedValue(response({ id: 'i1', status: 'pending' })),
    };
    configure(apiMock, 'adult');

    const fixture = TestBed.createComponent(Imports);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const component = fixture.componentInstance;
    component['form'].setValue({ sourceType: 'csv', accountId: 'a1' });
    const file = new File(['date,amount\n2026-01-01,-1.00\n'], 'statement.csv', {
      type: 'text/csv',
    });
    component['selectedFile'] = file;
    await component['submit']();

    expect(apiMock.createImport).toHaveBeenCalledWith({
      source_type: 'csv',
      filename: 'statement.csv',
      account_id: 'a1',
    });
    expect(apiMock.uploadImportFile).toHaveBeenCalledWith('i1', file);
  });

  it('applies a needs_review import', async () => {
    const apiMock = {
      listConnections: vi.fn().mockResolvedValue(response({ connections: [] })),
      createConnection: vi.fn(),
      deleteConnection: vi.fn(),
      syncConnection: vi.fn(),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listImports: vi
        .fn()
        .mockResolvedValue(
          response({
            imports: [
              {
                id: 'i2',
                source_type: 'csv',
                filename: 'a.csv',
                status: 'needs_review',
                skipped_row_count: 0,
              },
            ],
          }),
        ),
      applyImport: vi.fn().mockResolvedValue(response({ id: 'i2', status: 'completed' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Imports);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    await fixture.componentInstance['apply']('i2');
    expect(apiMock.applyImport).toHaveBeenCalledWith('i2');
  });

  it('hides the import form for a viewer', async () => {
    const apiMock = {
      listConnections: vi.fn().mockResolvedValue(response({ connections: [] })),
      createConnection: vi.fn(),
      deleteConnection: vi.fn(),
      syncConnection: vi.fn(),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listImports: vi.fn().mockResolvedValue(response({ imports: [] })),
    };
    configure(apiMock, 'viewer');

    const fixture = TestBed.createComponent(Imports);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.import-form')).toBeFalsy();
  });

  it('links an institution and syncs with counts', async () => {
    const apiMock = {
      listConnections: vi.fn().mockResolvedValue(
        response({
          connections: [
            {
              id: 'conn-1',
              provider: 'simplefin',
              display_name: 'My Bank',
              status: 'active',
              last_synced_at: null,
              last_sync_error: null,
              created_at: '2026-07-09T00:00:00Z',
            },
          ],
        }),
      ),
      createConnection: vi.fn().mockResolvedValue(response({ id: 'conn-1' })),
      deleteConnection: vi.fn(),
      syncConnection: vi.fn().mockResolvedValue(
        response({ accounts_synced: 1, imported: 5, duplicates_skipped: 2 }),
      ),
      listAccounts: vi.fn().mockResolvedValue(response({ accounts: [] })),
      listImports: vi.fn().mockResolvedValue(response({ imports: [] })),
      createImport: vi.fn(),
      uploadImportFile: vi.fn(),
      applyImport: vi.fn(),
      discardImport: vi.fn(),
    };
    TestBed.configureTestingModule({
      imports: [Imports],
      providers: [
        { provide: ApiService, useValue: apiMock },
        { provide: AuthService, useValue: { role: () => 'owner' } },
      ],
    });
    const fixture = TestBed.createComponent(Imports);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component['connectionForm'].setValue({ displayName: 'My Bank', setupToken: 'Z29vZC10b2tlbg==' });
    await component['linkInstitution']();
    expect(apiMock.createConnection).toHaveBeenCalledWith({
      provider: 'simplefin',
      display_name: 'My Bank',
      setup_token: 'Z29vZC10b2tlbg==',
    });

    await component['syncNow']('conn-1');
    expect(component['syncMessage']()).toContain('5 new');
    expect(component['syncMessage']()).toContain('2 duplicate');
  });
});
