import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  resource,
  signal,
  viewChild,
} from '@angular/core';
import { fmt, localParts } from '../../core/series';
import { DriverChart, DriverSeries } from './driver-chart';
import { ForecastChart } from './forecast-chart';

/**
 * Snapshot aus analysis/05_forecast.py: Ist und LightGBM-Prognose des CH-Day-Ahead-Preises (stündlich)
 * von jetzt − 7 Tage bis jetzt + 30 h, dazu die SHAP-Beiträge je Treiber für die Prognosestunden.
 */
interface Forecast {
  created: string;
  now: number;
  accuracy: { nmae: number; persistenceNmae: number; recentNmae: number };
  hourly: { t: number[]; actual: (number | null)[]; forecast: (number | null)[] };
  reasons: { t: number[]; groups: string[]; contrib: Record<string, (number | null)[]> };
}

type View = 'day' | 'week' | 'reasons';

const QUARTER = 900;
const HOUR = 3600;
const DAY = 86400;

/** Farbe folgt dem Treiber, nie seinem Rang (feste Zuordnung, validierte Palette) */
const DRIVER_COLOR: Record<string, string> = {
  Residuallast: 'var(--s1)',
  Preisniveau: 'var(--s2)',
  Nachbarländer: 'var(--s3)',
  Solar: 'var(--s4)',
  Wind: 'var(--s5)',
  'Wasser & Austausch': 'var(--s6)',
  Kalender: 'var(--s7)',
  Wetter: 'var(--s8)',
  Übrige: 'var(--chart-muted)',
};

const WEEKDAYS = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];
const hhmm = (t: number) => {
  const p = localParts(t);
  return `${String(p.hour).padStart(2, '0')}:${String(p.minute).padStart(2, '0')}`;
};
const dayLabel = (t: number) => {
  const p = localParts(t);
  const [, m, d] = p.day.split('-');
  return `${WEEKDAYS[p.weekday]} ${d}.${m}.`;
};

@Component({
  selector: 'app-analysis',
  imports: [ForecastChart, DriverChart],
  templateUrl: './analysis.html',
  styleUrl: './analysis.scss',
})
export class Analysis {
  protected readonly fmt = fmt;
  protected readonly hhmm = hhmm;
  protected readonly dayLabel = dayLabel;
  protected readonly dayTime = (t: number) => `${dayLabel(t)} ${hhmm(t)}`;
  protected readonly views: { key: View; label: string }[] = [
    { key: 'day', label: '24 h · 15 min' },
    { key: 'week', label: '7 Tage' },
    { key: 'reasons', label: 'Gründe' },
  ];
  /** Startansicht, direkt verlinkbar mit ?ansicht=7-tage bzw. ?ansicht=gruende */
  protected readonly view = signal<View>(
    ({ '7-tage': 'week', gruende: 'reasons' } as Record<string, View>)[
      new URLSearchParams(globalThis.location?.search ?? '').get('ansicht') ?? ''
    ] ?? 'day',
  );

  private readonly snapshot = resource({
    loader: async () => {
      const res = await fetch('forecast/forecast.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`forecast.json: HTTP ${res.status}`);
      return (await res.json()) as Forecast;
    },
  });
  protected readonly f = computed(() => (this.snapshot.hasValue() ? this.snapshot.value() : null));
  protected readonly error = computed(() => !!this.snapshot.error());

  // ---------- Grösse der Grafikfläche (füllt den restlichen Bildschirm) ----------
  private readonly stage = viewChild<ElementRef<HTMLElement>>('stage');
  protected readonly stageW = signal(800);
  protected readonly stageH = signal(400);

  // ---------- aktuelle Viertelstunde (läuft mit, solange die Seite offen ist) ----------
  private readonly clock = signal(Math.floor(Date.now() / 1000));

  constructor() {
    const destroy = inject(DestroyRef);
    const timer = setInterval(() => this.clock.set(Math.floor(Date.now() / 1000)), 30_000);
    destroy.onDestroy(() => clearInterval(timer));
    // Die Grafikfläche existiert erst, wenn die Daten geladen sind → beobachten, sobald sie da ist
    const observer = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      this.stageW.set(Math.max(280, Math.floor(box.width)));
      this.stageH.set(Math.max(220, Math.floor(box.height)));
    });
    effect(() => {
      const el = this.stage()?.nativeElement;
      if (el) observer.observe(el);
    });
    destroy.onDestroy(() => observer.disconnect());
  }

  /**
   * Beginn der Simulation: aktuelle Viertelstunde, begrenzt auf den Bereich, den der Snapshot abdeckt
   * (nach dem Lauf bleibt das Fenster so einige Stunden korrekt, danach bleibt es beim letzten Stand).
   */
  protected readonly cut = computed(() => {
    const f = this.f();
    if (!f) return 0;
    const last = f.hourly.t[f.hourly.t.length - 1] + HOUR;
    const now = Math.floor(this.clock() / QUARTER) * QUARTER;
    return Math.min(Math.max(now, f.now), last - DAY);
  });

  /** Stundenwerte → Viertelstunden (CH-Day-Ahead ist ein Stundenpreis) */
  private readonly quarters = computed(() => {
    const f = this.f();
    if (!f) return null;
    const t: number[] = [];
    const actual: (number | null)[] = [];
    const forecast: (number | null)[] = [];
    f.hourly.t.forEach((h, i) => {
      for (let q = 0; q < 4; q++) {
        t.push(h + q * QUARTER);
        actual.push(f.hourly.actual[i]);
        forecast.push(f.hourly.forecast[i]);
      }
    });
    return { t, actual, forecast };
  });

  /** Die simulierten 96 Viertelstunden ab jetzt */
  protected readonly next24 = computed(() => {
    const q = this.quarters();
    const cut = this.cut();
    if (!q) return [];
    return q.t
      .map((t, i) => ({ t, v: q.forecast[i] }))
      .filter((p): p is { t: number; v: number } => p.t >= cut && p.t < cut + DAY && p.v != null);
  });

  protected readonly kpis = computed(() => {
    const pts = this.next24();
    if (!pts.length) return null;
    const avg = pts.reduce((a, p) => a + p.v, 0) / pts.length;
    const low = pts.reduce((a, p) => (p.v < a.v ? p : a));
    const high = pts.reduce((a, p) => (p.v > a.v ? p : a));
    return { avg, low, high };
  });

  /** Liste rechts: Balkenlänge relativ zu Tief/Hoch, Tageswechsel als Trenner */
  protected readonly list = computed(() => {
    const pts = this.next24();
    const k = this.kpis();
    if (!k) return [];
    const span = k.high.v - k.low.v || 1;
    return pts.map((p, i) => ({
      ...p,
      pct: 8 + ((p.v - k.low.v) / span) * 92,
      low: p.t === k.low.t,
      high: p.t === k.high.t,
      newDay: i === 0 || localParts(p.t).day !== localParts(pts[i - 1].t).day,
    }));
  });

  /** 24 h zurück + 24 h voraus in Viertelstunden */
  protected readonly dayChart = computed(() => {
    const q = this.quarters();
    const cut = this.cut();
    if (!q) return null;
    const idx = q.t.map((t, i) => i).filter((i) => q.t[i] >= cut - DAY && q.t[i] < cut + DAY);
    return {
      x: idx.map((i) => q.t[i]),
      actual: idx.map((i) => (q.t[i] < cut ? q.actual[i] : null)),
      forecast: idx.map((i) => (q.t[i] >= cut ? q.forecast[i] : null)),
    };
  });

  /** 7 Tage zurück (Ist + damalige Prognose) + 24 h voraus, stündlich */
  protected readonly weekChart = computed(() => {
    const f = this.f();
    const cut = this.cut();
    if (!f) return null;
    const cutHour = Math.floor(cut / HOUR) * HOUR;
    const idx = f.hourly.t.map((t, i) => i).filter((i) => f.hourly.t[i] < cut + DAY);
    return {
      x: idx.map((i) => f.hourly.t[i]),
      actual: idx.map((i) => (f.hourly.t[i] < cutHour ? f.hourly.actual[i] : null)),
      forecast: idx.map((i) => (f.hourly.t[i] >= cutHour ? f.hourly.forecast[i] : null)),
      compare: idx.map((i) => (f.hourly.t[i] < cutHour ? f.hourly.forecast[i] : null)),
      cut: cutHour,
    };
  });

  /** Beitrag je Treiber und Prognosestunde (SHAP), nur die 24 h ab jetzt */
  protected readonly reasons = computed(() => {
    const f = this.f();
    const cut = Math.floor(this.cut() / HOUR) * HOUR;
    if (!f) return null;
    const idx = f.reasons.t.map((t, i) => i).filter((i) => f.reasons.t[i] >= cut && f.reasons.t[i] < cut + DAY);
    const series: DriverSeries[] = f.reasons.groups.map((g) => ({
      name: g,
      color: DRIVER_COLOR[g] ?? 'var(--chart-muted)',
      values: idx.map((i) => f.reasons.contrib[g]?.[i] ?? null),
    }));
    return { x: idx.map((i) => f.reasons.t[i]), series };
  });

  protected readonly stand = computed(() => {
    const f = this.f();
    return f ? `${f.created.slice(8, 10)}.${f.created.slice(5, 7)}. ${f.created.slice(11, 16)}` : '';
  });
}
