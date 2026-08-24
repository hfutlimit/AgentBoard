import { Injectable, signal, inject } from '@angular/core';
import { DOCUMENT } from '@angular/common';

export type Theme = 'light' | 'dark';

@Injectable({
  providedIn: 'root'
})
export class ThemeService {
  private document = inject(DOCUMENT);
  private window = this.document.defaultView;
  private readonly storageKey = 'agentboard_theme';

  // State
  readonly currentTheme = signal<Theme>('light');

  constructor() {
    this.initTheme();
  }

  private initTheme(): void {
    if (!this.window) return;

    let initialTheme: Theme | null = null;
    
    // 1. Try local storage
    try {
      const stored = this.window.localStorage.getItem(this.storageKey);
      if (stored === 'light' || stored === 'dark') {
        initialTheme = stored;
      }
    } catch {}

    // 2. Fallback to OS preference
    const mediaQuery = typeof this.window.matchMedia === 'function'
      ? this.window.matchMedia('(prefers-color-scheme: dark)')
      : null;
    if (!initialTheme) {
      initialTheme = mediaQuery?.matches ? 'dark' : 'light';
    }

    this.applyTheme(initialTheme, false);

    if (!mediaQuery) return;

    // 3. Listen to OS changes if no manual override is set
    mediaQuery.addEventListener('change', (e) => {
      try {
        if (!this.window?.localStorage.getItem(this.storageKey)) {
          this.applyTheme(e.matches ? 'dark' : 'light', false);
        }
      } catch {}
    });
  }

  /**
   * Toggles the theme between light and dark.
   * If invoked by the user, uses View Transitions API for a smooth cross-fade animation.
   */
  toggleTheme(): void {
    const nextTheme = this.currentTheme() === 'dark' ? 'light' : 'dark';
    
    // Save to local storage since it's a manual override
    if (this.window) {
      try {
        this.window.localStorage.setItem(this.storageKey, nextTheme);
      } catch {}
    }

    this.applyTheme(nextTheme, true);
  }

  private applyTheme(theme: Theme, useTransition: boolean): void {
    const updateDOM = () => {
      this.document.documentElement.dataset['theme'] = theme;
      this.currentTheme.set(theme);
    };

    const reducedMotion = typeof this.window?.matchMedia === 'function'
      ? this.window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false;

    // Type casting document to any to access startViewTransition which might not be in TS types yet
    const doc = this.document as any;

    if (useTransition && doc.startViewTransition && !reducedMotion) {
      doc.startViewTransition(() => updateDOM());
    } else {
      updateDOM();
    }
  }
}
