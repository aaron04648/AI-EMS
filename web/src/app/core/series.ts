import { Grid } from './data.models';

/** Ein Messpunkt: unix-Sekunden + Wert. */
export interface Point {
  t: number;
  v: number;
}

const TZ = 'Europe/Zurich';

const partsFormatter = new Intl.DateTimeFormat('de-CH', {
  timeZone: TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  weekday: 'short',
  hourCycle: 'h23',
});

export interface LocalParts {
  /** YYYY-MM-DD in Schweizer Zeit */
  day: string;
  /** YYYY-MM */
  month: string;
  hour: number;
  minute: number;
  /** 0 = Sonntag … 6 = Samstag */
  weekday: number;
}

const WEEKDAYS: Record<string, number> = { So: 0, Mo: 1, Di: 2, Mi: 3, Do: 4, Fr: 5, Sa: 6 };
const partsCache = new Map<number, LocalParts>();

/** Zerlegt einen Zeitpunkt in Schweizer Lokalzeit (inkl. Sommerzeit). */
export function localParts(t: number): LocalParts {
  let cached = partsCache.get(t);
  if (!cached) {
    const p: Record<string, string> = {};
    for (const part of partsFormatter.formatToParts(new Date(t * 1000))) {
      p[part.type] = part.value;
    }
    cached = {
      day: `${p['year']}-${p['month']}-${p['day']}`,
      month: `${p['year']}-${p['month']}`,
      hour: Number(p['hour']),
      minute: Number(p['minute']),
      weekday: WEEKDAYS[p['weekday'].replace('.', '').slice(0, 2)] ?? 0,
    };
    partsCache.set(t, cached);
  }
  return cached;
}

/** Wandelt ein Raster in Punkte um und lässt Lücken weg. */
export function points(grid: Grid, from = -Infinity, to = Infinity): Point[] {
  const out: Point[] = [];
  grid.values.forEach((v, i) => {
    const t = grid.start + i * grid.step;
    if (v != null && t >= from && t < to) {
      out.push({ t, v });
    }
  });
  return out;
}

/** Zeitpunkte eines Rasters. */
export function times(grid: Grid): number[] {
  return grid.values.map((_, i) => grid.start + i * grid.step);
}

/** Wert eines Rasters zum Zeitpunkt t (oder null). */
export function valueAt(grid: Grid, t: number): number | null {
  const idx = Math.floor((t - grid.start) / grid.step);
  return grid.values[idx] ?? null;
}

/** Gruppiert Punkte nach einem Schlüssel (z. B. Tag) und bewahrt die Reihenfolge. */
export function groupBy<K>(pts: Point[], key: (p: Point) => K): Map<K, Point[]> {
  const map = new Map<K, Point[]>();
  for (const p of pts) {
    const k = key(p);
    const list = map.get(k);
    if (list) {
      list.push(p);
    } else {
      map.set(k, [p]);
    }
  }
  return map;
}

export const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);
export const min = (xs: number[]) => Math.min(...xs);
export const max = (xs: number[]) => Math.max(...xs);

/** Pearson-Korrelation zweier gleich langer Reihen. */
export function correlation(xs: number[], ys: number[]): number {
  const n = Math.min(xs.length, ys.length);
  if (n < 3) return NaN;
  const mx = mean(xs.slice(0, n));
  const my = mean(ys.slice(0, n));
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - mx;
    const dy = ys[i] - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  return sxy / Math.sqrt(sxx * syy);
}

/** Mittelwert pro Stunde des Tages (0–23, Schweizer Zeit). */
export function hourlyProfile(pts: Point[]): number[] {
  const groups = groupBy(pts, (p) => localParts(p.t).hour);
  return Array.from({ length: 24 }, (_, h) => mean((groups.get(h) ?? []).map((p) => p.v)));
}

// ---------- Formatierung ----------

const formatters = new Map<number, Intl.NumberFormat>();

export function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return '–';
  let f = formatters.get(digits);
  if (!f) {
    f = new Intl.NumberFormat('de-CH', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    formatters.set(digits, f);
  }
  return f.format(v);
}

const dayFmt = new Intl.DateTimeFormat('de-CH', { timeZone: TZ, day: 'numeric', month: 'short' });
const dayTimeFmt = new Intl.DateTimeFormat('de-CH', {
  timeZone: TZ,
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
});
const timeFmt = new Intl.DateTimeFormat('de-CH', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
const monthFmt = new Intl.DateTimeFormat('de-CH', { timeZone: TZ, month: 'short', year: '2-digit' });
const fullFmt = new Intl.DateTimeFormat('de-CH', {
  timeZone: TZ,
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

export const formatDay = (t: number) => dayFmt.format(t * 1000);
export const formatDayTime = (t: number) => dayTimeFmt.format(t * 1000);
export const formatTime = (t: number) => timeFmt.format(t * 1000);
export const formatMonth = (t: number) => monthFmt.format(t * 1000);
export const formatFull = (t: number) => fullFmt.format(t * 1000);

/** Wechselkurs für die Umrechnung EUR/MWh → Rp./kWh (Näherung). */
export const EUR_CHF = 0.94;
export const eurMwhToRpKwh = (v: number) => (v / 10) * EUR_CHF;
