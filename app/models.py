from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class FuelEvent(Base):
    __tablename__ = 'fuel_events'
    __table_args__ = (
        UniqueConstraint('event_key', name='ux_fuel_events_event_key'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_dt: Mapped[datetime] = mapped_column(DateTime, index=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)
    fuel_type_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fuel_type_norm: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    liters: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    unit_price_try: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    amount_try: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    discount_try: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    station_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    station_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    station_city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    receipt_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_no: Mapped[str | None] = mapped_column(String(128), nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    odometer: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    sale_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class VehicleLimit(Base):
    __tablename__ = 'vehicle_limits'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # legacy field, keep for backward compatibility
    monthly_limit_liters: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    limit_mode: Mapped[str] = mapped_column(String(16), nullable=False, default='combined')
    unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    combined_limit_liters: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    turpak_limit_liters: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    cards_limit_liters: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AlertLog(Base):
    __tablename__ = 'alert_log'
    __table_args__ = (
        UniqueConstraint('year_month', 'plate', 'limit_bucket', 'threshold_pct', name='ux_alert_month_plate_bucket_threshold'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)
    limit_bucket: Mapped[str] = mapped_column(String(16), index=True)
    threshold_pct: Mapped[int] = mapped_column(Integer)
    usage_pct: Mapped[float] = mapped_column(Float)
    remaining_liters: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertState(Base):
    __tablename__ = 'alert_state'
    __table_args__ = (
        UniqueConstraint('year_month', 'plate', 'limit_bucket', name='ux_alert_state_month_plate_bucket'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)
    plate: Mapped[str] = mapped_column(String(32), index=True)
    limit_bucket: Mapped[str] = mapped_column(String(16), index=True)
    first_threshold_pct: Mapped[int] = mapped_column(Integer)
    max_threshold_pct: Mapped[int] = mapped_column(Integer)
    usage_pct: Mapped[float] = mapped_column(Float)
    remaining_liters: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    limit_liters: Mapped[float] = mapped_column(Float, default=0)
    consumed_liters: Mapped[float] = mapped_column(Float, default=0)
    total_amount_try: Mapped[float] = mapped_column(Float, default=0)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sources: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_event_dt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_triggered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ImportRun(Base):
    __tablename__ = 'import_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='running')
    rows_loaded: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
