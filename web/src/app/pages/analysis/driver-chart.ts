import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { fmt } from '../../core/series';

export interface DriverSeries {
  name: string;
  color: string;
  values: (number | null)[];
}

const PAD = { top: 12, right: 12, bottom: 28, left: 48 };

/**
 * Divergierende, gestapelte Säulen: Beitrag je Treiber und Stunde (positiv nach oben, negativ nach unten).
 * Gleiche Achsen- und Tooltip-Gestaltung wie die übrigen Diagramme.
 */
@Component({
  selector: 'app-driver-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="legend">
      @for (s of series(); track s.name) {
        <li><span class="legend__swatch" [style.background]="s.color"></span>{{ s.name }}</li>
      }
    </ul>

    <div class="plot" [style.height.px]="height()">
      <svg
        [attr.viewBox]="'0 0 ' + width() + ' ' + height()"
        [attr.width]="width()"
        [attr.height]="height()"
        role="img"
        [attr.aria-label]="ariaLabel()"
        (pointerleave)="hover.set(null)"
      >
        @for (t of yTicks(); track t.v) {
          <line class="grid" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="t.pos" [attr.y2]="t.pos" />
          <text class="tick tick--y" [attr.x]="pad.left - 8" [attr.y]="t.pos">{{ t.label }}</text>
        }

        @for (col of columns(); track col.i) {
          <g (pointerenter)="hover.set(col.i)">
            <rect class="hit" [attr.x]="col.x - slot() / 2" [attr.y]="pad.top" [attr.width]="slot()" [attr.height]="innerH()" />
            @for (seg of col.segs; track seg.name) {
              <rect
                class="seg"
                [attr.x]="col.x - barW() / 2"
                [attr.y]="seg.y"
                [attr.width]="barW()"
                [attr.height]="seg.h"
                [attr.rx]="1.5"
                [style.fill]="seg.color"
                [class.is-dim]="hover() !== null && hover() !== col.i"
              />
            }
          </g>
        }
        <line class="baseline" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="zeroY()" [attr.y2]="zeroY()" />

        @for (t of xTicks(); track t.i) {
          <text class="tick tick--x" [attr.x]="t.pos" [attr.y]="height() - 8">{{ t.label }}</text>
        }
      </svg>

      @if (hoverInfo(); as h) {
        <div class="tooltip" [style.left.px]="h.x + 10" [class.tooltip--left]="h.x > width() * 0.6">
          <strong>{{ h.title }}</strong>
          @for (r of h.rows; track r.name) {
            <span class="tooltip__row">
              <span class="legend__swatch" [style.background]="r.color"></span>
              <span class="tooltip__name">{{ r.name }}</span>
              <span class="tooltip__value">{{ r.value }}</span>
            </span>
          }
          <span class="tooltip__row tooltip__sum">
            <span class="tooltip__name">Σ</span>
            <span class="tooltip__value">{{ h.sum }}</span>
          </span>
        </div>
      }
    </div>
  `,
  styleUrls: ['../../shared/charts/chart.scss', './driver-chart.scss'],
  host: { class: 'chart' },
})
export class DriverChart {
  readonly series = input.required<DriverSeries[]>();
  readonly x = input.required<number[]>();
  readonly xFormat = input<(v: number) => string>((v) => String(v));
  readonly unit = input('');
  readonly height = input(300);
  readonly ariaLabel = input('Beiträge je Treiber');

  protected readonly pad = PAD;
  protected readonly width = signal(640);
  protected readonly hover = signal<number | null>(null);
  private readonly host = inject(ElementRef<HTMLElement>);

  constructor() {
    afterNextRender(() => {
      const el = this.host.nativeElement as HTMLElement;
      const update = () => this.width.set(Math.max(280, Math.floor(el.clientWidth)));
      update();
      new ResizeObserver(update).observe(el);
    });
  }

  protected readonly innerW = computed(() => this.width() - PAD.left - PAD.right);
  protected readonly innerH = computed(() => this.height() - PAD.top - PAD.bottom);
  protected readonly slot = computed(() => this.innerW() / Math.max(1, this.x().length));
  protected readonly barW = computed(() => Math.max(3, Math.min(28, this.slot() - 4)));

  /** Summe der positiven bzw. negativen Beiträge je Stunde */
  private readonly stacks = computed(() =>
    this.x().map((_, i) => {
      let pos = 0;
      let neg = 0;
      for (const s of this.series()) {
        const v = s.values[i] ?? 0;
        if (v > 0) pos += v;
        else neg += v;
      }
      return { pos, neg };
    }),
  );

  private readonly domain = computed(() => {
    const st = this.stacks();
    const hi = Math.max(1, ...st.map((s) => s.pos));
    const lo = Math.min(-1, ...st.map((s) => s.neg));
    const raw = (hi - lo) / 5;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
    const ticks: number[] = [];
    for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)));
    return { lo: ticks[0], hi: ticks[ticks.length - 1], ticks };
  });

  private readonly sy = computed(() => {
    const { lo, hi } = this.domain();
    const h = this.innerH();
    return (v: number) => PAD.top + h - ((v - lo) / (hi - lo || 1)) * h;
  });

  protected readonly zeroY = computed(() => this.sy()(0));
  protected readonly yTicks = computed(() =>
    this.domain().ticks.map((v) => ({ v, pos: this.sy()(v), label: fmt(v, 0) })),
  );

  private readonly cx = (i: number) => PAD.left + this.slot() * (i + 0.5);

  protected readonly columns = computed(() => {
    const sy = this.sy();
    return this.x().map((_, i) => {
      let up = 0;
      let down = 0;
      const segs: { name: string; color: string; y: number; h: number }[] = [];
      for (const s of this.series()) {
        const v = s.values[i] ?? 0;
        if (!v) continue;
        const from = v > 0 ? up : down;
        const to = from + v;
        if (v > 0) up = to;
        else down = to;
        // 2px Abstand zwischen den Segmenten
        const y = sy(Math.max(from, to));
        const h = Math.max(0, Math.abs(sy(from) - sy(to)) - 2);
        segs.push({ name: s.name, color: s.color, y: v > 0 ? y + 2 : y, h });
      }
      return { i, x: this.cx(i), segs };
    });
  });

  protected readonly xTicks = computed(() => {
    const xs = this.x();
    const every = Math.max(1, Math.ceil(xs.length / Math.max(2, Math.floor(this.innerW() / 56))));
    return xs
      .map((v, i) => ({ i, pos: this.cx(i), label: this.xFormat()(v) }))
      .filter((t) => t.i % every === 0);
  });

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    if (i == null) return null;
    const rows = this.series()
      .map((s) => ({ name: s.name, color: s.color, raw: s.values[i] ?? 0 }))
      .filter((r) => Math.abs(r.raw) >= 0.05)
      .sort((a, b) => Math.abs(b.raw) - Math.abs(a.raw))
      .map((r) => ({ ...r, value: `${r.raw > 0 ? '+' : ''}${fmt(r.raw, 1)} ${this.unit()}`.trim() }));
    const sum = this.series().reduce((acc, s) => acc + (s.values[i] ?? 0), 0);
    return {
      x: this.cx(i),
      title: this.xFormat()(this.x()[i]),
      rows,
      sum: `${sum > 0 ? '+' : ''}${fmt(sum, 1)} ${this.unit()}`.trim(),
    };
  });
}
