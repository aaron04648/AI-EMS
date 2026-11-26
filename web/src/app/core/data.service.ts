import { Injectable, resource } from '@angular/core';
import { ChPrices, Ekz, EuPrices, Meta, PowerMix } from './data.models';

/**
 * Loads the JSON snapshots stored by the fetcher (fetcher/fetch-data.mjs).
 * The paths are relative to <base href> – so the site works
 * under / (Docker, Kubernetes) as well as under /AI-EMS/ (GitHub Pages).
 *
 * Each file is loaded at most once and shared by all pages.
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
      // Do not cache failed requests so that a reload tries again
      request.catch(() => this.cache.delete(file));
      this.cache.set(file, request);
    }
    return this.cache.get(file) as Promise<T>;
  }
}
