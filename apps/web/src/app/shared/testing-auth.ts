/**
 * Test double for AuthService (ADR 0034): components gate by hasRight, so every
 * spec's auth mock derives rights from the legacy tier exactly like the
 * production fallback does.
 */
const ADMIN_ONLY = [
  'accounts.manage',
  'imports.manage',
  'connections.manage',
  'reports.manage',
  'members.manage',
  'roles.manage',
  'devices.manage',
  'backups.manage',
  'audit.view',
  'household.settings.manage',
  'ai_runtime.manage',
];

export function authMock(
  role: string,
  userId: string | null = 'current-user',
): { role: () => string; userId: () => string | null; hasRight: (right: string) => boolean } {
  return {
    role: () => role,
    userId: () => userId,
    hasRight: (right: string): boolean => {
      if (role === 'owner') {
        return true;
      }
      if (role === 'adult') {
        return !ADMIN_ONLY.includes(right);
      }
      return ['finances.view', 'advisor.use'].includes(right);
    },
  };
}
