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
 * Apple-like scroll animations based on the Intersection Observer API.
 *
 * Usage in the template:
 *   <div appReveal>…</div>                          (slide-up, default)
 *   <div appReveal="fade">…</div>
 *   <div appReveal="slide-up" [revealDelay]="150">…</div>
 *
 * During prerendering the element stays untouched – so the content is
 * visible in the delivered HTML (important for search engines and the Largest
 * Contentful Paint). Only in the browser are elements below the visible
 * area prepared for the fade-in animation.
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

    // Do not hide already visible content afterwards – that would visibly flicker
    // after hydrating the prerendered HTML.
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
