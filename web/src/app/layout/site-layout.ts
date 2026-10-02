import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Header } from '../shared/header/header';
import { Footer } from '../shared/footer/footer';

/** Rahmen der Webseite (Übersicht, Analyse) – der Rechner hat ein eigenes Layout. */
@Component({
  selector: 'app-site-layout',
  imports: [RouterOutlet, Header, Footer],
  template: `
    <app-header />
    <main>
      <router-outlet />
    </main>
    <app-footer />
  `,
})
export class SiteLayout {}
