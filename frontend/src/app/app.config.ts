import { ApplicationConfig, isDevMode, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    // 仅开发模式显示未捕获错误 banner；生产模式禁用，避免浏览器扩展
    // (如 Angular DevTools) 抛出的非致命警告被展示给用户。
    ...(isDevMode() ? [provideBrowserGlobalErrorListeners()] : []),
    provideHttpClient(),
    provideRouter(routes),
  ],
};
