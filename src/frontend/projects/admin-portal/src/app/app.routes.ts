import { Routes } from '@angular/router';
import { authGuard } from './auth.guard';

export const routes: Routes = [
  { path: '', redirectTo: 'login', pathMatch: 'full' },
  {
    path: 'login',
    loadComponent: () => import('./login/login').then((m) => m.Login),
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () => import('./dashboard/dashboard').then((m) => m.Dashboard),
  },
  {
    path: 'users',
    canActivate: [authGuard],
    loadComponent: () => import('./users/users').then((m) => m.Users),
  },
  {
    path: 'projects',
    canActivate: [authGuard],
    loadComponent: () => import('./projects/projects').then((m) => m.Projects),
  },
  {
    path: 'stats',
    canActivate: [authGuard],
    loadComponent: () => import('./stats/stats').then((m) => m.Stats),
  },
  { path: '**', redirectTo: 'login' },
];
