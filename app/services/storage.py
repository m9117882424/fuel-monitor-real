from __future__ import annotations

from typing import Iterable

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


def upsert_limits(db: Session, rows: Iterable[dict]) -> int:
    count = 0
    for row in rows:
        plate = normalize_plate(row['plate'])
        current = db.query(VehicleLimit).filter(VehicleLimit.plate == plate).one_or_none()
        if current is None:
            current = VehicleLimit(plate=plate)
            db.add(current)

        combined = row.get('combined_limit_liters')
        legacy_monthly = row.get('monthly_limit_liters')
        if combined is None and legacy_monthly is not None:
            combined = legacy_monthly

        current.limit_mode = str(row.get('limit_mode') or current.limit_mode or 'combined').strip().lower()
        if current.limit_mode not in {'combined', 'separate'}:
            current.limit_mode = 'combined'

        current.unlimited = bool(row.get('unlimited', current.unlimited))
        current.combined_limit_liters = float(combined) if combined not in (None, '') else current.combined_limit_liters
        current.turpak_limit_liters = float(row.get('turpak_limit_liters')) if row.get('turpak_limit_liters') not in (None, '') else current.turpak_limit_liters
        current.cards_limit_liters = float(row.get('cards_limit_liters')) if row.get('cards_limit_liters') not in (None, '') else current.cards_limit_liters

        # legacy mirror
        current.monthly_limit_liters = current.combined_limit_liters
        current.group_name = row.get('group_name')
        current.note = row.get('note')
        count += 1
    db.commit()
    return count
