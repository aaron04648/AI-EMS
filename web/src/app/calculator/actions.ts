import { SimulationResult } from './calculator.models';

/** Kategoriale Farben in fester Reihenfolge (validierte Dataviz-Palette) */
export const SLOT = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)'];

/** Aktionen der Batterie im Fahrplan – Farbe folgt der Aktion, nie dem Rang */
export const ACTIONS = {
  gridCharge: { label: 'Aus dem Netz laden', color: SLOT[0] },
  solarCharge: { label: 'Mit Solarstrom laden', color: SLOT[3] },
  supply: { label: 'Batterie versorgt das Haus', color: SLOT[2] },
  sell: { label: 'Batterie verkauft ins Netz', color: SLOT[1] },
  idle: { label: 'Batterie ruht', color: 'var(--line)' },
} as const;

export type Action = keyof typeof ACTIONS;
export const ACTION_KEYS = Object.keys(ACTIONS) as Action[];

/** Was die Batterie in Viertelstunde i des Fahrplans tut. */
export function actionAt(p: SimulationResult['plan'], i: number): Action {
  if (p.charge[i] > 0.05) {
    return p.grid_import[i] > 0.05 && p.charge[i] > p.pv[i] - p.load[i] + 0.05 ? 'gridCharge' : 'solarCharge';
  }
  if (p.discharge[i] > 0.05) {
    return p.grid_export[i] > 0.05 && p.discharge[i] > p.load[i] - p.pv[i] + 0.05 ? 'sell' : 'supply';
  }
  return 'idle';
}
