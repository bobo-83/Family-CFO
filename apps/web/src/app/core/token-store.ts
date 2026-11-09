import { computed, signal } from '@angular/core';

const LEGACY_KEY = 'family-cfo-auth';
const ACTIVE_KEY = 'family-cfo-auth-owner';
const SESSION_PREFIX = 'family-cfo-auth-session:';
const MAX_CLOCK_CHECK_MS = 30_000;

export interface StoredAuthState {
  accessToken: string;
  expiresAt: string;
  householdId: string;
  userId: string;
  role: string;
  roleName?: string;
  rights?: string[];
}

export type SignInReason = 'expired' | 'rejected';

/**
 * The shared session as it looked when an authentication request started
 * (#164 PR review). The tab-local generation cannot see a newer session that
 * another tab installed while this request was in flight — its storage event
 * may still be queued — so installation is fenced on the shared session
 * itself: the owner pointer *and* the record it names.
 *
 * The record matters as much as the pointer. Signing out removes the record
 * and deliberately leaves the pointer alone (the restore path reads it), so a
 * pointer-only fence cannot see a sign-out that happened in another tab.
 */
export interface SessionFence {
  pointer: string | null;
  record: string | null;
}

/** The shared session right now, or null when storage cannot be read at all. */
export function sessionFence(): SessionFence | null {
  try {
    const pointer = localStorage.getItem(ACTIVE_KEY);
    const record = pointer ? localStorage.getItem(SESSION_PREFIX + pointer) : null;
    return { pointer, record };
  } catch {
    // Storage is only a reload cache. Without it there is no shared session to
    // fence on, so the install runs unfenced instead of failing.
    return null;
  }
}

/** True when the shared session moved since `fence` was captured. */
export function sharedSessionMoved(fence: SessionFence | null): boolean {
  if (!fence) return false;
  const now = sessionFence();
  if (!now) return false;
  return now.pointer !== fence.pointer || now.record !== fence.record;
}

interface Session {
  owner: string;
  state: StoredAuthState;
}

const session = signal<Session | null>(null);
export const authState = computed(() => session()?.state ?? null);
export const authGeneration = signal(0);
export const signInReason = signal<SignInReason | null>(null);
export const needsKeyRecovery = signal(false);

let sealedHousehold: string | null = null;
let wallDeadline = 0;
let monotonicDeadline = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let persistentOwner: string | null = null;

type AuthStateListener = (state: StoredAuthState | null) => void;
const listeners = new Set<AuthStateListener>();

export function subscribeToAuthState(listener: AuthStateListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function publish(value: Session | null): void {
  if (value?.owner !== session()?.owner) authGeneration.update((generation) => generation + 1);
  session.set(value);
  for (const listener of [...listeners]) {
    try {
      listener(value?.state ?? null);
    } catch {
      // A failing listener must not block the rest of the transition.
    }
  }
}

function expiry(state: StoredAuthState): number {
  return typeof state.expiresAt === 'string' && /T.*(?:Z|[+-]\d\d:\d\d)$/.test(state.expiresAt)
    ? Date.parse(state.expiresAt)
    : NaN;
}

function remove(owner: string): void {
  try {
    // The pointer may already name another tab's new session. Only touch ours.
    localStorage.removeItem(SESSION_PREFIX + owner);
  } catch {
    // Storage is a reload cache. Memory still transitions.
  }
}

/**
 * Cross-tab serialization for the shared session (#164 PR review).
 *
 * The HTML storage standard gives no locking across browsing contexts, so a
 * read and the write that follows it can be separated by another tab's write:
 * a fence compared outside a lock is a read-then-write race, not a fence. The
 * Web Locks API is the one primitive that serializes same-origin tabs, so the
 * compare and the publication run inside it.
 */
const LOCK_NAME = 'family-cfo-auth-session';

interface LockManager {
  request?<T>(name: string, callback: () => T | Promise<T>): Promise<T>;
}

function withSessionLock<T>(critical: () => T): Promise<T> {
  const scope = globalThis.navigator as unknown as { locks?: LockManager } | undefined;
  const locks = scope?.locks;
  if (locks?.request) return locks.request(LOCK_NAME, () => Promise.resolve(critical()));
  // Without the API the section still runs in one go, but only against this
  // tab: the fence narrows the window instead of closing it.
  return Promise.resolve().then(critical);
}

function scheduleClockCheck(): void {
  clearTimeout(timer);
  timer = undefined;
  if (!session()) return;
  const remaining = Math.min(wallDeadline - Date.now(), monotonicDeadline - performance.now());
  if (remaining <= 0) {
    clearAuthState('expired');
    return;
  }
  timer = setTimeout(scheduleClockCheck, Math.min(remaining, MAX_CLOCK_CHECK_MS));
}

function install(value: Session): boolean {
  const deadline = expiry(value.state);
  if (!Number.isFinite(deadline) || deadline <= Date.now()) {
    remove(value.owner);
    clearAuthState(Number.isFinite(deadline) ? 'expired' : 'rejected');
    return false;
  }
  wallDeadline = deadline;
  monotonicDeadline = performance.now() + deadline - Date.now();
  signInReason.set(null);
  if (sealedHousehold !== value.state.householdId) sealedHousehold = null;
  needsKeyRecovery.set(false);
  publish(value);
  scheduleClockCheck();
  return true;
}

/**
 * Install a session as the shared one.
 *
 * The compare and the publication run inside the cross-tab lock: the fence is
 * only a fence when nothing can write between the comparison and the write.
 * A session that moved since the request started — a newer install, or a
 * sign-out that removed the record — means this response is stale, so it is
 * refused and the newer tab's record survives untouched.
 */
export async function setAuthState(
  state: StoredAuthState,
  fence?: SessionFence | null,
): Promise<boolean> {
  // getRandomValues is available even in an insecure local development context.
  const owner = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
  const deadline = expiry(state);
  if (!Number.isFinite(deadline) || deadline <= Date.now()) {
    if (!session()) signInReason.set(Number.isFinite(deadline) ? 'expired' : 'rejected');
    return false;
  }
  let refused = false;
  await withSessionLock(() => {
    try {
      const pointer = localStorage.getItem(ACTIVE_KEY);
      const record = pointer ? localStorage.getItem(SESSION_PREFIX + pointer) : null;
      if (fence && (pointer !== fence.pointer || record !== fence.record)) {
        refused = true;
        return;
      }
      localStorage.setItem(SESSION_PREFIX + owner, JSON.stringify(state));
      localStorage.setItem(ACTIVE_KEY, owner);
      persistentOwner = owner;
      if (pointer) remove(pointer);
    } catch {
      remove(owner);
      persistentOwner = null;
    }
  });
  if (refused) return false;
  return install({ owner, state });
}

/**
 * Remove this tab's shared record under the lock, so a sign-out cannot be
 * separated from the removal by a tab that is installing a session.
 */
async function removeSharedRecord(owner: string): Promise<void> {
  try {
    await withSessionLock(() => remove(owner));
  } catch {
    // Storage is a reload cache. The memory transition already happened.
  }
}

/**
 * Keep the stored login snapshot. A rights refresh updates only the live owner.
 *
 * This is a deliberate web tradeoff (confirmed in the #164 review): persisting
 * every rights refresh would rewrite the reload cache on each shell boot, and
 * the snapshot only ever feeds UI gating until the next refresh. Consequences:
 * a reload restores sign-in-time rights until the shell re-fetches them, and
 * an offline reload keeps serving that stale snapshot. The server checks every
 * request regardless. (iPhone differs: its rights refresh also persists, to
 * the Keychain, and the next launch starts from those rights.)
 */
export function updateSessionRights(
  owner: number,
  rights: Pick<StoredAuthState, 'role' | 'roleName' | 'rights'>,
): void {
  const current = session();
  if (current && ownsSession(owner)) publish({ ...current, state: { ...current.state, ...rights } });
}

export function clearAuthState(reason: SignInReason | null = null, keepSealRetry = false): Promise<void> {
  const owner = session()?.owner;
  clearTimeout(timer);
  timer = undefined;
  persistentOwner = null;
  signInReason.set(reason);
  if (!keepSealRetry) sealedHousehold = null;
  needsKeyRecovery.set(false);
  if (!owner) authGeneration.update((generation) => generation + 1);
  publish(null);
  // The shared record goes under the lock: a tab that is installing a session
  // must not be able to slip a write between this sign-out and the removal.
  return owner ? removeSharedRecord(owner) : Promise.resolve();
}

export function checkSessionExpiry(): void {
  // scheduleClockCheck owns the no-session guard; getToken() runs this on
  // every request, including while signed out.
  scheduleClockCheck();
}

export function getToken(): string | null {
  checkSessionExpiry();
  return authState()?.accessToken ?? null;
}

export function ownsSession(generation: number): boolean {
  checkSessionExpiry();
  return session() !== null && generation === authGeneration();
}

export function handleSealedHousehold(owner: number): void {
  const state = authState();
  if (!state || !ownsSession(owner)) return;
  if (sealedHousehold === state.householdId) {
    needsKeyRecovery.set(true);
    return;
  }
  sealedHousehold = state.householdId;
  clearAuthState(null, true);
}

export function noteHouseholdReadable(owner: number): void {
  if (!ownsSession(owner)) return;
  sealedHousehold = null;
  needsKeyRecovery.set(false);
}

function restoreSharedSession(initial = false): void {
  try {
    if (initial && localStorage.getItem(LEGACY_KEY) !== null) {
      localStorage.removeItem(LEGACY_KEY);
      signInReason.set('rejected');
    }
    const owner = localStorage.getItem(ACTIVE_KEY);
    if (owner && !/^[a-f0-9]{32}$/.test(owner)) {
      clearAuthState('rejected');
      return;
    }
    const raw = owner ? localStorage.getItem(SESSION_PREFIX + owner) : null;
    if (!owner || !raw) {
      if (persistentOwner) {
        const reason = wallDeadline <= Date.now() || monotonicDeadline <= performance.now()
          ? 'expired' : null;
        clearAuthState(reason);
      }
      return;
    }
    if (owner === session()?.owner) return;
    let value: StoredAuthState;
    try {
      value = JSON.parse(raw) as StoredAuthState;
      if (![value?.accessToken, value?.userId, value?.householdId, value?.role]
        .every((field) => typeof field === 'string' && field.length > 0)
        || (value.rights !== undefined && !Array.isArray(value.rights))) {
        throw new Error('Invalid stored session');
      }
    } catch {
      // Outside the lock, but fail-safe: a record that disappears makes another
      // tab's fence refuse, never accept.
      remove(owner);
      clearAuthState('rejected');
      return;
    }
    // This tab's own superseded record. No other tab's fence names it.
    if (persistentOwner && persistentOwner !== owner) remove(persistentOwner);
    if (install({ owner, state: value })) persistentOwner = owner;
  } catch {
    // The application can still sign in without persistence.
  }
}

if (typeof window !== 'undefined') {
  restoreSharedSession(true);
  window.addEventListener('storage', ({ key }) => {
    if (key === null || key === ACTIVE_KEY || key?.startsWith(SESSION_PREFIX)) restoreSharedSession();
  });
  const resume = () => {
    restoreSharedSession();
    checkSessionExpiry();
  };
  window.addEventListener('focus', resume);
  window.addEventListener('pageshow', resume);
  document.addEventListener('visibilitychange', resume);
}
