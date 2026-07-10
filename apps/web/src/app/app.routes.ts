import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';
import { Shell } from './shell/shell';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./pages/login/login').then((m) => m.Login),
  },
  {
    path: 'signup',
    loadComponent: () => import('./pages/signup/signup').then((m) => m.Signup),
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
        path: 'chat',
        loadComponent: () => import('./pages/chat/chat').then((m) => m.Chat),
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
        path: 'bills',
        loadComponent: () => import('./pages/bills/bills').then((m) => m.Bills),
      },
      {
        path: 'categories',
        loadComponent: () =>
          import('./pages/categories/categories').then((m) => m.Categories),
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
