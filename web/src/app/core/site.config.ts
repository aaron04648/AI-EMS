/** Zentrale Angaben zur Seite – Navigation, Mitwirkende, Repository. */
export const SITE = {
  name: 'AI-EMS',
  claim: 'Strompreise verstehen. Verbrauch verschieben. Geld sparen.',
  repo: 'https://github.com/aaron04648/AI-EMS',
  nav: [
    { label: 'Übersicht', path: '/', newTab: false },
    { label: 'Analyse', path: '/analyse', newTab: false },
    { label: 'Rechner', path: '/rechner', newTab: true },
  ],
  /** Mitwirkende – neue Personen einfach hier ergänzen. */
  contributors: [
    { name: 'Aaron Stupf', role: 'Entwicklung & Infrastruktur', github: 'aaron04648' },
  ],
  sources: [
    { name: 'Energy-Charts (Fraunhofer ISE)', url: 'https://energy-charts.info', license: 'CC BY 4.0' },
    { name: 'EKZ Tarif-API', url: 'https://api.tariffs.ekz.ch/swagger/index.html' },
  ],
} as const;
