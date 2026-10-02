import { Component } from '@angular/core';

/** Wortmarke: Blitz im abgerundeten Quadrat + „AI-EMS“. */
@Component({
  selector: 'app-logo',
  template: `
    <svg viewBox="0 0 28 28" width="24" height="24" aria-hidden="true">
      <defs>
        <linearGradient id="logo-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#2997ff" />
          <stop offset="1" stop-color="#0071e3" />
        </linearGradient>
      </defs>
      <rect width="28" height="28" rx="7" fill="url(#logo-grad)" />
      <path d="M15.5 5 8.5 15.5h5L12 23l7.5-11h-5.2z" fill="#fff" />
    </svg>
    <span>AI-EMS</span>
  `,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 700;
      font-size: 1.0625rem;
      letter-spacing: -0.02em;
      color: currentColor;
    }
  `,
})
export class Logo {}
