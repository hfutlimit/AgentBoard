import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

const TOKEN_KEY = 'admin_portal_token';

export const authGuard: CanActivateFn = () => {
  if (typeof localStorage !== 'undefined' && localStorage.getItem(TOKEN_KEY)) {
    return true;
  }
  return inject(Router).createUrlTree(['/login']);
};
