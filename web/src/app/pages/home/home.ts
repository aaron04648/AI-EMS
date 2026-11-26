import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DataService } from '../../core/data.service';
import { EkzTariff } from '../../core/data.models';
import { cheapestWindow } from '../../core/tariff';
import { fmt, formatDayTime, formatTime, localParts, mean, points, valueAt } from '../../core/series';
import { LineChart } from '../../shared/charts/line-chart';
import { RevealDirective } from '../../shared/reveal.directive';

const COLOR_DYNAMIC = 'var(--s1)';
const COLOR_STANDARD = 'var(--s2)';

@Component({
  selector: 'app-home',
  imports: [RouterLink, RevealDirective, LineChart],
  templateUrl: './home.html',
  styleUrl: './home.scss',
})
export class Home {
  protected readonly data = inject(DataService);
  protected readonly fmt = fmt;
  protected readonly formatTime = formatTime;
  protected readonly formatDayTime = formatDayTime;

  private readonly tariff = (key: EkzTariff['key']) =>
    computed(() => (this.data.ekz.hasValue() ? this.data.ekz.value().tariffs.find((t) => t.key === key) : undefined));

  private readonly dynamic = this.tariff('dynamic');
  private readonly standard = this.tariff('standard');

  /** Today (and tomorrow, once published) in Swiss time */
  protected readonly today = computed(() => {
    const dyn = this.dynamic();
    const std = this.standard();
    if (!dyn || !std) return null;

    const now = Math.floor(Date.now() / 1000 / 900) * 900;
    const todayKey = localParts(now).day;
    const all = points(dyn);
    const todaySlots = all.filter((p) => localParts(p.t).day === todayKey);
    if (!todaySlots.length) return null;

    const upcoming = all.filter((p) => p.t >= todaySlots[0].t);
    const hasTomorrow = upcoming.length > todaySlots.length;
    const future = upcoming.filter((p) => p.t >= now);
    const window = cheapestWindow(future.length >= 12 ? future : todaySlots, 12);

    const minSlot = todaySlots.reduce((a, b) => (b.v < a.v ? b : a));
    const maxSlot = todaySlots.reduce((a, b) => (b.v > a.v ? b : a));
    const x = upcoming.map((p) => p.t);

    return {
      now: valueAt(dyn, now),
      nowStandard: valueAt(std, now),
      avg: mean(todaySlots.map((p) => p.v)),
      min: minSlot,
      max: maxSlot,
      window,
      hasTomorrow,
      x,
      series: [
        { name: 'EKZ dynamic', color: COLOR_DYNAMIC, values: upcoming.map((p) => p.v) },
        { name: 'EKZ standard', color: COLOR_STANDARD, values: x.map((t) => valueAt(std, t)) },
      ],
      bands: window ? [{ from: window.from, to: window.to }] : [],
    };
  });

  /** Avg. day-ahead of the last 30 days for selected bidding zones */
  protected readonly europe = computed(() => {
    const eu = this.data.euPrices.hasValue() ? this.data.euPrices.value() : null;
    if (!eu) return [];
    const wanted = ['CH', 'DE-LU', 'FR', 'IT-North'];
    return eu.zones.filter((z) => wanted.includes(z.code))
      .sort((a, b) => wanted.indexOf(a.code) - wanted.indexOf(b.code))
      .map((z) => ({ name: z.name, avg: mean(points(z).map((p) => p.v)) }));
  });

  protected readonly drivers = [
    {
      icon: '☀︎',
      title: 'The sun pushes midday down',
      text: 'On sunny days millions of solar systems feed in at the same time. Supply exceeds demand – prices fall at midday, in summer even below zero.',
    },
    {
      icon: '◐',
      title: 'Evenings get expensive',
      text: 'When the sun sets and everyone cooks, washes and charges, demand rises. Now more expensive power plants have to step in – the price reaches its daily peak.',
    },
    {
      icon: '≈',
      title: 'Water as a battery',
      text: 'Swiss reservoir and pumped-storage plants produce when the price is high and pump when it is low. This smooths prices – but only within their capacity.',
    },
    {
      icon: '⇄',
      title: 'Europe sets the price',
      text: 'Switzerland is connected to Europe via more than 40 cross-border lines. Gas prices, wind lulls in Germany or nuclear plant outages in France pass straight through to the Swiss price.',
    },
  ];

  protected readonly steps = [
    {
      n: '1',
      title: 'Prices for tomorrow',
      text: 'EKZ publishes the dynamic tariff daily by 18:00 for every quarter hour of the following day.',
    },
    {
      n: '2',
      title: 'Plan consumption',
      text: 'An energy management system charges the car, heats the boiler or starts the heat pump in the cheapest hours.',
    },
    {
      n: '3',
      title: 'Cut costs',
      text: 'Being flexible means paying less per kWh – and at the same time relieving the grid at peak times.',
    },
  ];

  protected readonly tooltipTitle = (t: number) => formatDayTime(t);
  protected readonly xFormat = (t: number) => {
    const p = localParts(t);
    return `${String(p.hour).padStart(2, '0')}:00`;
  };
}
