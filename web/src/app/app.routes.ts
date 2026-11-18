import { Routes } from '@angular/router';
import { SiteLayout } from './layout/site-layout';

export const routes: Routes = [
  {
    path: 'calculator',
    title: 'Calculator – AI-EMS',
    loadComponent: () => import('./calculator/calculator').then((m) => m.Calculator),
  },
  {
    path: '',
    component: SiteLayout,
    children: [
      {
        path: '',
        title: 'AI-EMS – Understanding dynamic electricity tariffs',
        loadComponent: () => import('./pages/home/home').then((m) => m.Home),
      },
      {
        path: 'analysis',
        title: 'Analysis – AI-EMS',
        loadComponent: () => import('./pages/analysis/analysis').then((m) => m.Analysis),
      },
    ],
  },
  {
    path: '**',
    redirectTo: '',
  },
];
