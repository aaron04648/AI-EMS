import { Component, HostListener, signal } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { SITE } from '../../core/site.config';
import { Logo } from '../logo';
import { ThemeToggle } from '../theme-toggle';

@Component({
  selector: 'app-header',
  imports: [RouterLink, RouterLinkActive, Logo, ThemeToggle],
  templateUrl: './header.html',
  styleUrl: './header.scss',
})
export class Header {
  protected readonly scrolled = signal(false);
  protected readonly menuOpen = signal(false);
  protected readonly navItems = SITE.nav;

  @HostListener('window:scroll')
  onScroll(): void {
    this.scrolled.set(window.scrollY > 8);
  }

  toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  closeMenu(): void {
    this.menuOpen.set(false);
  }
}
