from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from ..config import settings
from ..models import FuelEvent, VehicleLimit
from ..utils import current_year_month, normalize_plate

STATUS_ORDER = {'OK': 0, 'WARNING': 1, 'CRITICAL': 2, 'EXCEEDED': 3, 'UNLIMITED': 4}


def _status_from_pct(pct: float | None) -> str:
    if pct is None:
        return 'OK'
    pct = float(pct)
    if pct >= 100:
        return 'EXCEEDED'
    if pct >= 90:
        return 'CRITICAL'
    if pct >= 80:
        return 'WARNING'
    return 'OK'


def _combine_status(*statuses: str) -> tuple[str, str | None]:
    best = 'OK'
    best_bucket = None
    for bucket, status in statuses:
        if STATUS_ORDER.get(status, 0) > STATUS_ORDER.get(best, 0):
            best = status
            best_bucket = bucket
    return best, best_bucket


def _empty_summary() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        'year_month', 'plate', 'tx_count', 'sources', 'last_event_dt',
        'turpak_liters', 'shell_liters', 'petrol_liters', 'cards_liters', 'total_liters', 'total_amount_try',
        'limit_mode', 'unlimited',
        'combined_limit_liters', 'combined_remaining_liters', 'combined_usage_pct',
        'turpak_limit_liters', 'turpak_remaining_liters', 'turpak_usage_pct',
        'cards_limit_liters', 'cards_remaining_liters', 'cards_usage_pct',
        'display_usage_pct', 'display_remaining_liters', 'status', 'worst_bucket',
    ])


def build_monthly_vehicle_summary(db: Session, year_month: str | None = None) -> pd.DataFrame:
    ym = year_month or current_year_month()
    events = db.query(FuelEvent).filter(FuelEvent.year_month == ym).all()
    if not events:
        return _empty_summary()

    rows = []
    for e in events:
        rows.append({
            'plate': normalize_plate(e.plate),
            'source': e.source,
            'liters': float(e.liters or 0),
            'amount_try': float(e.amount_try or 0),
            'event_dt': e.event_dt,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return _empty_summary()

    grouped_rows = []
    for plate, sub in df.groupby('plate'):
        turpak_liters = float(sub.loc[sub['source'] == 'turpak', 'liters'].sum())
        shell_liters = float(sub.loc[sub['source'] == 'shell_excel', 'liters'].sum())
        petrol_liters = float(sub.loc[sub['source'] == 'petrol', 'liters'].sum())
        cards_liters = shell_liters + petrol_liters
        total_liters = turpak_liters + cards_liters
        total_amount_try = float(sub['amount_try'].sum())
        sources = ', '.join(sorted(set(sub['source'].astype(str))))

        grouped_rows.append({
            'year_month': ym,
            'plate': plate,
            'tx_count': int(len(sub)),
            'sources': sources,
            'last_event_dt': sub['event_dt'].max(),
            'turpak_liters': round(turpak_liters, 3),
            'shell_liters': round(shell_liters, 3),
            'petrol_liters': round(petrol_liters, 3),
            'cards_liters': round(cards_liters, 3),
            'total_liters': round(total_liters, 3),
            'total_amount_try': round(total_amount_try, 2),
        })

    summary = pd.DataFrame(grouped_rows)

    limit_rows = db.query(VehicleLimit).all()
    if limit_rows:
        limits = pd.DataFrame([
            {
                'plate': normalize_plate(x.plate),
                'limit_mode': str(x.limit_mode or 'combined').lower(),
                'unlimited': bool(x.unlimited),
                'combined_limit_liters': float(x.combined_limit_liters) if x.combined_limit_liters is not None else None,
                'turpak_limit_liters': float(x.turpak_limit_liters) if x.turpak_limit_liters is not None else None,
                'cards_limit_liters': float(x.cards_limit_liters) if x.cards_limit_liters is not None else None,
            }
            for x in limit_rows
        ])
    else:
        limits = pd.DataFrame(columns=['plate', 'limit_mode', 'unlimited', 'combined_limit_liters', 'turpak_limit_liters', 'cards_limit_liters'])

    summary = summary.merge(limits, on='plate', how='left')

    summary['limit_mode'] = summary['limit_mode'].fillna('combined').astype(str).str.lower()
    summary['unlimited'] = summary['unlimited'].fillna(False).astype(bool)
    summary['combined_limit_liters'] = pd.to_numeric(summary['combined_limit_liters'], errors='coerce').fillna(float(settings.default_monthly_limit_liters))
    summary['turpak_limit_liters'] = pd.to_numeric(summary['turpak_limit_liters'], errors='coerce').fillna(float(settings.default_monthly_limit_liters))
    summary['cards_limit_liters'] = pd.to_numeric(summary['cards_limit_liters'], errors='coerce').fillna(float(settings.default_monthly_limit_liters))

    summary['combined_remaining_liters'] = (summary['combined_limit_liters'] - summary['total_liters']).round(2)
    summary['combined_usage_pct'] = ((summary['total_liters'] / summary['combined_limit_liters'].replace(0, pd.NA)) * 100).fillna(0).round(2)

    summary['turpak_remaining_liters'] = (summary['turpak_limit_liters'] - summary['turpak_liters']).round(2)
    summary['turpak_usage_pct'] = ((summary['turpak_liters'] / summary['turpak_limit_liters'].replace(0, pd.NA)) * 100).fillna(0).round(2)

    summary['cards_remaining_liters'] = (summary['cards_limit_liters'] - summary['cards_liters']).round(2)
    summary['cards_usage_pct'] = ((summary['cards_liters'] / summary['cards_limit_liters'].replace(0, pd.NA)) * 100).fillna(0).round(2)

    statuses = []
    display_usage = []
    display_remaining = []
    worst_buckets = []
    for _, row in summary.iterrows():
        if bool(row['unlimited']):
            statuses.append('UNLIMITED')
            display_usage.append(0.0)
            display_remaining.append(None)
            worst_buckets.append(None)
            continue

        if str(row['limit_mode']) == 'separate':
            turpak_status = _status_from_pct(row['turpak_usage_pct'])
            cards_status = _status_from_pct(row['cards_usage_pct'])
            status, bucket = _combine_status(('turpak', turpak_status), ('cards', cards_status))
            statuses.append(status)
            worst_buckets.append(bucket)
            display_usage.append(max(float(row['turpak_usage_pct']), float(row['cards_usage_pct'])))
            display_remaining.append(min(float(row['turpak_remaining_liters']), float(row['cards_remaining_liters'])))
        else:
            status = _status_from_pct(row['combined_usage_pct'])
            statuses.append(status)
            worst_buckets.append('combined' if status != 'OK' else None)
            display_usage.append(float(row['combined_usage_pct']))
            display_remaining.append(float(row['combined_remaining_liters']))

    summary['status'] = statuses
    summary['worst_bucket'] = worst_buckets
    summary['display_usage_pct'] = pd.Series(display_usage).round(2)
    summary['display_remaining_liters'] = pd.Series(display_remaining).round(2)

    return summary.sort_values(['display_usage_pct', 'total_liters'], ascending=[False, False]).reset_index(drop=True)


def fetch_events(db: Session, year_month: str | None = None, plate: str | None = None, limit: int = 500) -> pd.DataFrame:
    ym = year_month or current_year_month()
    q = db.query(FuelEvent).filter(FuelEvent.year_month == ym)
    if plate:
        q = q.filter(FuelEvent.plate == normalize_plate(plate))

    rows = q.order_by(FuelEvent.event_dt.desc()).limit(limit).all()
    return pd.DataFrame([
        {
            'source': r.source,
            'event_dt': r.event_dt,
            'year_month': r.year_month,
            'plate': normalize_plate(r.plate),
            'fuel_type_raw': r.fuel_type_raw,
            'fuel_type_norm': r.fuel_type_norm,
            'liters': float(r.liters or 0),
            'amount_try': float(r.amount_try or 0),
            'station_name': r.station_name,
            'group_name': r.group_name,
        }
        for r in rows
    ])
