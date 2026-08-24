import { ApplicationConfig, isDevMode, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withComponentInputBinding, withViewTransitions } from '@angular/router';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    // 仅开发模式显示未捕获错误 banner；生产模式禁用，避免浏览器扩展
    // (如 Angular DevTools) 抛出的非致命警告被展示给用户。
    ...(isDevMode() ? [provideBrowserGlobalErrorListeners()] : []),
    provideHttpClient(),
    // withComponentInputBinding：让路由 data / params 自动绑定到 component @Input。
    // Story 348 #1430：5 个全局路由（/epics /stories /tasks /bugs /dashboard）
    // 共用 GlobalStatsTabComponent，靠 data.entity @Input 切标题 / 高亮。
    provideRouter(routes, withComponentInputBinding(), withViewTransitions()),
  ],
};
