import { Component, inject } from '@angular/core';
import { ThemeService } from '../core/theme.service';

/** Sonne/Mond-Knopf zum Umschalten zwischen Hell- und Dunkelmodus. */
@Component({
  selector: 'app-theme-toggle',
  template: `
    <button
      type="button"
      class="theme-toggle"
      [attr.aria-label]="theme.theme() === 'dark' ? 'Hellen Modus aktivieren' : 'Dunklen Modus aktivieren'"
      [attr.title]="theme.theme() === 'dark' ? 'Heller Modus' : 'Dunkler Modus'"
      (click)="theme.toggle()"
    >
      @if (theme.theme() === 'dark') {
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <circle cx="12" cy="12" r="4.5" />
          <g stroke-width="2" stroke-linecap="round">
            <path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8 6 18M18 6l1.8-1.8" />
          </g>
        </svg>
      } @else {
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M20.5 14.6A8.5 8.5 0 0 1 9.4 3.5a8.5 8.5 0 1 0 11.1 11.1Z" />
        </svg>
      }
    </button>
  `,
  styles: `
    .theme-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      padding: 0;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: var(--surface-raised);
      color: var(--ink);
      cursor: pointer;
      transition: border-color 0.2s, color 0.2s;

      &:hover {
        border-color: var(--accent);
        color: var(--accent);
      }

      svg {
        fill: currentColor;
        stroke: currentColor;
      }
    }
  `,
})
export class ThemeToggle {
  protected readonly theme = inject(ThemeService);
}
