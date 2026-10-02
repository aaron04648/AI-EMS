import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DataService } from '../../core/data.service';
import { Grid, PowerGroup } from '../../core/data.models';
import {
  Point,
  correlation,
  eurMwhToRpKwh,
  fmt,
  formatDay,
  formatDayTime,
  groupBy,
  hourlyProfile,
  localParts,
  max,
  mean,
  min,
  points,
  valueAt,
} from '../../core/series';
import { BarChart, BarRow } from '../../shared/charts/bar-chart';
import { ChartSeries, LineChart } from '../../shared/charts/line-chart';
import { RevealDirective } from '../../shared/reveal.directive';

/** Kategoriale Farben in fester Reihenfolge (validiert, siehe Dataviz-Referenzpalette) */
const SLOT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'];
const DAY = 86400;
const MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

/** Erzeugungsgruppen der Schweiz in Stapelreihenfolge (unten → oben) */
const CH_MIX: { key: PowerGroup | 'windOther'; label: string }[] = [
  { key: 'hydroRiver', label: 'Laufwasser' },
  { key: 'nuclear', label: 'Kernenergie' },
  { key: 'hydroReservoir', label: 'Speicher- & Pumpspeicher' },
  { key: 'solar', label: 'Solar' },
  { key: 'windOther', label: 'Wind & Übrige' },
];

const dailyAverages = (pts: Point[]) =>
  [...groupBy(pts, (p) => localParts(p.t).day).entries()].map(([day, list]) => ({
    day,
    t: list[0].t,
    avg: mean(list.map((p) => p.v)),
    spread: max(list.map((p) => p.v)) - min(list.map((p) => p.v)),
    count: list.length,
  }));

@Component({
  selector: 'app-analysis',
  imports: [RouterLink, RevealDirective, LineChart, BarChart],
  templateUrl: './analysis.html',
  styleUrl: './analysis.scss',
})
export class Analysis {
  protected readonly data = inject(DataService);
  protected readonly fmt = fmt;
  protected readonly formatDay = formatDay;
  protected readonly formatDayTime = formatDayTime;
  protected readonly hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`;
  protected readonly hours = Array.from({ length: 24 }, (_, h) => h);

  private readonly ch = computed(() => (this.data.chPrices.hasValue() ? this.data.chPrices.value() : null));
  private readonly eu = computed(() => (this.data.euPrices.hasValue() ? this.data.euPrices.value() : null));
  private readonly mix = computed(() => (this.data.powerMix.hasValue() ? this.data.powerMix.value() : null));
  private readonly ekz = computed(() => (this.data.ekz.hasValue() ? this.data.ekz.value() : null));

  // =============================================================
  // Schweiz
  // =============================================================

  private readonly chYear = computed(() => {
    const ch = this.ch();
    return ch ? points(ch, -Infinity, Date.now() / 1000) : [];
  });

  protected readonly chKpis = computed(() => {
    const year = this.chYear();
    if (!year.length) return null;
    const last = year[year.length - 1].t;
    const month = year.filter((p) => p.t > last - 30 * DAY);
    const days = dailyAverages(month).filter((d) => d.count >= 23);
    const negative = year.filter((p) => p.v < 0).length;
    return {
      avgYear: mean(year.map((p) => p.v)),
      avgMonth: mean(month.map((p) => p.v)),
      negative,
      spread: mean(days.map((d) => d.spread)),
    };
  });

  /** Monatsmittel der letzten 12 Monate */
  protected readonly monthly = computed<BarRow[]>(() => {
    const groups = groupBy(this.chYear(), (p) => localParts(p.t).month);
    const rows = [...groups.entries()]
      .filter(([, list]) => list.length > 24 * 7) // angebrochene Monate am Rand weglassen
      .map(([month, list]) => {
        const [y, m] = month.split('-').map(Number);
        return {
          label: MONTHS[m - 1],
          value: mean(list.map((p) => p.v)),
          note: `${MONTHS[m - 1]} ${y}`,
        } as BarRow;
      });
    if (rows.length) {
      const top = rows.reduce((a, b) => (b.value > a.value ? b : a));
      const low = rows.reduce((a, b) => (b.value < a.value ? b : a));
      top.highlight = true;
      low.highlight = true;
    }
    return rows;
  });

  protected readonly monthlyExtremes = computed(() => {
    const rows = this.monthly();
    if (rows.length < 2) return null;
    const sorted = [...rows].sort((a, b) => a.value - b.value);
    return { low: sorted[0], high: sorted[sorted.length - 1] };
  });

  /** Tagesprofil Sommer- vs. Winterhalbjahr */
  protected readonly seasonProfile = computed(() => {
    const year = this.chYear();
    if (!year.length) return null;
    const isSummer = (p: Point) => {
      const m = Number(localParts(p.t).month.slice(5));
      return m >= 4 && m <= 9;
    };
    const summer = hourlyProfile(year.filter(isSummer));
    const winter = hourlyProfile(year.filter((p) => !isSummer(p)));
    const series: ChartSeries[] = [
      { name: 'Sommerhalbjahr (Apr–Sep)', color: SLOT[3], values: summer },
      { name: 'Winterhalbjahr (Okt–Mär)', color: SLOT[0], values: winter },
    ];
    return {
      series,
      summerNoon: summer[13],
      summerEvening: summer[20],
      winterMorning: winter[8],
      winterNight: winter[3],
    };
  });

  /** Ø Preis je Wochentag (letzte 12 Monate) */
  protected readonly weekdays = computed<BarRow[]>(() => {
    const groups = groupBy(this.chYear(), (p) => (localParts(p.t).weekday + 6) % 7);
    return WEEKDAYS.map((label, i) => ({
      label,
      value: mean((groups.get(i) ?? []).map((p) => p.v)),
      highlight: i >= 5,
    }));
  });

  /** Erzeugungsmix CH, Preis und Netto-Import der letzten 14 Tage (stündlich) */
  protected readonly chMix = computed(() => {
    const mix = this.mix()?.ch;
    const ch = this.ch();
    if (!mix || !ch) return null;
    const s = mix.series;
    const len = s.load?.length ?? 0;
    const from = Math.max(0, len - 14 * 24);
    const idx = Array.from({ length: len - from }, (_, i) => from + i);
    const x = idx.map((i) => mix.start + i * mix.step);
    const val = (key: PowerGroup, i: number) => s[key]?.[i] ?? null;

    const series: ChartSeries[] = CH_MIX.map((g, slot) => ({
      name: g.label,
      color: SLOT[slot],
      values: idx.map((i) =>
        g.key === 'windOther'
          ? (val('wind', i) ?? 0) + (val('other', i) ?? 0)
          : val(g.key as PowerGroup, i),
      ),
    }));
    const price = x.map((t) => valueAt(ch, t));
    const imports = idx.map((i) => val('crossBorder', i));

    // Ø Erzeugung je Quelle als Tabelle (zugleich Text-Alternative zum Flächendiagramm)
    const totals = series.map((ser) => mean(ser.values.filter((v): v is number => v != null)));
    const sum = totals.reduce((a, b) => a + b, 0);
    const table = series.map((ser, i) => ({ name: ser.name, color: ser.color, mw: totals[i], share: (totals[i] / sum) * 100 }));

    return {
      x,
      series,
      price: [{ name: 'Day-Ahead Schweiz', color: SLOT[0], values: price }],
      imports: [{ name: 'Netto-Import (+) / Export (–)', color: SLOT[1], values: imports }],
      table,
    };
  });

  /** Korrelation des Preises mit den Treibern (stündlich, letzte 30 Tage) */
  protected readonly drivers = computed(() => {
    const mix = this.mix()?.ch;
    const ch = this.ch();
    if (!mix || !ch) return null;
    const s = mix.series;
    const rows: { key: PowerGroup; label: string; note: string }[] = [
      { key: 'residualLoad', label: 'Residuallast', note: 'Last minus Wind & Solar' },
      { key: 'load', label: 'Stromverbrauch', note: 'Gesamtlast Schweiz' },
      { key: 'crossBorder', label: 'Netto-Import', note: 'Import positiv, Export negativ' },
      { key: 'hydroReservoir', label: 'Speicherwasser', note: 'Produktion aus Stauseen & Pumpspeicher' },
      { key: 'solar', label: 'Solar', note: 'Einspeisung Photovoltaik' },
    ];
    const result: BarRow[] = [];
    for (const r of rows) {
      const values = s[r.key];
      if (!values) continue;
      const xs: number[] = [];
      const ys: number[] = [];
      values.forEach((v, i) => {
        const p = valueAt(ch, mix.start + i * mix.step);
        if (v != null && p != null) {
          xs.push(v);
          ys.push(p);
        }
      });
      result.push({ label: r.label, value: correlation(xs, ys), note: r.note, highlight: true });
    }

    // Preis in sonnenreichen vs. sonnenarmen Tagesstunden (10–16 Uhr)
    const solar = s.solar ?? [];
    const day: { solar: number; price: number }[] = [];
    solar.forEach((v, i) => {
      const t = mix.start + i * mix.step;
      const h = localParts(t).hour;
      const p = valueAt(ch, t);
      if (v != null && p != null && h >= 10 && h <= 16) day.push({ solar: v, price: p });
    });
    day.sort((a, b) => a.solar - b.solar);
    const q = Math.floor(day.length / 4);
    const sunny = mean(day.slice(-q).map((d) => d.price));
    const cloudy = mean(day.slice(0, q).map((d) => d.price));

    return { rows: result.sort((a, b) => b.value - a.value), sunny, cloudy };
  });

  // =============================================================
  // Europa
  // =============================================================

  private readonly zonesDaily = computed(() => {
    const eu = this.eu();
    if (!eu) return [];
    const now = Date.now() / 1000;
    return eu.zones.map((z) => {
      const days = dailyAverages(points(z, -Infinity, now)).filter((d) => d.count >= 23);
      return { code: z.code, name: z.name, days };
    });
  });

  protected readonly euAverage = computed<BarRow[]>(() =>
    this.zonesDaily()
      .map((z) => ({
        label: z.name,
        value: mean(z.days.map((d) => d.avg)),
        highlight: z.code === 'CH',
        note: `${z.code} · ${z.days.length} Tage`,
      }))
      .sort((a, b) => b.value - a.value),
  );

  protected readonly euSpread = computed<BarRow[]>(() =>
    this.zonesDaily()
      .map((z) => ({
        label: z.name,
        value: mean(z.days.map((d) => d.spread)),
        highlight: z.code === 'CH',
        note: 'Ø Differenz zwischen teuerster und günstigster Stunde',
      }))
      .sort((a, b) => b.value - a.value),
  );

  protected readonly euTrend = computed(() => {
    const wanted = ['CH', 'DE-LU', 'FR', 'IT-North'];
    const zones = wanted
      .map((code) => this.zonesDaily().find((z) => z.code === code))
      .filter((z) => !!z && z.days.length);
    if (!zones.length) return null;
    const x = zones[0]!.days.map((d) => d.t);
    const series: ChartSeries[] = zones.map((z, i) => {
      const byDay = new Map(z!.days.map((d) => [d.day, d.avg]));
      return { name: z!.name, color: SLOT[i], values: zones[0]!.days.map((d) => byDay.get(d.day) ?? null) };
    });
    return { x, series };
  });

  /** Je Land: Ø Wind+Solar-Anteil an der Last und Korrelation mit dem Tagespreis */
  protected readonly renewables = computed(() => {
    const mix = this.mix();
    const eu = this.eu();
    if (!mix || !eu) return null;
    const share: BarRow[] = [];
    const corr: BarRow[] = [];
    for (const c of mix.countries) {
      const zone = eu.zones.find((z) => z.code === c.zone);
      if (!zone) continue;
      const daily = new Map(dailyAverages(points(zone)).map((d) => [d.day, d.avg]));
      const xs: number[] = [];
      const ys: number[] = [];
      const shares: number[] = [];
      const len = c.daily.load?.length ?? 0;
      for (let i = 0; i < len; i++) {
        const load = c.daily.load?.[i];
        const res = (c.daily.solar?.[i] ?? 0) + (c.daily.wind?.[i] ?? 0);
        const price = daily.get(localParts(c.start + i * c.step + DAY / 2).day);
        if (!load || price == null) continue;
        const sh = (res / load) * 100;
        shares.push(sh);
        xs.push(sh);
        ys.push(price);
      }
      if (shares.length < 7) continue;
      share.push({ label: c.name, value: mean(shares), highlight: c.country === 'ch' });
      corr.push({
        label: c.name,
        value: correlation(xs, ys),
        highlight: c.country === 'ch',
        note: `${xs.length} Tage`,
      });
    }
    return {
      share: share.sort((a, b) => b.value - a.value),
      corr: corr.sort((a, b) => a.value - b.value),
    };
  });

  // =============================================================
  // EKZ
  // =============================================================

  protected readonly ekzVsSpot = computed(() => {
    const tariff = this.ekz()?.tariffs.find((t) => t.key === 'energyDynamic');
    const ch = this.ch();
    if (!tariff || !ch) return null;
    const pts = points(tariff as Grid);
    if (!pts.length) return null;
    const end = pts[pts.length - 1].t;
    const recent = pts.filter((p) => p.t > end - 7 * DAY && p.t % 3600 === 0);
    const x = recent.map((p) => p.t);
    const spot = x.map((t) => {
      const v = valueAt(ch, t);
      return v == null ? null : eurMwhToRpKwh(v);
    });
    const pairs = recent
      .map((p, i) => [p.v, spot[i]] as const)
      .filter((pair): pair is readonly [number, number] => pair[1] != null);
    return {
      x,
      series: [
        { name: 'EKZ Energie Dynamisch', color: SLOT[0], values: recent.map((p) => p.v) },
        { name: 'Day-Ahead Schweiz (umgerechnet)', color: SLOT[1], values: spot },
      ] as ChartSeries[],
      corr: correlation(pairs.map((p) => p[0]), pairs.map((p) => p[1])),
      avgEkz: mean(pairs.map((p) => p[0])),
      avgSpot: mean(pairs.map((p) => p[1])),
    };
  });

  protected readonly dayTick = (t: number) => formatDay(t);
}
