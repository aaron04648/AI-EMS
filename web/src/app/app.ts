import { Component, afterNextRender, inject } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
})
export class App {
  private readonly document = inject(DOCUMENT);

  constructor() {
    // Sanftes Scrollen erst nach dem Seitenaufbau aktivieren (Deep-Links auf Anker)
    afterNextRender(() => {
      setTimeout(() => this.document.documentElement.classList.add('is-ready'), 500);
    });
  }
}
