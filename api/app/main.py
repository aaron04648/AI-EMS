"""AI-EMS Backend – PV-Prognose, MPC-Batteriesteuerung und Backtest.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException, Query

from .control import simulate
from .schemas import SimulationRequest
from .sources import geo
from .sources.http import SourceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ai-ems")

app = FastAPI(
    title="AI-EMS API",
    version="1.0.0",
    description="PV-Prognose (PVGIS + MeteoSwiss), statistische Last- und Preisprognose, "
    "MPC-Batteriesteuerung nach Ruddick et al. (2024) mit EKZ-Tarifen.",
    root_path="/api",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/geocode")
def geocode(q: str = Query(..., min_length=2, max_length=80)) -> list[dict]:
    try:
        return geo.search(q)
    except SourceError as err:
        raise HTTPException(502, f"Ortssuche nicht erreichbar: {err}") from err


@app.post("/simulate")
def run_simulation(req: SimulationRequest) -> dict:
    started = time.perf_counter()
    try:
        result = simulate.run(req)
    except SourceError as err:
        log.warning("Datenquelle: %s", err)
        raise HTTPException(502, f"Eine Datenquelle ist nicht erreichbar: {err}") from err
    except RuntimeError as err:
        log.warning("Simulation: %s", err)
        raise HTTPException(422, str(err)) from err
    result["meta"] = {"duration_s": round(time.perf_counter() - started, 2)}
    return result
