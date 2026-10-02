import { Routes } from '@angular/router';
import { SiteLayout } from './layout/site-layout';

export const routes: Routes = [
  {
    path: 'rechner',
    title: 'Rechner – AI-EMS',
    loadComponent: () => import('./calculator/calculator').then((m) => m.Calculator),
  },
  {
    path: '',
    component: SiteLayout,
    children: [
      {
        path: '',
        title: 'AI-EMS – Dynamische Stromtarife verstehen',
        loadComponent: () => import('./pages/home/home').then((m) => m.Home),
      },
      {
        path: 'analyse',
        title: 'Analyse – AI-EMS',
        loadComponent: () => import('./pages/analysis/analysis').then((m) => m.Analysis),
      },
    ],
  },
  {
    path: '**',
    redirectTo: '',
  },
];
