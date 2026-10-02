"""Ortssuche über die swisstopo-Suche (api3.geo.admin.ch) – Adressen, PLZ, Ortschaften."""

from __future__ import annotations

import re

from .http import get_json

SEARCH = "https://api3.geo.admin.ch/rest/services/api/SearchServer"


def search(text: str, limit: int = 6) -> list[dict]:
    data = get_json(
        SEARCH,
        {"searchText": text, "type": "locations", "limit": limit, "sr": 4326,
         "origins": "address,zipcode,gg25"},
        ttl=86400,
    )
    results = []
    for r in data.get("results", []):
        attrs = r.get("attrs", {})
        label = re.sub(r"<[^>]+>", "", attrs.get("label", "")).strip()
        if "lat" in attrs and "lon" in attrs and label:
            results.append({"label": label, "lat": attrs["lat"], "lon": attrs["lon"]})
    return results
