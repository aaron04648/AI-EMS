import { Injectable, resource } from '@angular/core';
import { ChPrices, Ekz, EuPrices, Meta, PowerMix } from './data.models';

/**
 * Lädt die JSON-Snapshots, die der Fetcher (fetcher/fetch-data.mjs) ablegt.
 * Die Pfade sind relativ zum <base href> – damit funktioniert die Seite
 * unter / (Docker, Kubernetes) ebenso wie unter /AI-EMS/ (GitHub Pages).
 *
 * Jede Datei wird höchstens einmal geladen und von allen Seiten geteilt.
 */
@Injectable({ providedIn: 'root' })
export class DataService {
  private readonly cache = new Map<string, Promise<unknown>>();

  readonly meta = this.create<Meta>('meta.json');
  readonly ekz = this.create<Ekz>('ekz.json');
  readonly chPrices = this.create<ChPrices>('ch-prices.json');
  readonly euPrices = this.create<EuPrices>('eu-prices.json');
  readonly powerMix = this.create<PowerMix>('power-mix.json');

  private create<T>(file: string) {
    return resource({ loader: () => this.load<T>(file) });
  }

  private load<T>(file: string): Promise<T> {
    if (!this.cache.has(file)) {
      const request = fetch(`data/${file}`, { cache: 'no-cache' }).then((res) => {
        if (!res.ok) {
          throw new Error(`${file}: HTTP ${res.status}`);
        }
        return res.json() as Promise<T>;
      });
      // Fehlgeschlagene Anfragen nicht cachen, damit ein Reload es erneut versucht
      request.catch(() => this.cache.delete(file));
      this.cache.set(file, request);
    }
    return this.cache.get(file) as Promise<T>;
  }
}
