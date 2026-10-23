import { Component, ElementRef, HostListener, inject, input, signal } from '@angular/core';

/**
 * Kleiner „?“-Knopf mit Erklärung im Popover – statt Fliesstext neben Grafiken.
 *
 *   <app-help>Erklärung …</app-help>
 *   <app-help align="right">…</app-help>   (Popover nach links öffnen, z. B. am rechten Rand)
 */
@Component({
  selector: 'app-help',
  template: `
    <button
      type="button"
      class="help__btn"
      [attr.aria-expanded]="open()"
      [attr.aria-label]="label()"
      (click)="open.set(!open())"
    >
      ?
    </button>
    @if (open()) {
      <div class="help__pop" role="tooltip" [class.help__pop--right]="align() === 'right'">
        <ng-content />
      </div>
    }
  `,
  styles: `
    :host {
      position: relative;
      display: inline-flex;
      vertical-align: middle;
      margin-left: 0.4rem;
      font-weight: 400;
      letter-spacing: normal;
      text-transform: none;
    }

    .help__btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      padding: 0;
      border: 1px solid var(--line);
      border-radius: 50%;
      background: var(--surface-raised);
      color: var(--muted);
      font: 600 12px/1 inherit;
      font-family: inherit;
      cursor: pointer;
      transition: color 0.2s, border-color 0.2s;

      &:hover,
      &[aria-expanded='true'] {
        color: var(--accent);
        border-color: var(--accent);
      }
    }

    .help__pop {
      position: absolute;
      top: calc(100% + 8px);
      left: -12px;
      z-index: 1000;
      width: max-content;
      max-width: min(300px, 80vw);
      padding: 0.75rem 0.9rem;
      border-radius: 12px;
      background: var(--help-bg, rgba(29, 29, 31, 0.96));
      color: #f5f5f7;
      border: 1px solid var(--line);
      font-size: 0.8125rem;
      line-height: 1.5;
      text-align: left;
      white-space: normal;
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.25);
    }

    .help__pop--right {
      left: auto;
      right: -12px;
    }
  `,
})
export class Help {
  readonly label = input('Erklärung anzeigen');
  readonly align = input<'left' | 'right'>('left');
  protected readonly open = signal(false);
  private readonly host = inject(ElementRef<HTMLElement>);

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (this.open() && !(this.host.nativeElement as HTMLElement).contains(event.target as Node)) {
      this.open.set(false);
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.open.set(false);
  }
}
