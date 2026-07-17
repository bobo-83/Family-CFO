import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Backups } from './backups';

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
    imports: [Backups],
    providers: [
      { provide: ApiService, useValue: apiMock },
      { provide: AuthService, useValue: { role: () => role } },
    ],
  });
}

describe('Backups', () => {
  it('hides everything for a non-owner', async () => {
    const apiMock = { listBackups: vi.fn().mockResolvedValue(response({ backups: [] })) };
    configure(apiMock, 'adult');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelector('.backups-actions')).toBeFalsy();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Only the household owner',
    );
  });

  it('creates a backup for an owner', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      createBackup: vi.fn().mockResolvedValue(response({ id: 'b1', status: 'completed' })),
      // M98: a fresh backup also refreshes the Synology (remote) list.
      listRemoteBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    await fixture.componentInstance['createBackup']();
    expect(apiMock.createBackup).toHaveBeenCalled();
  });

  it('confirms before restoring', async () => {
    const apiMock = {
      listBackups: vi.fn().mockResolvedValue(response({ backups: [] })),
      getBackupConfig: vi.fn().mockResolvedValue(response({ frequency: 'daily' })),
      restoreBackup: vi.fn().mockResolvedValue(response({ id: 'b1' })),
    };
    configure(apiMock, 'owner');

    const fixture = TestBed.createComponent(Backups);
    fixture.detectChanges();
    await fixture.whenStable();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await fixture.componentInstance['restore']('b1');
    expect(confirmSpy).toHaveBeenCalled();
    expect(apiMock.restoreBackup).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await fixture.componentInstance['restore']('b1');
    expect(apiMock.restoreBackup).toHaveBeenCalledWith('b1');
    confirmSpy.mockRestore();
  });
});
