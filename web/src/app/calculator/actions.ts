import { SimulationResult } from './calculator.models';

/** Categorical colours in fixed order (validated dataviz palette) */
export const SLOT = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)'];

/** Battery actions in the schedule – colour follows the action, never the rank */
export const ACTIONS = {
  gridCharge: { label: 'Charge from the grid', color: SLOT[0] },
  solarCharge: { label: 'Charge with solar power', color: SLOT[3] },
  supply: { label: 'Battery supplies the house', color: SLOT[2] },
  sell: { label: 'Battery sells to the grid', color: SLOT[1] },
  idle: { label: 'Battery idle', color: 'var(--line)' },
} as const;

export type Action = keyof typeof ACTIONS;
export const ACTION_KEYS = Object.keys(ACTIONS) as Action[];

/** What the battery does in quarter hour i of the schedule. */
export function actionAt(p: SimulationResult['plan'], i: number): Action {
  if (p.charge[i] > 0.05) {
    return p.grid_import[i] > 0.05 && p.charge[i] > p.pv[i] - p.load[i] + 0.05 ? 'gridCharge' : 'solarCharge';
  }
  if (p.discharge[i] > 0.05) {
    return p.grid_export[i] > 0.05 && p.discharge[i] > p.load[i] - p.pv[i] + 0.05 ? 'sell' : 'supply';
  }
  return 'idle';
}
