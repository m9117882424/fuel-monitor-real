from __future__ import annotations

from typing import Any, Iterable

import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import FuelEvent, VehicleLimit
from ..utils import normalize_plate


def save_events(db: Session, events: pd.DataFrame) -> int:
    if events is None or events.empty:
        return 0

    inserted = 0
    for row in events.to_dict(orient='records'):
        event = FuelEvent(**row)
        db.add(event)
        try:
            db.commit()
            inserted += 1
        except IntegrityError:
            db.rollback()
    return inserted


def _is_empty(value: Any) -> bool:
    if value is None or value == '':
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _optional_float(value: Any, current: Any = None) -> float | None:
    """Return a clean float, or keep the current DB value when input is empty/bad."""
    if _is_empty(value):
        return current
    if isinstance(value, str):
        value = value.strip().replace('\xa0', '').replace(' ', '').replace(',', '.')
    try:
        return float(value)
    except (TypeError, ValueError):
        return current


def _bool_value(value: Any, current: bool = False) -> bool:
    if _is_empty(value):
        return bool(current)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on', 'да'}
    return bool(value)


def upsert_limits(db: Session, rows: Iterable[dict]) -> int:
    count = 0
    for row in rows:
        plate = normalize_plate(str(row.get('plate') or ''))
        if not plate:
            continue

        current = db.query(VehicleLimit).filter(VehicleLimit.plate == plate).one_or_none()
        if current is None:
            current = VehicleLimit(plate=plate)
            db.add(current)

        combined = row.get('combined_limit_liters')
        legacy_monthly = row.get('monthly_limit_liters')
        if _is_empty(combined) and not _is_empty(legacy_monthly):
            combined = legacy_monthly

        limit_mode = str(row.get('limit_mode') or current.limit_mode or 'combined').strip().lower()
        if limit_mode not in {'combined', 'separate'}:
            limit_mode = 'combined'

        current.limit_mode = limit_mode
        current.unlimited = _bool_value(row.get('unlimited'), current.unlimited)
        current.combined_limit_liters = _optional_float(combined, current.combined_limit_liters)
        current.turpak_limit_liters = _optional_float(row.get('turpak_limit_liters'), current.turpak_limit_liters)
        current.cards_limit_liters = _optional_float(row.get('cards_limit_liters'), current.cards_limit_liters)

        # Legacy mirror for older reports and queries.
        current.monthly_limit_liters = current.combined_limit_liters

        if 'group_name' in row:
            current.group_name = row.get('group_name')
        if 'note' in row:
            current.note = row.get('note')

        count += 1

    db.commit()
    return count
