"""Eingaben des Rechners (validiert mit pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Location(BaseModel):
    lat: float = Field(47.37, ge=45.7, le=47.9, description="Breitengrad (Schweiz)")
    lon: float = Field(8.54, ge=5.9, le=10.6, description="Längengrad (Schweiz)")
    label: str = "Zürich"


class PvConfig(BaseModel):
    kwp: float = Field(10, gt=0, le=100, description="Nennleistung in kWp")
    tilt: float = Field(30, ge=0, le=90, description="Neigung in Grad")
    azimuth: float = Field(0, ge=-180, le=180, description="Ausrichtung: 0 = Süd, -90 = Ost, 90 = West")
    loss: float = Field(14, ge=0, le=40, description="Systemverluste in %")


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(10, ge=0, le=200)
    power_kw: float = Field(5, ge=0, le=50)
    efficiency: float = Field(0.95, gt=0.5, le=1)
    soc_min: float = Field(0.1, ge=0, lt=1)
    soc_max: float = Field(1.0, gt=0, le=1)
    soc_start: float = Field(0.5, ge=0, le=1)
    cycle_cost: float = Field(2.0, ge=0, le=50, description="Verschleiss in Rp./kWh Durchsatz")

    @model_validator(mode="after")
    def check_soc(self) -> "BatteryConfig":
        if self.soc_min >= self.soc_max:
            raise ValueError("soc_min muss kleiner als soc_max sein")
        self.soc_start = min(max(self.soc_start, self.soc_min), self.soc_max)
        return self


class Household(BaseModel):
    annual_kwh: float = Field(4500, ge=500, le=50000)


class TariffConfig(BaseModel):
    feed_in: Literal["spot", "fixed"] = "spot"
    feed_in_fixed: float = Field(8.0, ge=-50, le=100, description="Rp./kWh bei fixem Einspeisetarif")


class GridConfig(BaseModel):
    limit_kw: float = Field(11, gt=0, le=100)
    allow_grid_charging: bool = True
    allow_battery_export: bool = True


class SimulationRequest(BaseModel):
    location: Location = Location()
    pv: PvConfig = PvConfig()
    battery: BatteryConfig = BatteryConfig()
    household: Household = Household()
    tariff: TariffConfig = TariffConfig()
    grid: GridConfig = GridConfig()
    days: int = Field(3, ge=1, le=7, description="Länge des Backtests in Tagen")
