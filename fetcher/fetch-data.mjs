// =============================================================
// AI-EMS – Daten-Fetcher
// Holt Strompreise und Erzeugungsdaten von energy-charts.info
// (Fraunhofer ISE) sowie die Tarife der EKZ und legt sie als
// kompakte JSON-Snapshots ab. Die Webseite liest nur diese Dateien:
// energy-charts.info erlaubt keine Browser-Anfragen fremder Domains
// (CORS) und drosselt viele Anfragen (HTTP 429).
//
//   node fetch-data.mjs            einmal ausführen
//   node fetch-data.mjs --loop     alle INTERVAL_MINUTES wiederholen
//
// Umgebungsvariablen:
//   OUT_DIR           Zielordner (Standard: ../web/public/data)
//   INTERVAL_MINUTES  Intervall im Loop-Modus (Standard: 180)
// =============================================================
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(process.env.OUT_DIR ?? join(here, '../web/public/data'));
const INTERVAL_MINUTES = Number(process.env.INTERVAL_MINUTES ?? 180);

const EC_API = 'https://api.energy-charts.info';
const EKZ_API = 'https://api.tariffs.ekz.ch/v1/tariffs';
const HOUR = 3600;
const QUARTER = 900;
const DAY = 86400;

// Gebotszonen für den Europa-Vergleich (Day-Ahead-Preise)
const ZONES = [
  { code: 'CH', name: 'Schweiz' },
  { code: 'DE-LU', name: 'Deutschland' },
  { code: 'FR', name: 'Frankreich' },
  { code: 'AT', name: 'Österreich' },
  { code: 'IT-North', name: 'Italien Nord' },
  { code: 'NL', name: 'Niederlande' },
  { code: 'BE', name: 'Belgien' },
  { code: 'PL', name: 'Polen' },
  { code: 'ES', name: 'Spanien' },
  { code: 'DK1', name: 'Dänemark West' },
  { code: 'NO2', name: 'Norwegen Süd' },
  { code: 'SE3', name: 'Schweden Mitte' },
  { code: 'CZ', name: 'Tschechien' },
  { code: 'HU', name: 'Ungarn' },
];

// Länder für den Erzeugungsmix (public_power) – Preis über die Zone
const MIX_COUNTRIES = [
  { country: 'ch', zone: 'CH', name: 'Schweiz' },
  { country: 'de', zone: 'DE-LU', name: 'Deutschland' },
  { country: 'fr', zone: 'FR', name: 'Frankreich' },
  { country: 'at', zone: 'AT', name: 'Österreich' },
  { country: 'it', zone: 'IT-North', name: 'Italien' },
  { country: 'es', zone: 'ES', name: 'Spanien' },
  { country: 'pl', zone: 'PL', name: 'Polen' },
  { country: 'nl', zone: 'NL', name: 'Niederlande' },
];

// EKZ-Produkte (Haushalt, Netzebene 7): integriert = Energie + Netz + Abgaben
const EKZ_TARIFFS = [
  { key: 'dynamic', name: 'integrated_400D', label: 'EKZ Energie Dynamisch + Netz 400D' },
  { key: 'standard', name: 'integrated_400ST', label: 'EKZ Energie Erneuerbar + Netz 400ST' },
  { key: 'energyDynamic', name: 'electricity_dynamic', label: 'EKZ Energie Dynamisch (nur Energie)' },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...args) => console.log(new Date().toISOString(), ...args);

let lastEcCall = 0;

/** GET mit Drosselung (energy-charts) und Wiederholung bei 429/5xx. */
async function getJson(url, { throttleMs = 0 } = {}) {
  for (let attempt = 1; attempt <= 5; attempt++) {
    const wait = lastEcCall + throttleMs - Date.now();
    if (throttleMs && wait > 0) await sleep(wait);
    if (throttleMs) lastEcCall = Date.now();

    try {
      const res = await fetch(url, {
        headers: { accept: 'application/json', 'user-agent': 'AI-EMS data fetcher' },
        signal: AbortSignal.timeout(60_000),
      });
      if (res.ok) return await res.json();
      if (res.status !== 429 && res.status < 500) {
        throw new Error(`HTTP ${res.status} für ${url}`);
      }
      log(`HTTP ${res.status} – Versuch ${attempt}/5: ${url}`);
    } catch (err) {
      if (attempt === 5 || /HTTP 4\d\d/.test(err.message)) throw err;
      log(`Fehler – Versuch ${attempt}/5: ${err.message}`);
    }
    await sleep(15_000 * attempt);
  }
  throw new Error(`Keine Antwort nach 5 Versuchen: ${url}`);
}

const ec = (path) => getJson(`${EC_API}${path}`, { throttleMs: 6000 });
const isoDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);
const round = (v, digits = 2) => (v == null || Number.isNaN(v) ? null : Number(v.toFixed(digits)));

/**
 * Mittelt eine Zeitreihe (unix-Sekunden + Werte) auf ein festes Raster.
 * Ergebnis: { start, step, values } – fehlende Slots sind null.
 */
function resample(times, values, step, start, end, digits = 2) {
  const n = Math.max(0, Math.floor((end - start) / step));
  const sum = new Array(n).fill(0);
  const count = new Array(n).fill(0);
  times.forEach((t, i) => {
    const v = values[i];
    if (v == null) return;
    const idx = Math.floor((t - start) / step);
    if (idx < 0 || idx >= n) return;
    sum[idx] += v;
    count[idx] += 1;
  });
  return {
    start,
    step,
    values: sum.map((s, i) => (count[i] ? round(s / count[i], digits) : null)),
  };
}

/** Vorherige Version einer Datei (für Teilausfälle der Quelle) – oder null. */
async function previous(name) {
  try {
    return JSON.parse(await readFile(join(OUT_DIR, name), 'utf8'));
  } catch {
    return null;
  }
}

async function writeJson(name, data) {
  await mkdir(OUT_DIR, { recursive: true });
  const target = join(OUT_DIR, name);
  const tmp = `${target}.tmp`;
  await writeFile(tmp, JSON.stringify(data));
  await rename(tmp, target); // atomar: nginx liefert nie eine halbe Datei aus
  log(`geschrieben: ${name}`);
}

// ---------------------------------------------------------------
// Datensätze
// ---------------------------------------------------------------

/** Schweizer Day-Ahead-Preis der letzten 365 Tage (stündlich). */
async function chPrices(now) {
  const start = now - 365 * DAY;
  const end = now + 2 * DAY; // inkl. bereits veröffentlichter Preise für morgen
  const d = await ec(`/price?bzn=CH&start=${isoDate(start)}&end=${isoDate(end)}`);
  return { unit: 'EUR/MWh', zone: 'CH', ...resample(d.unix_seconds, d.price, HOUR, start, end) };
}

/** Day-Ahead-Preise aller Zonen der letzten 30 Tage (stündlich). */
async function euPrices(now) {
  const start = now - 30 * DAY;
  const end = now + 2 * DAY;
  const zones = [];
  for (const zone of ZONES) {
    try {
      const d = await ec(`/price?bzn=${zone.code}&start=${isoDate(start)}&end=${isoDate(end)}`);
      zones.push({ ...zone, ...resample(d.unix_seconds, d.price, HOUR, start, end) });
    } catch (err) {
      // Teilausfall: letzte bekannte Werte der Zone behalten
      const old = (await previous('eu-prices.json'))?.zones?.find((z) => z.code === zone.code);
      if (old) zones.push(old);
      log(`Zone ${zone.code} ${old ? 'aus vorherigem Snapshot' : 'übersprungen'}: ${err.message}`);
    }
  }
  if (!zones.some((z) => z.values?.some((v) => v != null))) {
    throw new Error('keine Zone verfügbar – vorhandene Datei bleibt bestehen');
  }
  return { unit: 'EUR/MWh', zones };
}

const POWER_GROUPS = {
  solar: (n) => n === 'Solar',
  wind: (n) => n.startsWith('Wind'),
  hydroRiver: (n) => n === 'Hydro Run-of-River',
  hydroReservoir: (n) => n === 'Hydro water reservoir' || n === 'Hydro pumped storage',
  nuclear: (n) => n === 'Nuclear',
  fossil: (n) => n.startsWith('Fossil'),
  other: (n) => ['Biomass', 'Geothermal', 'Others', 'Waste'].includes(n),
  load: (n) => n === 'Load',
  residualLoad: (n) => n === 'Residual load',
  crossBorder: (n) => n === 'Cross border electricity trading',
};

/** Summiert die Produktionsarten von energy-charts zu wenigen Gruppen (MW). */
function groupPower(d) {
  const len = d.unix_seconds.length;
  const groups = {};
  for (const [key, match] of Object.entries(POWER_GROUPS)) {
    const types = d.production_types.filter((p) => match(p.name));
    if (!types.length) continue;
    groups[key] = Array.from({ length: len }, (_, i) => {
      let sum = null;
      for (const t of types) {
        const v = t.data[i];
        if (v != null) sum = (sum ?? 0) + v;
      }
      return sum;
    });
  }
  return groups;
}

/**
 * Erzeugungsmix: stündlich für die Schweiz (für Detail-Charts) und
 * als Tagesmittel pro Land (für den Europa-Vergleich).
 */
async function powerMix(now) {
  // auf Tagesgrenzen (UTC) ausgerichtet, damit die Tagesmittel ganze Tage abdecken
  const start = Math.floor((now - 30 * DAY) / DAY) * DAY;
  const end = Math.floor(now / DAY) * DAY + DAY;
  const result = { unit: 'MW', ch: null, countries: [] };

  for (const c of MIX_COUNTRIES) {
    try {
      const d = await ec(`/public_power?country=${c.country}&start=${isoDate(start)}&end=${isoDate(end)}`);
      const groups = groupPower(d);

      const daily = {};
      for (const [key, values] of Object.entries(groups)) {
        daily[key] = resample(d.unix_seconds, values, DAY, start, end, 0).values;
      }
      result.countries.push({ ...c, start, step: DAY, daily });

      if (c.country === 'ch') {
        const hourly = {};
        for (const [key, values] of Object.entries(groups)) {
          hourly[key] = resample(d.unix_seconds, values, HOUR, start, end, 0).values;
        }
        result.ch = { start, step: HOUR, series: hourly };
      }
    } catch (err) {
      const prev = await previous('power-mix.json');
      const old = prev?.countries?.find((x) => x.country === c.country);
      if (old) result.countries.push(old);
      if (c.country === 'ch' && prev?.ch) result.ch = prev.ch;
      log(`Erzeugungsmix ${c.country} ${old ? 'aus vorherigem Snapshot' : 'übersprungen'}: ${err.message}`);
    }
  }
  if (!result.countries.length) {
    throw new Error('kein Land verfügbar – vorhandene Datei bleibt bestehen');
  }
  return result;
}

/** EKZ-Tarife in 15-Minuten-Auflösung: letzte 30 Tage + morgen (falls publiziert). */
async function ekzTariffs(now) {
  const start = now - 30 * DAY;
  const end = now + 2 * DAY;
  const tariffs = [];
  for (const t of EKZ_TARIFFS) {
    const url =
      `${EKZ_API}?tariff_name=${t.name}` +
      `&start_timestamp=${encodeURIComponent(new Date(start * 1000).toISOString())}` +
      `&end_timestamp=${encodeURIComponent(new Date(end * 1000).toISOString())}`;
    const d = await getJson(url);
    const times = [];
    const values = [];
    let monthlyFee = null;
    for (const p of d.prices) {
      const component = p.integrated ?? p.electricity ?? [];
      const kwh = component.find((c) => c.unit === 'CHF_kWh');
      const fee = component.find((c) => c.unit === 'CHF_m');
      if (!kwh) continue;
      times.push(Date.parse(p.start_timestamp) / 1000);
      values.push(kwh.value * 100); // Rappen pro kWh
      if (fee) monthlyFee = fee.value;
    }
    const grid = resample(times, values, QUARTER, start, end, 3);
    // Raster nach dem letzten publizierten Wert abschneiden
    let last = grid.values.length - 1;
    while (last >= 0 && grid.values[last] == null) last--;
    grid.values.length = last + 1;
    tariffs.push({ ...t, unit: 'Rp./kWh', monthlyFee, ...grid });
  }
  return { tariffs };
}

// ---------------------------------------------------------------

async function runOnce() {
  // auf volle Stunde gerundet, damit alle Raster übereinanderliegen
  const now = Math.floor(Date.now() / 1000 / HOUR) * HOUR;
  const tasks = [
    ['ekz.json', ekzTariffs],
    ['ch-prices.json', chPrices],
    ['eu-prices.json', euPrices],
    ['power-mix.json', powerMix],
  ];
  const status = {};
  for (const [file, task] of tasks) {
    try {
      await writeJson(file, await task(now));
      status[file] = 'ok';
    } catch (err) {
      // alte Datei bleibt bestehen – lieber etwas ältere Daten als gar keine
      log(`FEHLER ${file}: ${err.message}`);
      status[file] = 'error';
    }
  }
  await writeJson('meta.json', {
    generatedAt: new Date().toISOString(),
    status,
    sources: [
      { name: 'Energy-Charts (Fraunhofer ISE)', url: 'https://energy-charts.info', license: 'CC BY 4.0' },
      { name: 'EKZ Tarif-API', url: 'https://api.tariffs.ekz.ch' },
    ],
  });
  return Object.values(status).every((s) => s === 'ok');
}

if (process.argv.includes('--loop')) {
  log(`Loop-Modus: alle ${INTERVAL_MINUTES} min → ${OUT_DIR}`);
  for (;;) {
    await runOnce().catch((err) => log(`Durchlauf fehlgeschlagen: ${err.message}`));
    await sleep(INTERVAL_MINUTES * 60_000);
  }
} else {
  const ok = await runOnce();
  process.exit(ok ? 0 : 1);
}
