import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';
import { checkSessionExpiry } from './token-store';

export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  checkSessionExpiry();
  return auth.isAuthenticated() ? true : router.parseUrl('/login');
};
