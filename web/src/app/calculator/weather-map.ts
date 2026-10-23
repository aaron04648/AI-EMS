import {
  Component,
  ElementRef,
  OnDestroy,
  afterNextRender,
  computed,
  effect,
  input,
  signal,
  viewChild,
} from '@angular/core';
import * as L from 'leaflet';
import { fmt, formatDayTime } from '../core/series';
import { Help } from '../shared/help';
import { Place } from './calculator.models';

interface WeatherGrid {
  lats: number[];
  lons: number[];
  times: number[];
  now: number;
  model: string;
  cloud: (number | null)[][][];
  precip: (number | null)[][][];
  radiation: (number | null)[][][];
}

type Layer = 'cloud' | 'radiation' | 'precip';
type Rgba = [number, number, number, number];

/** Farbskalen: [Wert, r, g, b, Deckkraft] – zwischen den Stützstellen linear interpoliert */
const SCALES: Record<Layer, { stops: number[][]; unit: string; label: string; ticks: number[] }> = {
  cloud: {
    // Wie ein Satellitenbild: Wolken hell und deckend über abgedunkelter Karte
    label: 'Bewölkung',
    unit: '%',
    ticks: [0, 50, 100],
    stops: [
      [0, 255, 255, 255, 0],
      [8, 236, 240, 246, 0.2],
      [30, 241, 244, 248, 0.55],
      [60, 248, 249, 251, 0.8],
      [85, 255, 255, 255, 0.92],
      [100, 255, 255, 255, 0.97],
    ],
  },
  radiation: {
    label: 'Sonneneinstrahlung',
    unit: 'W/m²',
    ticks: [0, 400, 800],
    stops: [
      [0, 255, 224, 138, 0],
      [30, 255, 224, 138, 0.15],
      [250, 255, 196, 61, 0.45],
      [550, 248, 150, 30, 0.6],
      [850, 230, 57, 70, 0.72],
    ],
  },
  precip: {
    label: 'Niederschlag',
    unit: 'mm/h',
    ticks: [0.1, 2, 10],
    stops: [
      [0.05, 158, 202, 225, 0],
      [0.1, 158, 202, 225, 0.55],
      [1, 49, 130, 189, 0.7],
      [3, 63, 55, 201, 0.8],
      [6, 114, 9, 183, 0.85],
      [12, 247, 37, 133, 0.9],
    ],
  },
};

function colorOf(layer: Layer, v: number): Rgba {
  const stops = SCALES[layer].stops;
  if (v <= stops[0][0]) return [stops[0][1], stops[0][2], stops[0][3], stops[0][4]];
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const a = stops[i - 1];
      const b = stops[i];
      const f = (v - a[0]) / (b[0] - a[0]);
      return [a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), a[3] + f * (b[3] - a[3]), a[4] + f * (b[4] - a[4])];
    }
  }
  const last = stops[stops.length - 1];
  return [last[1], last[2], last[3], last[4]];
}

/** Bilineare Interpolation eines Gitterwerts an (Zeile, Spalte) in Gitterkoordinaten */
function sample(field: (number | null)[][], r: number, c: number): number {
  const r0 = Math.max(0, Math.min(field.length - 1, Math.floor(r)));
  const c0 = Math.max(0, Math.min(field[0].length - 1, Math.floor(c)));
  const r1 = Math.min(field.length - 1, r0 + 1);
  const c1 = Math.min(field[0].length - 1, c0 + 1);
  const fr = Math.max(0, Math.min(1, r - r0));
  const fc = Math.max(0, Math.min(1, c - c0));
  const v = (x: number | null) => x ?? 0;
  const top = v(field[r0][c0]) * (1 - fc) + v(field[r0][c1]) * fc;
  const bottom = v(field[r1][c0]) * (1 - fc) + v(field[r1][c1]) * fc;
  return top * (1 - fr) + bottom * fr;
}

const TILES = 'https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg';
const UPSAMPLE = 8;

@Component({
  selector: 'app-weather-map',
  imports: [Help],
  template: `
    <div class="wm" [class.wm--clouds]="layer() === 'cloud'">
      <div #map class="wm__map" role="img" [attr.aria-label]="'Wetterkarte: ' + scale().label"></div>

      <!-- Ebenen -->
      <div class="wm__layers" role="group" aria-label="Kartenebene">
        @for (l of layers; track l.key) {
          <button type="button" [class.is-active]="layer() === l.key" (click)="layer.set(l.key)">{{ l.label }}</button>
        }
      </div>

      <!-- Werte an der Adresse -->
      @if (here(); as h) {
        <div class="wm__here">
          <span class="wm__here-title">
            {{ place().label }}
            <app-help align="right">
              Modellwerte von MeteoSwiss (ICON-CH2, 2 km) an Ihrer Adresse für die gewählte Stunde.
              PV: Prognose Ihrer Anlage aus dem PV-Modell (nur für Stunden im Fahrplan).
            </app-help>
          </span>
          <dl>
            <div><dt>Wolken</dt><dd>{{ fmt(h.cloud) }} %</dd></div>
            <div><dt>Sonne</dt><dd>{{ fmt(h.radiation) }} W/m²</dd></div>
            <div><dt>Regen</dt><dd>{{ fmt(h.precip, 1) }} mm/h</dd></div>
            @if (h.pv !== null) {
              <div class="wm__pv"><dt>PV</dt><dd>{{ fmt(h.pv, 1) }} kW</dd></div>
            }
          </dl>
        </div>
      }

      <!-- Legende -->
      <div class="wm__legend">
        <span class="wm__legend-label">{{ scale().label }} ({{ scale().unit }})</span>
        <span class="wm__legend-bar" [style.background]="gradient()"></span>
        <span class="wm__legend-ticks">
          @for (t of scale().ticks; track t) {
            <span>{{ t }}</span>
          }
        </span>
      </div>

      @if (error()) {
        <div class="wm__state">{{ error() }}</div>
      } @else if (!grid()) {
        <div class="wm__state">Wetterdaten werden geladen …</div>
      }
    </div>

    <!-- Zeitachse -->
    @if (grid(); as g) {
      <div class="wm__time">
        <button type="button" class="wm__play" [attr.aria-label]="playing() ? 'Pause' : 'Abspielen'" (click)="toggle()">
          @if (playing()) {
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
          } @else {
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 2.5v11l9.5-5.5z" /></svg>
          }
        </button>
        <input
          type="range"
          min="0"
          [max]="g.times.length - 1"
          step="1"
          [value]="frame()"
          aria-label="Zeitpunkt"
          (input)="seek($event)"
        />
        <span class="wm__stamp">
          <strong>{{ formatDayTime(g.times[frame()]) }}</strong>
          <span class="wm__badge" [class.wm__badge--past]="g.times[frame()] < g.now">
            {{ g.times[frame()] < g.now ? 'Analyse' : g.times[frame()] === g.now ? 'Jetzt' : 'Prognose' }}
          </span>
        </span>
      </div>
    }
  `,
  styleUrl: './weather-map.scss',
})
export class WeatherMap implements OnDestroy {
  readonly place = input.required<Place>();
  /** PV-Prognose der Anlage (kW, 15-min-Raster) – optional, für die Anzeige an der Adresse */
  readonly pv = input<{ start: number; step: number; values: number[] } | null>(null);

  protected readonly fmt = fmt;
  protected readonly formatDayTime = formatDayTime;
  protected readonly layers: { key: Layer; label: string }[] = [
    { key: 'cloud', label: 'Wolken' },
    { key: 'radiation', label: 'Sonne' },
    { key: 'precip', label: 'Niederschlag' },
  ];

  protected readonly grid = signal<WeatherGrid | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly layer = signal<Layer>('cloud');
  protected readonly frame = signal(0);
  protected readonly playing = signal(false);

  protected readonly scale = computed(() => SCALES[this.layer()]);
  protected readonly gradient = computed(() => {
    const stops = this.scale().stops;
    const max = stops[stops.length - 1][0];
    return `linear-gradient(90deg, ${stops
      .map((s) => `rgba(${s[1]},${s[2]},${s[3]},${s[4]}) ${(s[0] / max) * 100}%`)
      .join(', ')})`;
  });

  /** Werte an der Adresse (bilinear aus dem Gitter) */
  protected readonly here = computed(() => {
    const g = this.grid();
    if (!g) return null;
    const p = this.place();
    const r = (p.lat - g.lats[0]) / (g.lats[1] - g.lats[0]);
    const c = (p.lon - g.lons[0]) / (g.lons[1] - g.lons[0]);
    const i = this.frame();
    const t = g.times[i];
    let pv: number | null = null;
    const series = this.pv();
    if (series) {
      // Stundenmittel der Viertelstunden in der Stunde vor dem Zeitstempel (soweit im Fahrplan)
      const values = series.values.filter((_, k) => {
        const qt = series.start + k * series.step;
        return qt >= t - 3600 && qt < t;
      });
      if (values.length) pv = values.reduce((a, b) => a + b, 0) / values.length;
    }
    return {
      cloud: sample(g.cloud[i], r, c),
      radiation: sample(g.radiation[i], r, c),
      precip: sample(g.precip[i], r, c),
      pv,
    };
  });

  private readonly mapEl = viewChild.required<ElementRef<HTMLDivElement>>('map');
  private map?: L.Map;
  private overlay?: L.ImageOverlay;
  private marker?: L.CircleMarker;
  private timer?: ReturnType<typeof setInterval>;
  private readonly canvas = typeof document !== 'undefined' ? document.createElement('canvas') : null;

  constructor() {
    afterNextRender(() => {
      this.initMap();
      void this.load();
    });

    // Overlay neu zeichnen, wenn Daten, Ebene oder Zeitpunkt wechseln
    effect(() => {
      const g = this.grid();
      const layer = this.layer();
      const frame = this.frame();
      if (g && this.map) this.draw(g, layer, frame);
    });

    // Marker folgt der Adresse
    effect(() => {
      const p = this.place();
      if (this.marker) {
        this.marker.setLatLng([p.lat, p.lon]).setTooltipContent(p.label);
      }
    });
  }

  ngOnDestroy(): void {
    clearInterval(this.timer);
    this.map?.remove();
  }

  private initMap(): void {
    const p = this.place();
    this.map = L.map(this.mapEl().nativeElement, {
      center: [46.8, 8.2],
      zoom: 8,
      minZoom: 7,
      maxZoom: 12,
      maxBounds: [
        [45.3, 5.0],
        [48.3, 11.4],
      ],
      zoomControl: true,
      attributionControl: true,
    });
    L.tileLayer(TILES, {
      attribution: '© <a href="https://www.swisstopo.admin.ch" target="_blank" rel="noopener">swisstopo</a> · Wetter: MeteoSwiss ICON-CH2',
      maxZoom: 12,
    }).addTo(this.map);
    this.marker = L.circleMarker([p.lat, p.lon], {
      radius: 7,
      color: '#ffffff',
      weight: 3,
      fillColor: '#0071e3',
      fillOpacity: 1,
    })
      .bindTooltip(p.label, { direction: 'top', offset: [0, -8] })
      .addTo(this.map);
    // Flex-Layout: Grösse nach dem ersten Layout neu bestimmen
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private async load(): Promise<void> {
    try {
      const res = await fetch('api/weather-map');
      if (!res.ok) throw new Error(`Wetterdaten nicht verfügbar (HTTP ${res.status})`);
      const g: WeatherGrid = await res.json();
      const nowIdx = Math.max(0, g.times.indexOf(g.now));
      this.frame.set(nowIdx);
      this.grid.set(g);
      this.map?.fitBounds(
        [
          [g.lats[0], g.lons[0]],
          [g.lats[g.lats.length - 1], g.lons[g.lons.length - 1]],
        ],
        { padding: [0, 0] },
      );
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  private draw(g: WeatherGrid, layer: Layer, frame: number): void {
    if (!this.canvas || !this.map) return;
    const field = g[layer][frame];
    const rows = g.lats.length;
    const cols = g.lons.length;
    const w = (cols - 1) * UPSAMPLE + 1;
    const h = (rows - 1) * UPSAMPLE + 1;
    this.canvas.width = w;
    this.canvas.height = h;
    const ctx = this.canvas.getContext('2d')!;
    const img = ctx.createImageData(w, h);
    // Ränder weich ausblenden, damit das Gitter nicht als Rechteck sichtbar ist
    const fade = UPSAMPLE * 1.5;
    for (let y = 0; y < h; y++) {
      // Canvas-Zeile 0 = Norden; Gitterzeile 0 = Süden
      const r = (h - 1 - y) / UPSAMPLE;
      const fy = Math.min(1, y / fade, (h - 1 - y) / fade);
      for (let x = 0; x < w; x++) {
        const edge = Math.min(fy, x / fade, (w - 1 - x) / fade);
        const [cr, cg, cb, a] = colorOf(layer, sample(field, r, x / UPSAMPLE));
        const ca = a * Math.max(0, edge);
        const o = (y * w + x) * 4;
        img.data[o] = cr;
        img.data[o + 1] = cg;
        img.data[o + 2] = cb;
        img.data[o + 3] = ca * 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    const bounds = L.latLngBounds([g.lats[0], g.lons[0]], [g.lats[rows - 1], g.lons[cols - 1]]);
    const url = this.canvas.toDataURL();
    if (this.overlay) {
      this.overlay.setUrl(url);
    } else {
      this.overlay = L.imageOverlay(url, bounds, { opacity: 1, className: 'wm__overlay' }).addTo(this.map);
    }
  }

  protected seek(event: Event): void {
    this.frame.set(Number((event.target as HTMLInputElement).value));
  }

  protected toggle(): void {
    if (this.playing()) {
      clearInterval(this.timer);
      this.playing.set(false);
      return;
    }
    this.playing.set(true);
    this.timer = setInterval(() => {
      const g = this.grid();
      if (!g) return;
      this.frame.update((f) => (f + 1) % g.times.length);
    }, 600);
  }
}
