import { Component, computed, inject } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';
import { DataService } from '../../core/data.service';
import { formatFull } from '../../core/series';
import { SITE } from '../../core/site.config';
import { Logo } from '../logo';

@Component({
  selector: 'app-footer',
  imports: [RouterLink, NgTemplateOutlet, Logo],
  templateUrl: './footer.html',
  styleUrl: './footer.scss',
})
export class Footer {
  private readonly data = inject(DataService);

  protected readonly site = SITE;
  protected readonly year = new Date().getFullYear();

  protected readonly updated = computed(() => {
    const meta = this.data.meta.hasValue() ? this.data.meta.value() : null;
    return meta ? formatFull(Date.parse(meta.generatedAt) / 1000) : null;
  });

  protected initials(name: string): string {
    return name
      .split(' ')
      .map((part) => part[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
}
