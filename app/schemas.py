from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SyncResult(BaseModel):
    source: str
    rows_loaded: int
    detail: str | None = None


class SyncRunResponse(BaseModel):
    ok: bool
    results: list[SyncResult]
    report_path: str | None = None


class VehicleSummaryRow(BaseModel):
    plate: str
    total_liters: float
    total_amount_try: float
    last_event_dt: datetime | None = None
    tx_count: int = 0
    sources: str = ''
    turpak_liters: float = 0.0
    shell_liters: float = 0.0
    petrol_liters: float = 0.0
    cards_liters: float = 0.0
    limit_mode: str = 'combined'
    unlimited: bool = False
    combined_limit_liters: float | None = None
    combined_remaining_liters: float | None = None
    combined_usage_pct: float | None = None
    turpak_limit_liters: float | None = None
    turpak_remaining_liters: float | None = None
    turpak_usage_pct: float | None = None
    cards_limit_liters: float | None = None
    cards_remaining_liters: float | None = None
    cards_usage_pct: float | None = None
    display_usage_pct: float = 0.0
    display_remaining_liters: float = 0.0
    status: str = 'OK'
    worst_bucket: str | None = None


class LimitUpsert(BaseModel):
    plate: str
    limit_mode: str = 'combined'
    unlimited: bool = False
    combined_limit_liters: float | None = None
    turpak_limit_liters: float | None = None
    cards_limit_liters: float | None = None
    group_name: str | None = None
    note: str | None = None


class EventRow(BaseModel):
    source: str
    event_dt: datetime
    plate: str
    fuel_type_raw: str | None = None
    fuel_type_norm: str | None = None
    liters: float
    amount_try: float
    station_name: str | None = None
    group_name: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    app: str
    time: datetime


class DashboardStats(BaseModel):
    year_month: str
    vehicles: int
    total_liters: float
    total_amount_try: float
    warning_count: int
    exceeded_count: int
    top_plates: list[dict[str, Any]]
