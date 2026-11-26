// =============================================================
// AI-EMS – data fetcher
// Fetches electricity prices and generation data from energy-charts.info
// (Fraunhofer ISE) as well as the EKZ tariffs and stores them as
// compact JSON snapshots. The website only reads these files:
// energy-charts.info does not allow browser requests from other domains
// (CORS) and throttles many requests (HTTP 429).
//
//   node fetch-data.mjs            run once
//   node fetch-data.mjs --loop     repeat every INTERVAL_MINUTES
//
// Environment variables:
//   OUT_DIR           target folder (default: ../web/public/data)
//   INTERVAL_MINUTES  interval in loop mode (default: 180)
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

// Bidding zones for the European comparison (day-ahead prices)
const ZONES = [
  { code: 'CH', name: 'Switzerland' },
  { code: 'DE-LU', name: 'Germany' },
  { code: 'FR', name: 'France' },
  { code: 'AT', name: 'Austria' },
  { code: 'IT-North', name: 'Italy North' },
  { code: 'NL', name: 'Netherlands' },
  { code: 'BE', name: 'Belgium' },
  { code: 'PL', name: 'Poland' },
  { code: 'ES', name: 'Spain' },
  { code: 'DK1', name: 'Denmark West' },
  { code: 'NO2', name: 'Norway South' },
  { code: 'SE3', name: 'Sweden Central' },
  { code: 'CZ', name: 'Czechia' },
  { code: 'HU', name: 'Hungary' },
];

// Countries for the generation mix (public_power) – price via the zone
const MIX_COUNTRIES = [
  { country: 'ch', zone: 'CH', name: 'Switzerland' },
  { country: 'de', zone: 'DE-LU', name: 'Germany' },
  { country: 'fr', zone: 'FR', name: 'France' },
  { country: 'at', zone: 'AT', name: 'Austria' },
  { country: 'it', zone: 'IT-North', name: 'Italy' },
  { country: 'es', zone: 'ES', name: 'Spain' },
  { country: 'pl', zone: 'PL', name: 'Poland' },
  { country: 'nl', zone: 'NL', name: 'Netherlands' },
];

// EKZ products (household, grid level 7): integrated = energy + grid + levies
const EKZ_TARIFFS = [
  { key: 'dynamic', name: 'integrated_400D', label: 'EKZ dynamic energy + grid 400D' },
  { key: 'standard', name: 'integrated_400ST', label: 'EKZ renewable energy + grid 400ST' },
  { key: 'energyDynamic', name: 'electricity_dynamic', label: 'EKZ dynamic energy (energy only)' },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...args) => console.log(new Date().toISOString(), ...args);

let lastEcCall = 0;

/** GET with throttling (energy-charts) and retry on 429/5xx. */
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
        throw new Error(`HTTP ${res.status} for ${url}`);
      }
      log(`HTTP ${res.status} – attempt ${attempt}/5: ${url}`);
    } catch (err) {
      if (attempt === 5 || /HTTP 4\d\d/.test(err.message)) throw err;
      log(`Error – attempt ${attempt}/5: ${err.message}`);
    }
    await sleep(15_000 * attempt);
  }
  throw new Error(`No response after 5 attempts: ${url}`);
}

const ec = (path) => getJson(`${EC_API}${path}`, { throttleMs: 6000 });
const isoDate = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);
const round = (v, digits = 2) => (v == null || Number.isNaN(v) ? null : Number(v.toFixed(digits)));

/**
 * Averages a time series (unix seconds + values) onto a fixed grid.
 * Result: { start, step, values } – missing slots are null.
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

/** Previous version of a file (for partial outages of the source) – or null. */
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
  await rename(tmp, target); // atomic: nginx never serves a half-written file
  log(`written: ${name}`);
}

// ---------------------------------------------------------------
// Datasets
// ---------------------------------------------------------------

/** Swiss day-ahead price of the last 365 days (hourly). */
async function chPrices(now) {
  const start = now - 365 * DAY;
  const end = now + 2 * DAY; // incl. already published prices for tomorrow
  const d = await ec(`/price?bzn=CH&start=${isoDate(start)}&end=${isoDate(end)}`);
  return { unit: 'EUR/MWh', zone: 'CH', ...resample(d.unix_seconds, d.price, HOUR, start, end) };
}

/** Day-ahead prices of all zones for the last 30 days (hourly). */
async function euPrices(now) {
  const start = now - 30 * DAY;
  const end = now + 2 * DAY;
  const zones = [];
  for (const zone of ZONES) {
    try {
      const d = await ec(`/price?bzn=${zone.code}&start=${isoDate(start)}&end=${isoDate(end)}`);
      zones.push({ ...zone, ...resample(d.unix_seconds, d.price, HOUR, start, end) });
    } catch (err) {
      // Partial outage: keep the last known values of the zone
      const old = (await previous('eu-prices.json'))?.zones?.find((z) => z.code === zone.code);
      if (old) zones.push(old);
      log(`Zone ${zone.code} ${old ? 'from previous snapshot' : 'skipped'}: ${err.message}`);
    }
  }
  if (!zones.some((z) => z.values?.some((v) => v != null))) {
    throw new Error('no zone available – existing file is kept');
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

/** Sums the energy-charts production types into a few groups (MW). */
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
 * Generation mix: hourly for Switzerland (for detail charts) and
 * as daily mean per country (for the European comparison).
 */
async function powerMix(now) {
  // aligned to day boundaries (UTC) so that the daily means cover whole days
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
      log(`Generation mix ${c.country} ${old ? 'from previous snapshot' : 'skipped'}: ${err.message}`);
    }
  }
  if (!result.countries.length) {
    throw new Error('no country available – existing file is kept');
  }
  return result;
}

/** EKZ tariffs in 15 minute resolution: last 30 days + tomorrow (if published). */
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
      values.push(kwh.value * 100); // cents (Rappen) per kWh
      if (fee) monthlyFee = fee.value;
    }
    const grid = resample(times, values, QUARTER, start, end, 3);
    // Cut the grid after the last published value
    let last = grid.values.length - 1;
    while (last >= 0 && grid.values[last] == null) last--;
    grid.values.length = last + 1;
    tariffs.push({ ...t, unit: 'ct/kWh', monthlyFee, ...grid });
  }
  return { tariffs };
}

// ---------------------------------------------------------------

async function runOnce() {
  // rounded to the full hour so that all grids align
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
      // old file is kept – better slightly older data than none
      log(`ERROR ${file}: ${err.message}`);
      status[file] = 'error';
    }
  }
  await writeJson('meta.json', {
    generatedAt: new Date().toISOString(),
    status,
    sources: [
      { name: 'Energy-Charts (Fraunhofer ISE)', url: 'https://energy-charts.info', license: 'CC BY 4.0' },
      { name: 'EKZ tariff API', url: 'https://api.tariffs.ekz.ch' },
    ],
  });
  return Object.values(status).every((s) => s === 'ok');
}

if (process.argv.includes('--loop')) {
  log(`Loop mode: every ${INTERVAL_MINUTES} min → ${OUT_DIR}`);
  for (;;) {
    await runOnce().catch((err) => log(`Run failed: ${err.message}`));
    await sleep(INTERVAL_MINUTES * 60_000);
  }
} else {
  const ok = await runOnce();
  process.exit(ok ? 0 : 1);
}
