"""Statistical price forecast for times whose tariff has not been published yet.

Method: mean of the same quarter hour (Swiss local time) over the last 14
available days, separately for weekday and weekend. If there is nothing for a combination,
the mean of all days is used as a fallback.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Europe/Zurich"


def _key(index: pd.DatetimeIndex) -> pd.MultiIndex:
    local = index.tz_convert(TZ)
    slot = local.hour * 4 + local.minute // 15
    weekend = local.dayofweek >= 5
    return pd.MultiIndex.from_arrays([weekend, slot])


def profile(history: pd.Series, days: int = 14) -> tuple[pd.Series, pd.Series]:
    """(Profile per [weekend, quarter hour], profile per quarter hour) from the history."""
    recent = history[history.index >= history.index.max() - pd.Timedelta(days=days)]
    by_type = recent.groupby(_key(recent.index)).mean()
    local = recent.index.tz_convert(TZ)
    by_slot = recent.groupby(local.hour * 4 + local.minute // 15).mean()
    return by_type, by_slot


def complete(known: pd.Series, index: pd.DatetimeIndex, history: pd.Series) -> pd.Series:
    """Take over known prices, fill gaps with the statistical profile."""
    out = known.reindex(index)
    missing = out.isna()
    if missing.any():
        by_type, by_slot = profile(history)
        keys = _key(index[missing])
        values = by_type.reindex(keys).to_numpy()
        slots = np.array([k[1] for k in keys])
        fallback = by_slot.reindex(slots).to_numpy()
        values = np.where(np.isnan(values), fallback, values)
        out[missing] = values
    return out.fillna(history.mean())
