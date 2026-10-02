/** Eingaben des Rechners – entspricht api/app/schemas.py (Anteile als 0–1). */
export interface SimulationRequest {
  location: { lat: number; lon: number; label: string };
  pv: { kwp: number; tilt: number; azimuth: number; loss: number };
  battery: {
    capacity_kwh: number;
    power_kw: number;
    efficiency: number;
    soc_min: number;
    soc_max: number;
    soc_start: number;
    cycle_cost: number;
  };
  household: { annual_kwh: number };
  tariff: { feed_in: 'spot' | 'fixed'; feed_in_fixed: number };
  grid: { limit_kw: number; allow_grid_charging: boolean; allow_battery_export: boolean };
  days: number;
}

export type StrategyKey = 'none' | 'rbc' | 'mpc' | 'optimal';

export interface StrategyResult {
  series: Record<'charge' | 'discharge' | 'import' | 'export' | 'curtailed' | 'soc', number[]>;
  cost_chf: number;
  cost_adjusted_chf: number;
  import_kwh: number;
  export_kwh: number;
  self_consumption: number | null;
  autarky: number | null;
  cycles: number;
}

export interface SimulationResult {
  pv_model: {
    r2: number;
    mae_w_per_kwp: number;
    train_rows: number;
    test_year: number;
    annual_kwh: number;
    monthly_kwh: number[];
  };
  backtest: {
    start: number;
    step: number;
    days: number;
    station: { abbr: string; name: string; km: number; height: number };
    pv_actual: number[];
    pv_forecast: number[];
    load_actual: number[];
    load_forecast: number[];
    price_import: number[];
    price_export: number[];
    no_pv_cost_chf: number;
    pv_forecast_mae_kw: number;
    strategies: Record<StrategyKey, StrategyResult>;
  };
  plan: {
    start: number;
    step: number;
    published_until: number;
    known: boolean[];
    pv: number[];
    load: number[];
    price_import: number[];
    price_export: number[];
    charge: number[];
    discharge: number[];
    grid_import: number[];
    grid_export: number[];
    soc: number[];
    cost_chf: number;
  };
  meta: { duration_s: number };
}

export interface Place {
  label: string;
  lat: number;
  lon: number;
}
