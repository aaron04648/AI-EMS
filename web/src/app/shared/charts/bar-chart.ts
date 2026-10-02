import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { fmt } from '../../core/series';

export interface BarRow {
  label: string;
  value: number;
  /** optionale Zusatzinfo im Tooltip */
  note?: string;
  highlight?: boolean;
}

/**
 * Balkendiagramm (horizontal) bzw. Säulendiagramm (vertikal).
 * Negative Werte wachsen von der Nulllinie nach links bzw. unten.
 * Hervorgehobene Balken erhalten die Akzentfarbe, die übrigen ein ruhiges Grau-Blau.
 */
@Component({
  selector: 'app-bar-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (orientation() === 'horizontal') {
      <ul class="hbars" role="list" [attr.aria-label]="ariaLabel()">
        @for (r of rows(); track r.label; let i = $index) {
          <li
            class="hbar"
            [class.is-hover]="hover() === i"
            (pointerenter)="hover.set(i)"
            (pointerleave)="hover.set(null)"
          >
            <span class="hbar__label">{{ r.label }}</span>
            <span class="hbar__track">
              <span
                class="hbar__bar"
                [class.hbar__bar--neg]="r.value < 0"
                [style.left.%]="r.value < 0 ? zeroPct() - pct(r.value) : zeroPct()"
                [style.width.%]="pct(r.value)"
                [style.background]="r.highlight ? highlightColor() : color()"
              ></span>
            </span>
            <span class="hbar__value">{{ format(r.value) }}</span>
            @if (hover() === i && r.note) {
              <span class="tooltip tooltip--inline">{{ r.note }}</span>
            }
          </li>
        }
      </ul>
    } @else {
      <div class="vbars" [style.height.px]="height()" [attr.aria-label]="ariaLabel()" role="list">
        @for (r of rows(); track r.label; let i = $index) {
          <div
            class="vbar"
            role="listitem"
            [attr.aria-label]="r.label + ': ' + format(r.value)"
            (pointerenter)="hover.set(i)"
            (pointerleave)="hover.set(null)"
          >
            <span class="vbar__track">
              <span
                class="vbar__bar"
                [class.vbar__bar--neg]="r.value < 0"
                [style.bottom.%]="r.value < 0 ? zeroPct() - pct(r.value) : zeroPct()"
                [style.height.%]="pct(r.value)"
                [style.background]="r.highlight ? highlightColor() : color()"
              ></span>
            </span>
            <span class="vbar__label">{{ r.label }}</span>
            @if (hover() === i) {
              <span class="tooltip tooltip--bar">
                <strong>{{ r.note ?? r.label }}</strong>
                <span>{{ format(r.value) }}</span>
              </span>
            }
          </div>
        }
      </div>
    }
  `,
  styleUrl: './chart.scss',
  host: { class: 'chart' },
})
export class BarChart {
  readonly rows = input.required<BarRow[]>();
  readonly orientation = input<'horizontal' | 'vertical'>('horizontal');
  readonly unit = input('');
  readonly digits = input(0);
  readonly height = input(240);
  readonly color = input('#86b6ef');
  readonly highlightColor = input('#2a78d6');
  readonly ariaLabel = input('Balkendiagramm');

  protected readonly hover = signal<number | null>(null);

  private readonly range = computed(() => {
    const values = this.rows().map((r) => r.value);
    const lo = Math.min(0, ...values);
    const hi = Math.max(0, ...values);
    return { lo, span: hi - lo || 1 };
  });

  /** Position der Nulllinie in Prozent der Spur */
  protected readonly zeroPct = computed(() => (-this.range().lo / this.range().span) * 100);

  protected pct(value: number): number {
    return (Math.abs(value) / this.range().span) * 100;
  }

  protected format(value: number): string {
    return `${fmt(value, this.digits())} ${this.unit()}`.trim();
  }
}
