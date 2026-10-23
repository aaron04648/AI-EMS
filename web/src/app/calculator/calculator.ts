import { Component, OnDestroy, computed, effect, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { fmt, formatDay, formatDayTime, formatTime } from '../core/series';
import { BarChart, BarRow } from '../shared/charts/bar-chart';
import { Band, ChartSeries, LineChart } from '../shared/charts/line-chart';
import { Help } from '../shared/help';
import { Logo } from '../shared/logo';
import { ThemeToggle } from '../shared/theme-toggle';
import { ACTIONS, ACTION_KEYS, Action, SLOT, actionAt } from './actions';
import { Place, SimulationRequest, SimulationResult, StrategyKey } from './calculator.models';
import { TariffBoard } from './tariff-board';
import { WeatherMap } from './weather-map';

export type View = 'meteo' | 'boerse' | 'statistik';

/** Formularwerte in Anzeige-Einheiten (Prozent statt Anteil) */
interface Form {
  place: Place;
  kwp: number;
  tilt: number;
  azimuth: number;
  loss: number;
  capacity: number;
  power: number;
  efficiency: number;
  socMin: number;
  socMax: number;
  socStart: number;
  cycleCost: number;
  annualKwh: number;
  feedIn: 'spot' | 'fixed';
  feedInFixed: number;
  gridLimit: number;
  allowGridCharging: boolean;
  allowBatteryExport: boolean;
  days: number;
}

const DEFAULTS: Form = {
  place: { label: 'Zürich (ZH)', lat: 47.3769, lon: 8.5417 },
  kwp: 10,
  tilt: 30,
  azimuth: 0,
  loss: 14,
  capacity: 10,
  power: 5,
  efficiency: 95,
  socMin: 10,
  socMax: 100,
  socStart: 50,
  cycleCost: 2,
  annualKwh: 4500,
  feedIn: 'spot',
  feedInFixed: 8,
  gridLimit: 11,
  allowGridCharging: true,
  allowBatteryExport: true,
  days: 3,
};

const STORAGE_KEY = 'ai-ems-rechner';

export const ORIENTATIONS = [
  { label: 'Ost', value: -90 },
  { label: 'Südost', value: -45 },
  { label: 'Süd', value: 0 },
  { label: 'Südwest', value: 45 },
  { label: 'West', value: 90 },
];

const STRATEGIES: { key: StrategyKey; label: string; note: string }[] = [
  { key: 'none', label: 'Ohne Batterie', note: 'nur PV' },
  { key: 'rbc', label: 'Eigenverbrauchsregel', note: 'RBC, Paper 3.5.2' },
  { key: 'mpc', label: 'MPC', note: 'Prognosen, alle 15 min neu optimiert' },
  { key: 'optimal', label: 'Optimum', note: 'theoretisch, perfekte Voraussicht' },
];


@Component({
  selector: 'app-calculator',
  imports: [RouterLink, Logo, Help, ThemeToggle, LineChart, BarChart, WeatherMap, TariffBoard],
  templateUrl: './calculator.html',
  styleUrl: './calculator.scss',
})
export class Calculator implements OnDestroy {
  protected readonly fmt = fmt;
  protected readonly orientations = ORIENTATIONS;
  protected readonly strategies = STRATEGIES;
  protected readonly actions = ACTIONS;
  protected readonly actionKeys = ACTION_KEYS;
  protected readonly view = signal<View>('meteo');
  protected readonly views: { key: View; label: string }[] = [
    { key: 'meteo', label: 'Meteo' },
    { key: 'boerse', label: 'Börse' },
    { key: 'statistik', label: 'Statistik' },
  ];
  protected readonly formatDay = formatDay;
  protected readonly formatDayTime = formatDayTime;
  protected readonly formatTime = formatTime;

  protected readonly form = signal<Form>(this.restore());
  protected readonly result = signal<SimulationResult | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly elapsed = signal(0);
  protected readonly sidebarOpen = signal(false);

  // ---------- Ortssuche ----------
  protected readonly query = signal('');
  protected readonly suggestions = signal<Place[]>([]);
  private searchTimer?: ReturnType<typeof setTimeout>;
  private tick?: ReturnType<typeof setInterval>;

  constructor() {
    effect(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.form()));
      } catch {
        // Speichern ist nur eine Komfortfunktion
      }
    });
  }

  ngOnDestroy(): void {
    clearTimeout(this.searchTimer);
    clearInterval(this.tick);
  }

  private restore(): Form {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved ? { ...DEFAULTS, ...JSON.parse(saved) } : DEFAULTS;
    } catch {
      return DEFAULTS;
    }
  }

  protected set<K extends keyof Form>(key: K, value: Form[K]): void {
    this.form.update((f) => ({ ...f, [key]: value }));
  }

  protected num(key: keyof Form, event: Event): void {
    const value = Number((event.target as HTMLInputElement).value);
    if (!Number.isNaN(value)) {
      this.set(key, value as never);
    }
  }

  protected reset(): void {
    this.form.set(DEFAULTS);
  }

  protected search(event: Event): void {
    const text = (event.target as HTMLInputElement).value;
    this.query.set(text);
    clearTimeout(this.searchTimer);
    if (text.trim().length < 2) {
      this.suggestions.set([]);
      return;
    }
    this.searchTimer = setTimeout(async () => {
      try {
        const res = await fetch(`api/geocode?q=${encodeURIComponent(text.trim())}`);
        this.suggestions.set(res.ok ? await res.json() : []);
      } catch {
        this.suggestions.set([]);
      }
    }, 300);
  }

  protected choose(place: Place): void {
    this.set('place', place);
    this.query.set('');
    this.suggestions.set([]);
  }

  // ---------- Berechnung ----------
  private request(): SimulationRequest {
    const f = this.form();
    return {
      location: { lat: f.place.lat, lon: f.place.lon, label: f.place.label },
      pv: { kwp: f.kwp, tilt: f.tilt, azimuth: f.azimuth, loss: f.loss },
      battery: {
        capacity_kwh: f.capacity,
        power_kw: f.power,
        efficiency: f.efficiency / 100,
        soc_min: f.socMin / 100,
        soc_max: f.socMax / 100,
        soc_start: f.socStart / 100,
        cycle_cost: f.cycleCost,
      },
      household: { annual_kwh: f.annualKwh },
      tariff: { feed_in: f.feedIn, feed_in_fixed: f.feedInFixed },
      grid: {
        limit_kw: f.gridLimit,
        allow_grid_charging: f.allowGridCharging,
        allow_battery_export: f.allowBatteryExport,
      },
      days: f.days,
    };
  }

  protected async calculate(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.sidebarOpen.set(false);
    const started = Date.now();
    this.elapsed.set(0);
    this.tick = setInterval(() => this.elapsed.set(Math.round((Date.now() - started) / 1000)), 500);
    try {
      const res = await fetch('api/simulate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(this.request()),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = body?.detail;
        throw new Error(
          res.status === 404 || res.status === 405
            ? 'Das Backend ist hier nicht verfügbar. Der Rechner läuft in der Docker- oder Kubernetes-Umgebung (nicht auf GitHub Pages).'
            : typeof detail === 'string'
              ? detail
              : Array.isArray(detail)
                ? detail.map((d: { msg: string }) => d.msg).join(', ')
                : `Fehler ${res.status}`,
        );
      }
      this.result.set(await res.json());
    } catch (err) {
      this.error.set(
        err instanceof TypeError
          ? 'Das Backend ist nicht erreichbar. Läuft der API-Container?'
          : (err as Error).message,
      );
    } finally {
      clearInterval(this.tick);
      this.loading.set(false);
    }
  }

  // ---------- Fahrplan ----------
  protected readonly plan = computed(() => {
    const r = this.result();
    if (!r) return null;
    const p = r.plan;
    const x = p.pv.map((_, i) => p.start + i * p.step);

    const action = (i: number): Action => actionAt(p, i);

    // zusammenhängende Blöcke gleicher Aktion
    const blocks: { action: Action; from: number; to: number; priceSum: number; energy: number; n: number }[] = [];
    x.forEach((t, i) => {
      const a = action(i);
      const energy = (p.charge[i] + p.discharge[i]) * 0.25;
      const last = blocks[blocks.length - 1];
      // Massgebender Preis: beim Verkaufen und Laden mit Solarstrom die (entgangene) Einspeisevergütung
      const price = a === 'sell' || a === 'solarCharge' ? p.price_export[i] : p.price_import[i];
      if (last && last.action === a) {
        last.to = t + p.step;
        last.priceSum += price;
        last.energy += energy;
        last.n += 1;
      } else {
        blocks.push({ action: a, from: t, to: t + p.step, priceSum: price, energy, n: 1 });
      }
    });
    const span = x.length * p.step;
    const timeline = blocks.map((b) => ({ ...b, width: ((b.to - b.from) / span) * 100 }));
    const events = blocks
      .filter((b) => b.action !== 'idle' && b.energy >= 0.2)
      .map((b) => ({ ...b, price: b.priceSum / b.n }));

    const unknownFrom = x.findIndex((_, i) => !p.known[i]);
    const bands: Band[] = unknownFrom >= 0 ? [{ from: x[unknownFrom], to: x[x.length - 1] + p.step }] : [];

    return {
      x,
      bands,
      timeline,
      events,
      cost: p.cost_chf,
      publishedUntil: p.published_until,
      prices: [
        { name: 'Bezug EKZ Dynamisch', color: SLOT[0], values: p.price_import },
        { name: 'Einspeisung', color: SLOT[1], values: p.price_export },
      ] as ChartSeries[],
      power: [
        { name: 'PV-Prognose', color: SLOT[3], values: p.pv },
        { name: 'Verbrauch', color: SLOT[4], values: p.load },
        { name: 'Batterie (+ laden / – entladen)', color: SLOT[2], values: p.charge.map((c, i) => c - p.discharge[i]) },
        { name: 'Netz (+ Bezug / – Einspeisung)', color: SLOT[0], values: p.grid_import.map((g, i) => g - p.grid_export[i]) },
      ] as ChartSeries[],
      soc: [{ name: 'Ladestand', color: SLOT[2], values: p.soc }] as ChartSeries[],
    };
  });

  // ---------- Backtest ----------
  protected readonly backtest = computed(() => {
    const r = this.result();
    if (!r) return null;
    const b = r.backtest;
    const x = b.pv_actual.map((_, i) => b.start + i * b.step);
    const s = b.strategies;
    const cap = this.form().capacity || 1;
    const socPct = (k: StrategyKey) => s[k].series.soc.map((v) => (v / cap) * 100);

    const costs: BarRow[] = [
      { label: 'Ohne PV & Batterie', value: b.no_pv_cost_chf, note: 'nur Netzbezug' },
      ...STRATEGIES.map((st) => ({
        label: st.label,
        value: s[st.key].cost_adjusted_chf,
        note: st.note,
        highlight: st.key === 'mpc',
      })),
    ];
    const mpc = s.mpc.cost_adjusted_chf;
    const gap = s.optimal.cost_adjusted_chf - b.no_pv_cost_chf;
    const yearFactor = 365 / b.days;

    return {
      x,
      station: b.station,
      days: b.days,
      costs,
      mpc,
      savingVsNone: s.none.cost_adjusted_chf - mpc,
      savingVsRbc: s.rbc.cost_adjusted_chf - mpc,
      // Anteil des theoretisch möglichen Vorteils, den die MPC erreicht
      efficiency: gap !== 0 ? (mpc - b.no_pv_cost_chf) / gap : null,
      yearFactor,
      pvMae: b.pv_forecast_mae_kw,
      soc: [
        { name: 'Eigenverbrauchsregel', color: SLOT[1], values: socPct('rbc') },
        { name: 'MPC', color: SLOT[0], values: socPct('mpc') },
        { name: 'Optimum', color: SLOT[2], values: socPct('optimal') },
      ] as ChartSeries[],
      pv: [
        { name: 'Ist (MeteoSwiss-Messung)', color: SLOT[3], values: b.pv_actual },
        { name: 'Prognose vom Vortag (ICON-CH2)', color: SLOT[0], values: b.pv_forecast },
      ] as ChartSeries[],
      prices: [
        { name: 'Bezug EKZ Dynamisch', color: SLOT[0], values: b.price_import },
        { name: 'Einspeisung', color: SLOT[1], values: b.price_export },
      ] as ChartSeries[],
      table: STRATEGIES.map((st) => ({ ...st, ...s[st.key] })),
    };
  });

  protected readonly monthly = computed<BarRow[]>(() => {
    const r = this.result();
    if (!r) return [];
    const names = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
    return r.pv_model.monthly_kwh.map((v, i) => ({ label: names[i], value: v, note: `${names[i]} · Ø Monatsertrag` }));
  });

  protected readonly orientationLabel = computed(() => {
    const a = this.form().azimuth;
    const exact = ORIENTATIONS.find((o) => o.value === a);
    return exact ? exact.label : `${a > 0 ? '+' : ''}${a}°`;
  });

  protected pct(v: number | null, digits = 0): string {
    return v == null ? '–' : `${fmt(v * 100, digits)} %`;
  }

  protected readonly hourTick = (t: number) => formatTime(t);
  /** Achsenbeschriftung über mehrere Tage: „Fr 12:00“ */
  protected readonly dayHourTick = (t: number) =>
    new Date(t * 1000).toLocaleString('de-CH', { timeZone: 'Europe/Zurich', weekday: 'short', hour: '2-digit', minute: '2-digit' });

  /** PV-Prognose des Fahrplans für die Wetterkarte */
  protected readonly pvSeries = computed(() => {
    const r = this.result();
    return r ? { start: r.plan.start, step: r.plan.step, values: r.plan.pv } : null;
  });
}
