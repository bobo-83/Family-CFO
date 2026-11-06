import { client } from '../api-client/client.gen';
import {
  authGeneration,
  clearAuthState,
  getToken,
  handleSealedHousehold,
  noteHouseholdReadable,
  ownsSession,
} from './token-store';

let configured = false;
const requestOwners = new WeakMap<Request, number>();

async function isNewMemberLock(response: Response): Promise<boolean> {
  try {
    // The form still needs the original response body (#103).
    const body = (await response.clone().json()) as { error?: { code?: string } };
    return body?.error?.code === 'household_locked_new_member';
  } catch {
    return false;
  }
}

export async function handleSessionResponse(response: Response, owner?: number): Promise<Response> {
  if (owner === undefined || !ownsSession(owner)) return response;
  if (response.status === 401) clearAuthState('rejected');
  if (response.status === 423 && !(await isNewMemberLock(response))) handleSealedHousehold(owner);
  return response;
}

export async function fetchProtected(path: string, init?: RequestInit): Promise<Response> {
  const token = getToken();
  const owner = authGeneration();
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!token) return response;
  if (!ownsSession(owner)) throw new DOMException('Session changed', 'AbortError');
  return handleSessionResponse(response, owner);
}

export function configureApiClient(): void {
  if (configured) return;
  configured = true;

  client.interceptors.request.use((request, options) => {
    // The generated SDK exposes OpenAPI security here. Public operations never
    // gain a bearer or ownership merely because a member is already signed in.
    if (options.security?.some(({ scheme }) => scheme === 'bearer')) {
      const token = getToken();
      if (token) {
        request.headers.set('Authorization', `Bearer ${token}`);
        requestOwners.set(request, authGeneration());
      }
    }
    const locale = document.documentElement.lang;
    if (locale) request.headers.set('Accept-Language', locale);
    return request;
  });

  client.interceptors.response.use(async (response, request, options) => {
    const owner = requestOwners.get(request);
    if (owner === undefined) return response;
    if (!ownsSession(owner)) throw new DOMException('Session changed', 'AbortError');
    if (response.ok && options.url === '/household') noteHouseholdReadable(owner);
    return handleSessionResponse(response, owner);
  });
}
