import {
  Component,
  ElementRef,
  afterNextRender,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { fmt, localParts, mean } from '../core/series';
import { Help } from '../shared/help';
import { ACTIONS, ACTION_KEYS, Action, actionAt } from './actions';
import { SimulationResult } from './calculator.models';

interface Quarter {
  t: number;
  v: number;
  exp: number | null;
  action: Action | null;
}

interface Day {
  key: string;
  label: string;
  short: string;
  quarters: Quarter[];
}

const PAD = { top: 16, right: 16, bottom: 26, left: 44 };
// Theme-Farben (styles.scss): rot = teurer, grün = günstiger
const RED = 'var(--up)';
const GREEN = 'var(--down)';

const dayLabel = new Intl.DateTimeFormat('de-CH', {
  timeZone: 'Europe/Zurich',
  weekday: 'long',
  day: 'numeric',
  month: 'long',
});
const dayShort = new Intl.DateTimeFormat('de-CH', {
  timeZone: 'Europe/Zurich',
  weekday: 'short',
  day: 'numeric',
  month: 'numeric',
});
const hhmm = (t: number) => {
  const p = localParts(t);
  return `${String(p.hour).padStart(2, '0')}:${String(p.minute).padStart(2, '0')}`;
};

/** Börsen-Ansicht der 96 Viertelstunden-Tarife eines Tages mit Tages-Slider. */
@Component({
  selector: 'app-tariff-board',
  imports: [Help],
  templateUrl: './tariff-board.html',
  styleUrl: './tariff-board.scss',
})
export class TariffBoard {
  readonly tariffs = input.required<SimulationResult['tariffs']>();
  readonly plan = input<SimulationResult['plan'] | null>(null);

  protected readonly fmt = fmt;
  protected readonly hhmm = hhmm;
  protected readonly actions = ACTIONS;
  protected readonly actionKeys = ACTION_KEYS;
  protected readonly pad = PAD;
  protected readonly red = RED;
  protected readonly green = GREEN;

  protected readonly mode = signal<'line' | 'candles'>('line');
  protected readonly showExport = signal(false);
  protected readonly selected = signal<number | null>(null);
  protected readonly hover = signal<number | null>(null);
  protected readonly width = signal(800);
  protected readonly height = 320;

  private readonly host = inject(ElementRef<HTMLElement>);

  constructor() {
    afterNextRender(() => {
      const el = (this.host.nativeElement as HTMLElement).querySelector('.board__chart') as HTMLElement;
      const update = () => this.width.set(Math.max(300, Math.floor(el.clientWidth)));
      update();
      new ResizeObserver(update).observe(el);
    });
  }

  /** Vollständige Tage mit publizierten Tarifen */
  protected readonly days = computed<Day[]>(() => {
    const tr = this.tariffs();
    const plan = this.plan();
    const byDay = new Map<string, Quarter[]>();
    tr.import.forEach((v, i) => {
      if (v == null) return;
      const t = tr.start + i * tr.step;
      let action: Action | null = null;
      if (plan) {
        const j = Math.round((t - plan.start) / plan.step);
        if (j >= 0 && j < plan.charge.length) action = actionAt(plan, j);
      }
      const key = localParts(t).day;
      const list = byDay.get(key) ?? [];
      list.push({ t, v, exp: tr.export[i], action });
      byDay.set(key, list);
    });
    return [...byDay.entries()]
      .filter(([, q]) => q.length >= 92)
      .map(([key, quarters]) => ({
        key,
        label: dayLabel.format(quarters[0].t * 1000),
        short: dayShort.format(quarters[0].t * 1000),
        quarters,
      }));
  });

  /** Standard: heute (sonst der letzte verfügbare Tag) */
  protected readonly index = computed(() => {
    const days = this.days();
    const sel = this.selected();
    if (sel !== null && sel < days.length) return sel;
    const today = localParts(Date.now() / 1000).day;
    const i = days.findIndex((d) => d.key === today);
    return i >= 0 ? i : days.length - 1;
  });

  protected readonly day = computed(() => this.days()[this.index()] ?? null);

  protected readonly ticker = computed(() => {
    const d = this.day();
    if (!d) return null;
    const q = d.quarters;
    const values = q.map((x) => x.v);
    const avg = mean(values);
    const prev = this.days()[this.index() - 1];
    const prevAvg = prev ? mean(prev.quarters.map((x) => x.v)) : null;
    const now = Date.now() / 1000;
    const current = q.find((x) => x.t <= now && now < x.t + 900) ?? null;
    const high = q.reduce((a, b) => (b.v > a.v ? b : a));
    const low = q.reduce((a, b) => (b.v < a.v ? b : a));
    return {
      price: current ? current.v : q[q.length - 1].v,
      priceLabel: current ? `Jetzt · ${hhmm(current.t)}` : 'Schluss · 23:45',
      open: q[0].v,
      high,
      low,
      avg,
      spread: high.v - low.v,
      change: prevAvg !== null ? avg - prevAvg : null,
      changePct: prevAvg ? ((avg - prevAvg) / prevAvg) * 100 : null,
      hasPlan: q.some((x) => x.action !== null),
    };
  });

  // ---------- Geometrie ----------
  private readonly innerW = computed(() => this.width() - PAD.left - PAD.right);
  private readonly innerH = this.height - PAD.top - PAD.bottom;

  private readonly domain = computed(() => {
    const d = this.day();
    if (!d) return { lo: 0, hi: 1, ticks: [0, 1] };
    const values = d.quarters.flatMap((q) => (this.showExport() && q.exp !== null ? [q.v, q.exp] : [q.v]));
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad;
    hi += pad;
    const step = [0.5, 1, 2, 2.5, 5, 10].find((s) => (hi - lo) / s <= 6) ?? 10;
    const ticks: number[] = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(Number(v.toFixed(2)));
    return { lo, hi, ticks };
  });

  protected x(i: number): number {
    return PAD.left + (i / 96) * this.innerW();
  }

  protected y(v: number): number {
    const { lo, hi } = this.domain();
    return PAD.top + this.innerH - ((v - lo) / (hi - lo)) * this.innerH;
  }

  protected readonly yTicks = computed(() => this.domain().ticks.map((t) => ({ v: t, y: this.y(t) })));
  protected readonly xTicks = computed(() => {
    const every = this.width() < 560 ? 6 : 3; // schmale Bildschirme: weniger Beschriftungen
    const hours = Array.from({ length: 24 / every + 1 }, (_, i) => i * every);
    return hours.map((h) => ({ label: `${String(h).padStart(2, '0')}:00`, x: this.x(h * 4) }));
  });
  protected readonly quarterW = computed(() => this.innerW() / 96);

  /** Treppenlinie, eingefärbt nach günstig (unter Ø) und teuer (über Ø) */
  protected readonly segments = computed(() => {
    const d = this.day();
    const t = this.ticker();
    if (!d || !t) return [];
    return d.quarters.map((q, i) => ({
      x1: this.x(i),
      x2: this.x(i + 1),
      y: this.y(q.v),
      color: q.v > t.avg ? RED : GREEN,
      // senkrechter Übergang zum nächsten Wert
      next: i + 1 < d.quarters.length ? this.y(d.quarters[i + 1].v) : null,
    }));
  });

  protected readonly area = computed(() => {
    const d = this.day();
    if (!d) return '';
    const base = PAD.top + this.innerH;
    let path = `M${this.x(0)},${base}`;
    d.quarters.forEach((q, i) => (path += `L${this.x(i)},${this.y(q.v)}L${this.x(i + 1)},${this.y(q.v)}`));
    return `${path}L${this.x(d.quarters.length)},${base}Z`;
  });

  protected readonly exportLine = computed(() => {
    const d = this.day();
    if (!d || !this.showExport()) return '';
    let path = '';
    let pen = false;
    d.quarters.forEach((q, i) => {
      if (q.exp === null) {
        pen = false;
        return;
      }
      path += `${pen ? 'L' : 'M'}${this.x(i)},${this.y(q.exp)}L${this.x(i + 1)},${this.y(q.exp)}`;
      pen = true;
    });
    return path;
  });

  /** Stundenkerzen aus je 4 Viertelstunden */
  protected readonly candles = computed(() => {
    const d = this.day();
    if (!d) return [];
    const out = [];
    for (let h = 0; h * 4 < d.quarters.length; h++) {
      const q = d.quarters.slice(h * 4, h * 4 + 4).map((x) => x.v);
      const open = q[0];
      const close = q[q.length - 1];
      const cx = this.x(h * 4 + 2);
      out.push({
        cx,
        w: Math.max(3, this.quarterW() * 2.6),
        high: this.y(Math.max(...q)),
        low: this.y(Math.min(...q)),
        top: this.y(Math.max(open, close)),
        bottom: this.y(Math.min(open, close)),
        // Börsenkonvention: grün = Kurs steigt innerhalb der Stunde, rot = fällt
        color: close >= open ? GREEN : RED,
      });
    }
    return out;
  });

  protected readonly nowX = computed(() => {
    const d = this.day();
    if (!d) return null;
    const now = Date.now() / 1000;
    const i = (now - d.quarters[0].t) / 900;
    return i >= 0 && i <= d.quarters.length ? this.x(i) : null;
  });

  protected readonly hoverInfo = computed(() => {
    const i = this.hover();
    const d = this.day();
    const t = this.ticker();
    if (i === null || !d || !t || !d.quarters[i]) return null;
    const q = d.quarters[i];
    return {
      x: this.x(i + 0.5),
      y: this.y(q.v),
      time: `${hhmm(q.t)}–${hhmm(q.t + 900)}`,
      price: q.v,
      exp: q.exp,
      diff: ((q.v - t.avg) / t.avg) * 100,
      action: q.action,
    };
  });

  protected onMove(event: PointerEvent): void {
    const svg = event.currentTarget as SVGSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * this.width();
    const i = Math.floor(((px - PAD.left) / this.innerW()) * 96);
    const d = this.day();
    this.hover.set(d && i >= 0 && i < d.quarters.length ? i : null);
  }

  protected step(delta: number): void {
    const next = this.index() + delta;
    if (next >= 0 && next < this.days().length) this.selected.set(next);
  }

  protected pick(event: Event): void {
    this.selected.set(Number((event.target as HTMLInputElement).value));
  }
}
