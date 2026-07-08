import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';
import { Shell } from './shell/shell';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./pages/login/login').then((m) => m.Login),
  },
  {
    path: '',
    component: Shell,
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      {
        path: 'overview',
        loadComponent: () => import('./pages/overview/overview').then((m) => m.Overview),
      },
      {
        path: 'accounts',
        loadComponent: () => import('./pages/accounts/accounts').then((m) => m.Accounts),
      },
      {
        path: 'goals',
        loadComponent: () => import('./pages/goals/goals').then((m) => m.Goals),
      },
      {
        path: 'reports',
        loadComponent: () => import('./pages/reports/reports').then((m) => m.Reports),
      },
      {
        path: 'transactions',
        loadComponent: () =>
          import('./pages/transactions/transactions').then((m) => m.Transactions),
      },
      {
        path: 'imports',
        loadComponent: () => import('./pages/imports/imports').then((m) => m.Imports),
      },
      {
        path: 'ai-runtime',
        loadComponent: () => import('./pages/ai-runtime/ai-runtime').then((m) => m.AiRuntime),
      },
      {
        path: 'backups',
        loadComponent: () => import('./pages/backups/backups').then((m) => m.Backups),
      },
      {
        path: 'users',
        loadComponent: () => import('./pages/users/users').then((m) => m.Users),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
