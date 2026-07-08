import { client } from '../api-client/client.gen';
import { clearAuthState, getToken } from './token-store';

let configured = false;

/**
 * Registers the bearer-token request interceptor and the 401 handler on
 * the generated client. Called once from `main.ts` before bootstrap.
 */
export function configureApiClient(): void {
  if (configured) {
    return;
  }
  configured = true;

  client.interceptors.request.use((request) => {
    const token = getToken();
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`);
    }
    return request;
  });

  client.interceptors.response.use((response) => {
    if (response.status === 401) {
      clearAuthState();
    }
    return response;
  });
}
