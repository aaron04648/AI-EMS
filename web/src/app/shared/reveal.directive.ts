import {
  Directive,
  ElementRef,
  OnDestroy,
  OnInit,
  PLATFORM_ID,
  inject,
  input,
} from '@angular/core';
import { isPlatformBrowser } from '@angular/common';

export type RevealVariant = 'fade' | 'slide-up' | 'slide-left' | 'slide-right' | 'scale';

/**
 * Apple-ähnliche Scroll-Animationen auf Basis der Intersection Observer API.
 *
 * Verwendung im Template:
 *   <div appReveal>…</div>                          (Slide-up, Standard)
 *   <div appReveal="fade">…</div>
 *   <div appReveal="slide-up" [revealDelay]="150">…</div>
 *
 * Beim Prerendering bleibt das Element unangetastet – der Inhalt steht damit
 * sichtbar im ausgelieferten HTML (wichtig für Suchmaschinen und den Largest
 * Contentful Paint). Erst im Browser werden Elemente unterhalb des sichtbaren
 * Bereichs für die Einblend-Animation vorbereitet.
 */
@Directive({
  selector: '[appReveal]',
})
export class RevealDirective implements OnInit, OnDestroy {
  readonly appReveal = input<RevealVariant | ''>('');
  readonly revealDelay = input(0);

  private readonly el = inject(ElementRef<HTMLElement>);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));
  private observer?: IntersectionObserver;

  ngOnInit(): void {
    if (!this.isBrowser) {
      return;
    }

    const element = this.el.nativeElement as HTMLElement;

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const alreadyVisible = element.getBoundingClientRect().top < window.innerHeight;

    // Bereits sichtbare Inhalte nicht nachträglich ausblenden – das würde nach
    // dem Hydrieren des vorgerenderten HTML sichtbar flackern.
    if (prefersReducedMotion || typeof IntersectionObserver === 'undefined' || alreadyVisible) {
      return;
    }

    const variant = this.appReveal() || 'slide-up';
    element.classList.add('reveal');
    if (variant !== 'fade') {
      element.classList.add(`reveal--${variant}`);
    }
    if (this.revealDelay() > 0) {
      element.style.setProperty('--reveal-delay', `${this.revealDelay()}ms`);
    }

    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            element.classList.add('is-visible');
            this.observer?.unobserve(element);
          }
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -40px 0px' },
    );
    this.observer.observe(element);
  }

  ngOnDestroy(): void {
    this.observer?.disconnect();
  }
}
