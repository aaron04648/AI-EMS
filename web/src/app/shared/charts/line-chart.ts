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

export interface ChartSeries {
  name: string;
  color: string;
  values: (number | null)[];
}

export interface Band {
  from: number;
  to: number;
  label?: string;
}

interface Tick {
  pos: number;
  label: string;
}

const PAD = { top: 12, right: 12, bottom: 28, left: 48 };

/** Rundet einen Wertebereich auf „schöne“ Achsenwerte. */
function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (lo === hi) {
    hi = lo + 1;
  }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const start = Math.floor(lo / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= hi + step * 0.5; v += step) {
    ticks.push(Number(v.toFixed(10)));
  }
  return ticks;
}

/**
 * Linien- bzw. gestapeltes Flächendiagramm als SVG.
 * Eine gemeinsame y-Achse (nie zwei Skalen), Crosshair-Tooltip beim Hover,
 * Legende ab zwei Reihen.
 */
@Component({
  selector: 'app-line-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (series().length > 1) {
      <ul class="legend">
        @for (s of series(); track s.name) {
          <li><span class="legend__swatch" [style.background]="s.color"></span>{{ s.name }}</li>
        }
      </ul>
    }

    <div class="plot" [style.height.px]="height()">
      <svg
        [attr.viewBox]="'0 0 ' + width() + ' ' + height()"
        [attr.width]="width()"
        [attr.height]="height()"
        role="img"
        [attr.aria-label]="ariaLabel()"
        (pointermove)="onMove($event)"
        (pointerleave)="hover.set(null)"
      >
        <!-- Hervorgehobene Zeitfenster -->
        @for (b of bandRects(); track b.x) {
          <rect class="band" [attr.x]="b.x" [attr.y]="pad.top" [attr.width]="b.w" [attr.height]="innerH()" [style.fill]="bandColor()" />
        }

        <!-- Raster & y-Achse -->
        @for (t of yTicks(); track t.label) {
          <line class="grid" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="t.pos" [attr.y2]="t.pos" />
          <text class="tick tick--y" [attr.x]="pad.left - 8" [attr.y]="t.pos">{{ t.label }}</text>
        }
        @if (zeroY() !== null) {
          <line class="baseline" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="zeroY()" [attr.y2]="zeroY()" />
        }

        <!-- x-Achse -->
        @for (t of xTickPos(); track $index) {
          <text class="tick tick--x" [attr.x]="t.pos" [attr.y]="height() - 8">{{ t.label }}</text>
        }

        <!-- Daten -->
        @for (p of paths(); track p.name) {
          @if (p.area) {
            <path class="area" [attr.d]="p.area" [style.fill]="p.color" [class.area--stacked]="stacked()" />
          }
          <path class="line" [attr.d]="p.line" [style.stroke]="p.color" />
        }

        <!-- Hover -->
        @if (hoverInfo(); as h) {
          <line class="crosshair" [attr.x1]="h.x" [attr.x2]="h.x" [attr.y1]="pad.top" [attr.y2]="pad.top + innerH()" />
          @for (d of h.dots; track d.name) {
            <circle class="dot" [attr.cx]="h.x" [attr.cy]="d.y" r="4" [style.fill]="d.color" />
          }
        }
      </svg>

      @if (hoverInfo(); as h) {
        <div class="tooltip" [style.left.px]="h.x" [class.tooltip--left]="h.x > width() * 0.6">
          <strong>{{ h.title }}</strong>
          @for (d of h.rows; track d.name) {
            <span class="tooltip__row">
              <span class="legend__swatch" [style.background]="d.color"></span>
              <span class="tooltip__name">{{ d.name }}</span>
              <span class="tooltip__value">{{ d.value }}</span>
            </span>
          }
        </div>
      }
    </div>
  `,
  styleUrl: './chart.scss',
  host: { class: 'chart' },
})
export class LineChart {
  readonly series = input.required<ChartSeries[]>();
  /** x-Werte (z. B. unix-Sekunden oder Stunde 0–23) – gleiche Länge wie die Reihen */
  readonly x = input.required<number[]>();
  readonly xFormat = input<(v: number) => string>((v) => String(v));
  readonly tooltipTitle = input<((v: number) => string) | null>(null);
  readonly xTickCount = input(6);
  readonly unit = input('');
  readonly digits = input(0);
  readonly stacked = input(false);
  readonly area = input(false);
  readonly height = input(280);
  readonly bands = input<Band[]>([]);
  readonly bandColor = input<string | null>(null);
  readonly includeZero = input(false);
  readonly ariaLabel = input('Diagramm');

  protected readonly pad = PAD;
  protected readonly width = signal(640);
  protected readonly hover = signal<number | null>(null);

  private readonly host = inject(ElementRef<HTMLElement>);

  constructor() {
    afterNextRender(() => {
      const el = this.host.nativeElement as HTMLElement;
      const update = () => this.width.set(Math.max(280, Math.floor(el.clientWidth)));
      update();
      const observer = new ResizeObserver(update);
      observer.observe(el);
    });
  }

  protected readonly innerW = computed(() => this.width() - PAD.left - PAD.right);
  protected readonly innerH = computed(() => this.height() - PAD.top - PAD.bottom);

  /** Für gestapelte Flächen: kumulierte Ober- und Unterkanten je Reihe. */
  private readonly layers = computed(() => {
    const series = this.series();
    const n = this.x().length;
    if (!this.stacked()) {
      return series.map((s) => ({ s, lower: null as number[] | null, upper: s.values }));
    }
    const acc = new Array(n).fill(0);
    return series.map((s) => {
      const lower = [...acc];
      const upper = s.values.map((v, i) => (acc[i] += Math.max(0, v ?? 0)));
      return { s, lower, upper: [...upper] as (number | null)[] };
    });
  });

  private readonly domain = computed(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const layer of this.layers()) {
      for (const v of layer.upper) {
        if (v == null) continue;
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
    if (this.stacked() || this.includeZero() || this.area()) {
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 0);
    }
    if (!Number.isFinite(lo)) {
      return { lo: 0, hi: 1, ticks: [0, 1] };
    }
    const ticks = niceTicks(lo, hi);
    return { lo: ticks[0], hi: ticks[ticks.length - 1], ticks };
  });

  private readonly xScale = computed(() => {
    const xs = this.x();
    const x0 = xs[0] ?? 0;
    const x1 = xs[xs.length - 1] ?? 1;
    const span = x1 - x0 || 1;
    const w = this.innerW();
    return (v: number) => PAD.left + ((v - x0) / span) * w;
  });

  private readonly yScale = computed(() => {
    const { lo, hi } = this.domain();
    const h = this.innerH();
    return (v: number) => PAD.top + h - ((v - lo) / (hi - lo || 1)) * h;
  });

  protected readonly yTicks = computed<Tick[]>(() => {
    const y = this.yScale();
    const digits = this.domain().ticks.some((t) => !Number.isInteger(t)) ? Math.max(1, this.digits()) : 0;
    return this.domain().ticks.map((t) => ({ pos: y(t), label: fmt(t, Math.min(digits, 2)) }));
  });

  protected readonly zeroY = computed(() => {
    const { lo, hi } = this.domain();
    return lo < 0 && hi > 0 ? this.yScale()(0) : null;
  });

  protected readonly xTickPos = computed<Tick[]>(() => {
    const xs = this.x();
    if (!xs.length) return [];
    const count = Math.min(this.xTickCount(), Math.max(2, Math.floor(this.innerW() / 90)));
    const sx = this.xScale();
    const ticks: Tick[] = [];
    for (let i = 0; i < count; i++) {
      const idx = Math.round((i / (count - 1)) * (xs.length - 1));
      ticks.push({ pos: sx(xs[idx]), label: this.xFormat()(xs[idx]) });
    }
    return ticks;
  });

  protected readonly bandRects = computed(() => {
    const sx = this.xScale();
    return this.bands().map((b) => ({ x: sx(b.from), w: Math.max(2, sx(b.to) - sx(b.from)) }));
  });

  protected readonly paths = computed(() => {
    const xs = this.x();
    const sx = this.xScale();
    const sy = this.yScale();
    const zero = sy(Math.max(this.domain().lo, 0));

    return this.layers().map(({ s, lower, upper }) => {
      let line = '';
      let pen = false;
      upper.forEach((v, i) => {
        if (v == null) {
          pen = false;
          return;
        }
        line += `${pen ? 'L' : 'M'}${sx(xs[i]).toFixed(1)},${sy(v).toFixed(1)}`;
        pen = true;
      });

      let area: string | null = null;
      if (lower) {
        const top = upper.map((v, i) => `${sx(xs[i]).toFixed(1)},${sy(v ?? 0).toFixed(1)}`);
        const bottom = lower.map((v, i) => `${sx(xs[i]).toFixed(1)},${sy(v).toFixed(1)}`).reverse();
        area = `M${top.join('L')}L${bottom.join('L')}Z`;
      } else if (this.area() && line) {
        const first = upper.findIndex((v) => v != null);
        let last = upper.length - 1;
        while (last > 0 && upper[last] == null) last--;
        area = `${line}L${sx(xs[last]).toFixed(1)},${zero}L${sx(xs[first]).toFixed(1)},${zero}Z`;
      }
      return { name: s.name, color: s.color, line, area };
    });
  });

  protected readonly hoverInfo = computed(() => {
    const idx = this.hover();
    if (idx == null) return null;
    const xs = this.x();
    const sx = this.xScale();
    const sy = this.yScale();
    const title = (this.tooltipTitle() ?? this.xFormat())(xs[idx]);
    const layers = this.layers();
    const dots = layers
      .filter((l) => l.upper[idx] != null)
      .map((l) => ({ name: l.s.name, color: l.s.color, y: sy(l.upper[idx] as number) }));
    const rows = layers
      .map((l) => ({ name: l.s.name, color: l.s.color, raw: l.s.values[idx] }))
      .filter((r) => r.raw != null)
      .map((r) => ({ ...r, value: `${fmt(r.raw, this.digits())} ${this.unit()}`.trim() }));
    // gestapelt: oberste Fläche zuerst – wie im Diagramm
    return { x: sx(xs[idx]), title, dots, rows: this.stacked() ? rows.reverse() : rows };
  });

  protected onMove(event: PointerEvent): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * this.width();
    const xs = this.x();
    if (!xs.length) return;
    const x0 = xs[0];
    const span = xs[xs.length - 1] - x0 || 1;
    const value = x0 + ((px - PAD.left) / this.innerW()) * span;
    // nächstgelegener x-Wert (x ist aufsteigend sortiert)
    let lo = 0;
    let hi = xs.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] < value) lo = mid;
      else hi = mid;
    }
    this.hover.set(Math.abs(xs[lo] - value) <= Math.abs(xs[hi] - value) ? lo : hi);
  }
}
