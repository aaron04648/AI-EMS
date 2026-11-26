import { DOCUMENT } from '@angular/common';
import { Injectable, inject, signal } from '@angular/core';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'ai-ems-theme';

/**
 * Light/dark mode. The data-theme attribute on <html> is already set by an inline script in
 * index.html (against flashing); this service takes it over, toggles it and remembers the choice.
 * Without a stored choice the site follows the system setting – even when it changes.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);
  readonly theme = signal<Theme>(this.initial());

  constructor() {
    this.apply(this.theme());
    const media = this.document.defaultView?.matchMedia?.('(prefers-color-scheme: dark)');
    media?.addEventListener('change', (e) => {
      if (!this.stored()) this.set(e.matches ? 'dark' : 'light', false);
    });
  }

  toggle(): void {
    this.set(this.theme() === 'dark' ? 'light' : 'dark', true);
  }

  private set(theme: Theme, remember: boolean): void {
    this.theme.set(theme);
    this.apply(theme);
    if (remember) {
      try {
        localStorage.setItem(STORAGE_KEY, theme);
      } catch {
        // convenience only
      }
    }
  }

  private apply(theme: Theme): void {
    const root = this.document.documentElement;
    root.setAttribute('data-theme', theme);
    this.document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#000000' : '#ffffff');
  }

  private stored(): Theme | null {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'dark' || v === 'light' ? v : null;
    } catch {
      return null;
    }
  }

  private initial(): Theme {
    const attr = this.document.documentElement.getAttribute('data-theme');
    if (attr === 'dark' || attr === 'light') return attr;
    return this.stored() ?? (this.document.defaultView?.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
}
