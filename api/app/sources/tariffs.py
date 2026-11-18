"""Tariffs: EKZ (import, dynamic) and Swiss day-ahead price (feed-in).

- EKZ tariff API: https://api.tariffs.ekz.ch – integrated_400D = EKZ dynamic energy + grid 400D
  + ancillary services, electricity reserve, socialised costs, federal levies (15 minute grid, ct/kWh).
  The dynamic tariff is published daily by 18:00 for the following day.
- energy-charts.info (Fraunhofer ISE, CC BY 4.0): day-ahead price of the CH bidding zone (EUR/MWh).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .http import SourceError, get_json

log = logging.getLogger(__name__)

# Snapshot of the fetcher (ch-prices.json): in Docker/Kubernetes via the web service,
# locally directly from web/public/data. Spares the throttled energy-charts API.
SNAPSHOT_URL = os.environ.get("SNAPSHOT_URL")
SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "web" / "public" / "data"

EKZ = "https://api.tariffs.ekz.ch/v1/tariffs"
ENERGY_CHARTS = "https://api.energy-charts.info/price"
EUR_CHF = 0.94
TZ = "Europe/Zurich"


def ekz_tariff(name: str, start: datetime, end: datetime) -> pd.Series:
    """Quarter-hour tariff in ct/kWh (index: UTC, start of the quarter hour)."""
    data = get_json(
        EKZ,
        {"tariff_name": name,
         "start_timestamp": start.astimezone(timezone.utc).isoformat(),
         "end_timestamp": end.astimezone(timezone.utc).isoformat()},
        ttl=1800,
    )
    rows = []
    for p in data.get("prices", []):
        component = p.get("integrated") or p.get("electricity") or []
        kwh = next((c["value"] for c in component if c["unit"] == "CHF_kWh"), None)
        if kwh is not None:
            rows.append((pd.Timestamp(p["start_timestamp"]).tz_convert("UTC"), kwh * 100))
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(dict(rows)).sort_index()
    return s[~s.index.duplicated()]


def _snapshot() -> dict | None:
    try:
        if SNAPSHOT_URL:
            return get_json(f"{SNAPSHOT_URL.rstrip('/')}/ch-prices.json", ttl=600, retries=1)
        file = SNAPSHOT_DIR / "ch-prices.json"
        return json.loads(file.read_text()) if file.exists() else None
    except (SourceError, ValueError, OSError) as err:
        log.info("No price snapshot available: %s", err)
        return None


def spot_ch(start: datetime, end: datetime) -> pd.Series:
    """Day-ahead CH in ct/kWh, forward-filled to 15 minutes (index: UTC)."""
    snap = _snapshot()
    snap_series = None
    if snap:
        index = pd.to_datetime([snap["start"] + i * snap["step"] for i in range(len(snap["values"]))], unit="s", utc=True)
        s = pd.Series(snap["values"], index=index, dtype=float).dropna()
        snap_series = (s / 10 * EUR_CHF).resample("15min").ffill()
        wanted = s[(s.index >= pd.Timestamp(start)) & (s.index < pd.Timestamp(end) - pd.Timedelta(days=1))]
        expected = (pd.Timestamp(end) - pd.Timedelta(days=1) - pd.Timestamp(start)) / pd.Timedelta(hours=1)
        if len(wanted) >= 0.95 * expected:
            return snap_series

    try:
        data = get_json(
            ENERGY_CHARTS,
            {"bzn": "CH", "start": start.strftime("%Y-%m-%d"), "end": (end + timedelta(days=1)).strftime("%Y-%m-%d")},
            ttl=3600,
            retries=4,
        )
    except SourceError:
        if snap_series is None:
            raise
        # Better an older snapshot than no data at all – gaps are filled by the statistical price forecast
        log.warning("energy-charts unreachable – using snapshot until %s", snap_series.index.max())
        return snap_series
    s = pd.Series(data["price"], index=pd.to_datetime(data["unix_seconds"], unit="s", utc=True), dtype=float)
    s = s.dropna() / 10 * EUR_CHF
    return s.resample("15min").ffill()


def published_until(now_utc: pd.Timestamp) -> pd.Timestamp:
    """Until when the dynamic tariff is known at time `now` (EKZ publishes by 18:00)."""
    local = now_utc.tz_convert(TZ)
    end_of_day = (local + pd.Timedelta(days=1)).normalize()
    if local.hour >= 18:
        end_of_day += pd.Timedelta(days=1)
    return end_of_day.tz_convert("UTC")
