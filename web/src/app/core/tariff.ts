import { EkzTariff } from './data.models';
import { Point, groupBy, localParts, mean, points } from './series';

/** Alle Viertelstunden eines Tages (Schweizer Zeit). */
export interface TariffDay {
  day: string;
  slots: Point[];
}

/** Zerlegt einen EKZ-Tarif in vollständige Kalendertage (92–100 Viertelstunden wegen Sommerzeit). */
export function tariffDays(tariff: EkzTariff): TariffDay[] {
  const byDay = groupBy(points(tariff), (p) => localParts(p.t).day);
  return [...byDay.entries()]
    .filter(([, slots]) => slots.length >= 92)
    .map(([day, slots]) => ({ day, slots }));
}

/** Günstigstes zusammenhängendes Zeitfenster einer bestimmten Länge. */
export function cheapestWindow(slots: Point[], quarters: number): { from: number; to: number; avg: number } | null {
  if (slots.length < quarters) return null;
  let best = { from: 0, to: 0, avg: Infinity };
  for (let i = 0; i + quarters <= slots.length; i++) {
    const avg = mean(slots.slice(i, i + quarters).map((p) => p.v));
    if (avg < best.avg) {
      best = { from: slots[i].t, to: slots[i + quarters - 1].t + 900, avg };
    }
  }
  return best;
}

// ---------------------------------------------------------------
// Lastprofile (Anteil pro Stunde 0–23, Summe = 1)
// ---------------------------------------------------------------

const normalize = (xs: number[]) => {
  const sum = xs.reduce((a, b) => a + b, 0);
  return xs.map((x) => x / sum);
};

export type ProfileKey = 'household' | 'ev' | 'heatPump';

/** Vereinfachte, typische Verbrauchsmuster – angelehnt an das Standardlastprofil H0. */
export const PROFILES: Record<ProfileKey, { label: string; weights: number[] }> = {
  household: {
    label: 'Haushalt',
    weights: normalize([
      2.6, 2.2, 2.0, 1.9, 1.9, 2.2, 3.3, 4.5, 4.7, 4.5, 4.4, 4.6, 5.0, 4.7, 4.2, 4.0, 4.2, 5.0, 6.2, 6.4,
      6.0, 5.4, 4.5, 3.5,
    ]),
  },
  ev: {
    label: 'Elektroauto (Laden nach Feierabend)',
    weights: normalize([
      3, 2, 1, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1, 4, 14, 18, 17, 14,
      10, 6,
    ]),
  },
  heatPump: {
    label: 'Wärmepumpe',
    weights: normalize([
      4.5, 4.5, 4.6, 4.7, 4.9, 5.2, 5.6, 5.4, 4.8, 4.0, 3.5, 3.2, 3.0, 3.0, 3.1, 3.3, 3.6, 4.1, 4.5, 4.6,
      4.6, 4.5, 4.4, 4.4,
    ]),
  },
};

export interface CalcInput {
  /** Jahresverbrauch je Profil in kWh */
  consumption: Record<ProfileKey, number>;
  /** Anteil des Tagesverbrauchs, der in günstige Zeiten verschoben werden kann (0–1) */
  flexShare: number;
  /** maximale Leistung für verschobene Lasten in kW (z. B. Wallbox 11 kW) */
  maxPowerKw: number;
}

export interface CalcResult {
  days: number;
  from: string;
  to: string;
  annualKwh: number;
  /** Ø bezahlter Preis in Rp./kWh */
  avgStandard: number;
  avgDynamic: number;
  avgOptimized: number;
  /** Jahreskosten in CHF inkl. Grundgebühr */
  costStandard: number;
  costDynamic: number;
  costOptimized: number;
  /** durchschnittlicher Lastgang (kWh je Viertelstunde) vor/nach der Verschiebung */
  loadBefore: number[];
  loadAfter: number[];
}

/** Viertelstunden-Lastgang eines Tages aus den Stundenprofilen. */
function dailyLoad(slots: Point[], input: CalcInput): number[] {
  const perHour = Array(24).fill(0);
  for (const key of Object.keys(PROFILES) as ProfileKey[]) {
    const daily = input.consumption[key] / 365;
    PROFILES[key].weights.forEach((w, h) => (perHour[h] += w * daily));
  }
  // Stunde auf ihre Viertelstunden verteilen (Sommerzeit-Tage haben 23/25 Stunden)
  const quartersPerHour = new Map<number, number>();
  for (const s of slots) {
    const h = localParts(s.t).hour;
    quartersPerHour.set(h, (quartersPerHour.get(h) ?? 0) + 1);
  }
  return slots.map((s) => {
    const h = localParts(s.t).hour;
    return perHour[h] / (quartersPerHour.get(h) ?? 4);
  });
}

/** Verschiebt den flexiblen Anteil in die günstigsten Viertelstunden des Tages. */
function shiftLoad(load: number[], prices: number[], flexShare: number, maxPowerKw: number): number[] {
  const flexible = load.reduce((a, b) => a + b, 0) * flexShare;
  const shifted = load.map((e) => e * (1 - flexShare));
  const capacity = maxPowerKw / 4; // kWh pro Viertelstunde
  const order = prices.map((p, i) => ({ p, i })).sort((a, b) => a.p - b.p);
  let rest = flexible;
  for (const { i } of order) {
    if (rest <= 0) break;
    const add = Math.min(rest, Math.max(0, capacity - shifted[i]));
    shifted[i] += add;
    rest -= add;
  }
  // Reicht die Leistung nicht, bleibt der Rest dort, wo er ursprünglich anfiel
  if (rest > 1e-9) {
    const scale = rest / flexible;
    load.forEach((e, i) => (shifted[i] += e * flexShare * scale));
  }
  return shifted;
}

/**
 * Vergleicht EKZ Standard mit EKZ Dynamisch – einmal mit unverändertem
 * Verbrauch und einmal mit verschobenen flexiblen Lasten. Grundlage sind
 * alle vollständigen Tage, für die beide Tarife vorliegen; das Ergebnis
 * wird auf ein Jahr hochgerechnet.
 */
export function calculate(standard: EkzTariff, dynamic: EkzTariff, input: CalcInput): CalcResult | null {
  const std = new Map(tariffDays(standard).map((d) => [d.day, d.slots]));
  const days = tariffDays(dynamic).filter((d) => std.get(d.day)?.length === d.slots.length);
  if (!days.length) return null;

  let energy = 0;
  let costStd = 0;
  let costDyn = 0;
  let costOpt = 0;
  const loadBefore = Array(96).fill(0);
  const loadAfter = Array(96).fill(0);

  for (const { day, slots } of days) {
    const stdSlots = std.get(day)!;
    const pricesDyn = slots.map((s) => s.v);
    const load = dailyLoad(slots, input);
    const shifted = shiftLoad(load, pricesDyn, input.flexShare, input.maxPowerKw);

    load.forEach((e, i) => {
      energy += e;
      costStd += e * stdSlots[i].v;
      costDyn += e * pricesDyn[i];
      costOpt += shifted[i] * pricesDyn[i];
      const q = localParts(slots[i].t);
      const idx = q.hour * 4 + Math.floor(q.minute / 15);
      loadBefore[idx] += e / days.length;
      loadAfter[idx] += shifted[i] / days.length;
    });
  }

  const annualKwh = Object.values(input.consumption).reduce((a, b) => a + b, 0);
  const fee = (dynamic.monthlyFee ?? 0) * 12;
  const feeStd = (standard.monthlyFee ?? 0) * 12;
  const avgStandard = costStd / energy;
  const avgDynamic = costDyn / energy;
  const avgOptimized = costOpt / energy;

  return {
    days: days.length,
    from: days[0].day,
    to: days[days.length - 1].day,
    annualKwh,
    avgStandard,
    avgDynamic,
    avgOptimized,
    costStandard: (avgStandard * annualKwh) / 100 + feeStd,
    costDynamic: (avgDynamic * annualKwh) / 100 + fee,
    costOptimized: (avgOptimized * annualKwh) / 100 + fee,
    loadBefore,
    loadAfter,
  };
}
