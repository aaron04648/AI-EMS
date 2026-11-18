/** Central site information – navigation, contributors, repository. */
export const SITE = {
  name: 'AI-EMS',
  claim: 'Understand electricity prices. Shift consumption. Save money.',
  repo: 'https://github.com/aaron04648/AI-EMS',
  nav: [
    { label: 'Overview', path: '/', newTab: false },
    { label: 'Analysis', path: '/analysis', newTab: false },
    { label: 'Calculator', path: '/calculator', newTab: true },
  ],
  /** Contributors – simply add new people here (github optional). */
  contributors: [
    { name: 'Aaron Stupf', role: 'Website, infrastructure & PV model', github: 'aaron04648' },
    { name: 'Sebastian Lischer', role: 'Data analysis, MPC & CI/CD', github: '' },
  ],
  sources: [
    { name: 'Energy-Charts (Fraunhofer ISE)', url: 'https://energy-charts.info', license: 'CC BY 4.0' },
    { name: 'EKZ tariff API', url: 'https://api.tariffs.ekz.ch/swagger/index.html' },
  ],
} as const;
