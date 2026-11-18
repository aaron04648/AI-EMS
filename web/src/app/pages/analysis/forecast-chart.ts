import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { fmt } from '../../core/series';

const PAD = { top: 28, right: 16, bottom: 28, left: 48 };

/**
 * Main chart of the dashboard: actual price up to "now", then the simulated 24 h.
 * Each value applies until the next point in time (step line, market prices are block prices).
 * Optionally a comparison series (past forecast) as a thin line.
 */
@Component({
  selector: 'app-forecast-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="legend">
      <li><span class="legend__swatch" style="background: var(--ink)"></span>Actual</li>
      <li><span class="legend__swatch" style="background: var(--s1)"></span>Forecast</li>
      @if (compare()) {
        <li><span class="legend__swatch legend__swatch--thin" style="background: var(--s1)"></span>Past forecast</li>
      }
    </ul>
    <div class="plot" [style.height.px]="plotH()">
      <svg
        [attr.viewBox]="'0 0 ' + width() + ' ' + plotH()"
        [attr.width]="width()"
        [attr.height]="plotH()"
        role="img"
        [attr.aria-label]="ariaLabel()"
        (pointermove)="onMove($event)"
        (pointerleave)="hover.set(null)"
      >
        <rect class="future" [attr.x]="cutX()" [attr.y]="pad.top" [attr.width]="endX() - cutX()" [attr.height]="innerH()" />

        @for (t of yTicks(); track t.v) {
          <line class="grid" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="t.pos" [attr.y2]="t.pos" />
          <text class="tick tick--y" [attr.x]="pad.left - 8" [attr.y]="t.pos">{{ t.label }}</text>
        }
        @if (zeroY() !== null) {
          <line class="baseline" [attr.x1]="pad.left" [attr.x2]="width() - pad.right" [attr.y1]="zeroY()" [attr.y2]="zeroY()" />
        }
        @for (t of xTicks(); track t.pos) {
          <text class="tick tick--x" [attr.x]="t.pos" [attr.y]="plotH() - 8">{{ t.label }}</text>
        }

        @if (paths().compare) {
          <path class="line line--compare" [attr.d]="paths().compare" />
        }
        <path class="line line--actual" [attr.d]="paths().actual" />
        <path class="line line--forecast" [attr.d]="paths().forecast" />

        <line class="cut" [attr.x1]="cutX()" [attr.x2]="cutX()" [attr.y1]="pad.top - 18" [attr.y2]="pad.top + innerH()" />
        <text class="cut-label" [attr.x]="cutX() + 6" [attr.y]="pad.top - 8">Now {{ xFormat()(cut()) }}</text>
        @if (endX() - cutX() > 150) {
          <text class="cut-label cut-label--end" [attr.x]="endX() - 4" [attr.y]="pad.top - 8">+24 h</text>
        }

        @if (hoverInfo(); as h) {
          <line class="crosshair" [attr.x1]="h.x" [attr.x2]="h.x" [attr.y1]="pad.top" [attr.y2]="pad.top + innerH()" />
          @for (d of h.dots; track d.cls) {
            <circle [attr.class]="'dot ' + d.cls" [attr.cx]="h.x" [attr.cy]="d.y" r="4" />
          }
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
        </div>
      }
    </div>
  `,
  styleUrls: ['../../shared/charts/chart.scss', './forecast-chart.scss'],
  host: { class: 'chart' },
})
export class ForecastChart {
  /** Points in time (unix seconds, ascending, uniform step) */
  readonly x = input.required<number[]>();
  readonly actual = input.required<(number | null)[]>();
  readonly forecast = input.required<(number | null)[]>();
  readonly compare = input<(number | null)[] | null>(null);
  /** Start of the simulation */
  readonly cut = input.required<number>();
  readonly width = input(640);
  readonly height = input(320);
  readonly xFormat = input<(t: number) => string>((t) => String(t));
  readonly tooltipTitle = input<(t: number) => string>((t) => String(t));
  readonly xTickCount = input(8);
  readonly unit = input('€/MWh');
  readonly ariaLabel = input('Actual price and forecast');

  protected readonly pad = PAD;
  protected readonly hover = signal<number | null>(null);

  /** Height minus legend */
  protected readonly plotH = computed(() => Math.max(160, this.height() - 32));
  protected readonly innerW = computed(() => this.width() - PAD.left - PAD.right);
  protected readonly innerH = computed(() => this.plotH() - PAD.top - PAD.bottom);

  private readonly step = computed(() => {
    const xs = this.x();
    return xs.length > 1 ? xs[1] - xs[0] : 3600;
  });

  private readonly sx = computed(() => {
    const xs = this.x();
    const x0 = xs[0] ?? 0;
    const span = (xs[xs.length - 1] ?? 1) + this.step() - x0 || 1;
    const w = this.innerW();
    return (t: number) => PAD.left + ((t - x0) / span) * w;
  });

  private readonly domain = computed(() => {
    const all = [...this.actual(), ...this.forecast(), ...(this.compare() ?? [])].filter(
      (v): v is number => v != null,
    );
    // Axis on the data range (zero only when prices become negative)
    let lo = Math.min(...all);
    let hi = Math.max(...all);
    if (!all.length) [lo, hi] = [0, 1];
    if (hi - lo < 1) hi = lo + 1;
    const raw = (hi - lo) / 5;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
    const ticks: number[] = [];
    for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)));
    lo = ticks[0];
    hi = ticks[ticks.length - 1];
    return { lo, hi, ticks };
  });

  private readonly sy = computed(() => {
    const { lo, hi } = this.domain();
    const h = this.innerH();
    return (v: number) => PAD.top + h - ((v - lo) / (hi - lo || 1)) * h;
  });

  protected readonly yTicks = computed(() =>
    this.domain().ticks.map((v) => ({ v, pos: this.sy()(v), label: fmt(v, 0) })),
  );

  protected readonly zeroY = computed(() => {
    const { lo, hi } = this.domain();
    return lo < 0 && hi > 0 ? this.sy()(0) : null;
  });

  protected readonly cutX = computed(() => this.sx()(this.cut()));
  protected readonly endX = computed(() => this.width() - PAD.right);

  protected readonly xTicks = computed(() => {
    const xs = this.x();
    if (!xs.length) return [];
    const count = Math.min(this.xTickCount(), Math.max(2, Math.floor(this.innerW() / 90)));
    const sx = this.sx();
    return Array.from({ length: count }, (_, i) => {
      const t = xs[Math.round((i / (count - 1)) * (xs.length - 1))];
      return { pos: sx(t), label: this.xFormat()(t) };
    });
  });

  /** Step line: each value applies from t to t + step */
  private stepPath(values: (number | null)[]): string {
    const xs = this.x();
    const sx = this.sx();
    const sy = this.sy();
    const step = this.step();
    let d = '';
    let pen = false;
    values.forEach((v, i) => {
      if (v == null) {
        pen = false;
        return;
      }
      const y = sy(v).toFixed(1);
      d += `${pen ? 'L' : 'M'}${sx(xs[i]).toFixed(1)},${y}L${sx(xs[i] + step).toFixed(1)},${y}`;
      pen = true;
    });
    return d;
  }

  protected readonly paths = computed(() => ({
    actual: this.stepPath(this.actual()),
    forecast: this.stepPath(this.forecast()),
    compare: this.compare() ? this.stepPath(this.compare()!) : null,
  }));

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    if (i == null) return null;
    const t = this.x()[i];
    const x = this.sx()(t + this.step() / 2);
    const rows: { name: string; color: string; value: string }[] = [];
    const dots: { cls: string; y: number }[] = [];
    const add = (name: string, color: string, cls: string, v: number | null | undefined) => {
      if (v == null) return;
      rows.push({ name, color, value: `${fmt(v, 1)} ${this.unit()}` });
      dots.push({ cls, y: this.sy()(v) });
    };
    add('Actual', 'var(--ink)', 'dot--actual', this.actual()[i]);
    add('Forecast', 'var(--s1)', 'dot--forecast', this.forecast()[i]);
    add('Past forecast', 'var(--s1)', 'dot--compare', this.compare()?.[i]);
    return { x, title: this.tooltipTitle()(t), rows, dots };
  });

  protected onMove(event: PointerEvent): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * this.width();
    const xs = this.x();
    if (!xs.length) return;
    const frac = (px - PAD.left) / this.innerW();
    const i = Math.floor(frac * xs.length);
    this.hover.set(i >= 0 && i < xs.length ? i : null);
  }
}
