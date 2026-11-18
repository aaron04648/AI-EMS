/** Time series on a fixed grid: value i belongs to time start + i * step (unix seconds). */
export interface Grid {
  start: number;
  step: number;
  values: (number | null)[];
}

export interface ChPrices extends Grid {
  unit: string;
  zone: string;
}

export interface Zone extends Grid {
  code: string;
  name: string;
}

export interface EuPrices {
  unit: string;
  zones: Zone[];
}

export type PowerGroup =
  | 'solar'
  | 'wind'
  | 'hydroRiver'
  | 'hydroReservoir'
  | 'nuclear'
  | 'fossil'
  | 'other'
  | 'load'
  | 'residualLoad'
  | 'crossBorder';

export interface CountryMix {
  country: string;
  zone: string;
  name: string;
  start: number;
  step: number;
  daily: Partial<Record<PowerGroup, (number | null)[]>>;
}

export interface PowerMix {
  unit: string;
  ch: { start: number; step: number; series: Partial<Record<PowerGroup, (number | null)[]>> } | null;
  countries: CountryMix[];
}

export interface EkzTariff extends Grid {
  key: 'dynamic' | 'standard' | 'energyDynamic';
  name: string;
  label: string;
  unit: string;
  monthlyFee: number | null;
}

export interface Ekz {
  tariffs: EkzTariff[];
}

export interface Meta {
  generatedAt: string;
  status: Record<string, 'ok' | 'error'>;
  sources: { name: string; url: string; license?: string }[];
}
