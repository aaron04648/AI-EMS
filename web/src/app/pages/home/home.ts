import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DataService } from '../../core/data.service';
import { EkzTariff } from '../../core/data.models';
import { cheapestWindow } from '../../core/tariff';
import { fmt, formatDayTime, formatTime, localParts, mean, points, valueAt } from '../../core/series';
import { LineChart } from '../../shared/charts/line-chart';
import { RevealDirective } from '../../shared/reveal.directive';

const COLOR_DYNAMIC = '#2a78d6';
const COLOR_STANDARD = '#eb6834';

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

  /** Heute (und morgen, sobald publiziert) in Schweizer Zeit */
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
        { name: 'EKZ Dynamisch', color: COLOR_DYNAMIC, values: upcoming.map((p) => p.v) },
        { name: 'EKZ Standard', color: COLOR_STANDARD, values: x.map((t) => valueAt(std, t)) },
      ],
      bands: window ? [{ from: window.from, to: window.to }] : [],
    };
  });

  /** Ø Day-Ahead der letzten 30 Tage für ausgewählte Gebotszonen */
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
      title: 'Sonne drückt den Mittag',
      text: 'An sonnigen Tagen speisen Millionen Solaranlagen gleichzeitig ein. Das Angebot übersteigt die Nachfrage – mittags fallen die Preise, im Sommer sogar unter null.',
    },
    {
      icon: '◐',
      title: 'Abends wird es teuer',
      text: 'Wenn die Sonne untergeht und alle kochen, waschen und laden, steigt die Nachfrage. Jetzt müssen teurere Kraftwerke einspringen – der Preis erreicht seine Tagesspitze.',
    },
    {
      icon: '≈',
      title: 'Wasser als Batterie',
      text: 'Schweizer Speicher- und Pumpspeicherkraftwerke produzieren, wenn der Preis hoch ist, und pumpen, wenn er tief ist. Das glättet die Preise – aber nur im Rahmen ihrer Kapazität.',
    },
    {
      icon: '⇄',
      title: 'Europa setzt den Preis',
      text: 'Die Schweiz ist über 40 Grenzleitungen mit Europa verbunden. Gaspreise, Windflauten in Deutschland oder AKW-Revisionen in Frankreich schlagen direkt auf den Schweizer Preis durch.',
    },
  ];

  protected readonly steps = [
    {
      n: '1',
      title: 'Preise für morgen',
      text: 'EKZ publiziert den dynamischen Tarif täglich bis 18 Uhr für jede Viertelstunde des Folgetags.',
    },
    {
      n: '2',
      title: 'Verbrauch planen',
      text: 'Ein Energiemanagement-System lädt das Auto, heizt den Boiler oder startet die Wärmepumpe in den günstigsten Stunden.',
    },
    {
      n: '3',
      title: 'Kosten senken',
      text: 'Wer flexibel ist, zahlt pro kWh weniger – und entlastet gleichzeitig das Netz zu Spitzenzeiten.',
    },
  ];

  protected readonly tooltipTitle = (t: number) => formatDayTime(t);
  protected readonly xFormat = (t: number) => {
    const p = localParts(t);
    return `${String(p.hour).padStart(2, '0')}:00`;
  };
}
