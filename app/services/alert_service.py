from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AlertLog, AlertState
from .telegram_service import send_telegram_text


STATUS_ORDER = {'OK': 0, 'WARNING': 1, 'CRITICAL': 2, 'EXCEEDED': 3, 'UNLIMITED': 4}


def _normalize_thresholds() -> list[int]:
    values = []
    for value in settings.alert_threshold_values:
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(values))


def _threshold_bounds(usage_pct: float) -> tuple[int, int] | None:
    reached = [x for x in _normalize_thresholds() if usage_pct >= x]
    if not reached:
        return None
    return reached[0], reached[-1]


def _bucket_candidates(row: dict, year_month: str) -> list[dict]:
    if bool(row.get('unlimited')):
        return []

    buckets: list[tuple[str, float, float, str]] = []
    if str(row.get('limit_mode')) == 'separate':
        buckets.append(('turpak', float(row.get('turpak_usage_pct', 0) or 0), float(row.get('turpak_remaining_liters', 0) or 0), 'Turpak'))
        buckets.append(('cards', float(row.get('cards_usage_pct', 0) or 0), float(row.get('cards_remaining_liters', 0) or 0), 'Карты Shell и Petrol'))
    else:
        buckets.append(('combined', float(row.get('combined_usage_pct', 0) or 0), float(row.get('combined_remaining_liters', 0) or 0), 'Общий'))

    rows = []
    for bucket, usage, remaining, bucket_label in buckets:
        for threshold in _normalize_thresholds():
            if usage >= threshold:
                rows.append({
                    'year_month': year_month,
                    'plate': str(row.get('plate', '') or ''),
                    'limit_bucket': bucket,
                    'limit_bucket_label': bucket_label,
                    'threshold_pct': int(threshold),
                    'usage_pct': usage,
                    'remaining_liters': remaining,
                    'status': str(row.get('status', '') or ''),
                    'last_event_dt': row.get('last_event_dt'),
                    'consumed_liters': float(row.get('total_liters', 0) or 0) if bucket == 'combined' else float(row.get(f'{bucket}_liters', 0) or 0),
                    'limit_liters': float(row.get('combined_limit_liters', 0) or 0) if bucket == 'combined' else float(row.get(f'{bucket}_limit_liters', 0) or 0),
                })
    return rows


def build_alert_candidates(monthly_summary: pd.DataFrame, year_month: str) -> pd.DataFrame:
    if monthly_summary is None or monthly_summary.empty:
        return pd.DataFrame(columns=['year_month', 'plate', 'limit_bucket', 'limit_bucket_label', 'threshold_pct', 'usage_pct', 'remaining_liters', 'status', 'last_event_dt', 'consumed_liters', 'limit_liters'])

    rows: list[dict] = []
    for row in monthly_summary.to_dict(orient='records'):
        rows.extend(_bucket_candidates(row, year_month))

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values(['threshold_pct', 'usage_pct'], ascending=[False, False]).reset_index(drop=True)


def filter_unsent_alerts(db: Session, candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=['year_month', 'plate', 'limit_bucket', 'threshold_pct', 'usage_pct', 'remaining_liters', 'status'])

    unsent: list[dict] = []
    for row in candidates.to_dict(orient='records'):
        exists = (
            db.query(AlertLog)
            .filter(
                AlertLog.year_month == row['year_month'],
                AlertLog.plate == row['plate'],
                AlertLog.limit_bucket == row['limit_bucket'],
                AlertLog.threshold_pct == row['threshold_pct'],
            )
            .one_or_none()
        )
        if exists is None:
            unsent.append(row)

    return pd.DataFrame(unsent)


def dispatch_alerts(db: Session, alerts: pd.DataFrame) -> int:
    if alerts is None or alerts.empty:
        return 0

    sent = 0
    for row in alerts.to_dict(orient='records'):
        text = (
            f"⛽ Лимит топлива: {row['plate']}\n"
            f"Контур лимита: {row['limit_bucket_label']}\n"
            f"Месяц: {row['year_month']}\n"
            f"Порог: {row['threshold_pct']}%\n"
            f"Текущий расход: {float(row['usage_pct']):.2f}%\n"
            f"Остаток: {float(row['remaining_liters']):.2f} л\n"
            f"Статус: {row['status']}"
        )

        ok = send_telegram_text(text)
        if not ok and settings.telegram_enabled:
            continue

        log = AlertLog(
            year_month=row['year_month'],
            plate=row['plate'],
            limit_bucket=row['limit_bucket'],
            threshold_pct=int(row['threshold_pct']),
            usage_pct=float(row['usage_pct']),
            remaining_liters=float(row['remaining_liters']),
            status=row['status'],
        )
        db.add(log)
        try:
            db.commit()
            sent += 1
        except IntegrityError:
            db.rollback()

    return sent


def refresh_alert_state(db: Session, monthly_summary: pd.DataFrame, year_month: str) -> pd.DataFrame:
    empty_columns = [
        'year_month', 'plate', 'limit_bucket', 'first_threshold_pct', 'max_threshold_pct', 'usage_pct',
        'remaining_liters', 'status', 'limit_liters', 'consumed_liters', 'total_amount_try', 'mode', 'unlimited',
        'sources', 'last_event_dt', 'first_triggered_at', 'last_seen_at',
    ]

    if monthly_summary is None or monthly_summary.empty:
        return pd.DataFrame(columns=empty_columns)

    current_keys: set[tuple[str, str, str]] = set()
    now_dt = datetime.utcnow()

    for row in monthly_summary.to_dict(orient='records'):
        if bool(row.get('unlimited')):
            continue

        bucket_configs = []
        if str(row.get('limit_mode')) == 'separate':
            bucket_configs.extend([
                ('turpak', float(row.get('turpak_usage_pct', 0) or 0), float(row.get('turpak_remaining_liters', 0) or 0), float(row.get('turpak_limit_liters', 0) or 0), float(row.get('turpak_liters', 0) or 0)),
                ('cards', float(row.get('cards_usage_pct', 0) or 0), float(row.get('cards_remaining_liters', 0) or 0), float(row.get('cards_limit_liters', 0) or 0), float(row.get('cards_liters', 0) or 0)),
            ])
        else:
            bucket_configs.append(('combined', float(row.get('combined_usage_pct', 0) or 0), float(row.get('combined_remaining_liters', 0) or 0), float(row.get('combined_limit_liters', 0) or 0), float(row.get('total_liters', 0) or 0)))

        for bucket, usage_pct, remaining_liters, limit_liters, consumed_liters in bucket_configs:
            bounds = _threshold_bounds(usage_pct)
            if bounds is None:
                continue

            first_threshold_pct, max_threshold_pct = bounds
            plate = str(row.get('plate', '') or '').strip()
            if not plate:
                continue

            current_keys.add((year_month, plate, bucket))
            state = (
                db.query(AlertState)
                .filter(AlertState.year_month == year_month, AlertState.plate == plate, AlertState.limit_bucket == bucket)
                .one_or_none()
            )
            if state is None:
                state = AlertState(
                    year_month=year_month,
                    plate=plate,
                    limit_bucket=bucket,
                    first_threshold_pct=first_threshold_pct,
                    max_threshold_pct=max_threshold_pct,
                    usage_pct=usage_pct,
                    remaining_liters=remaining_liters,
                    status=str(row.get('status', '') or ''),
                    limit_liters=limit_liters,
                    consumed_liters=consumed_liters,
                    total_amount_try=float(row.get('total_amount_try', 0) or 0),
                    mode=str(row.get('limit_mode', '') or ''),
                    unlimited=bool(row.get('unlimited')),
                    sources=str(row.get('sources', '') or ''),
                    last_event_dt=row.get('last_event_dt'),
                    first_triggered_at=now_dt,
                    last_seen_at=now_dt,
                )
                db.add(state)
            else:
                state.max_threshold_pct = max(int(state.max_threshold_pct), int(max_threshold_pct))
                state.usage_pct = usage_pct
                state.remaining_liters = remaining_liters
                state.status = str(row.get('status', '') or '')
                state.limit_liters = limit_liters
                state.consumed_liters = consumed_liters
                state.total_amount_try = float(row.get('total_amount_try', 0) or 0)
                state.mode = str(row.get('limit_mode', '') or '')
                state.unlimited = bool(row.get('unlimited'))
                state.sources = str(row.get('sources', '') or '')
                state.last_event_dt = row.get('last_event_dt')
                state.last_seen_at = now_dt

    stale_rows = db.query(AlertState).filter(AlertState.year_month == year_month).all()
    for stale in stale_rows:
        if (stale.year_month, stale.plate, stale.limit_bucket) not in current_keys:
            db.delete(stale)

    db.commit()

    rows = (
        db.query(AlertState)
        .filter(AlertState.year_month == year_month)
        .order_by(AlertState.max_threshold_pct.desc(), AlertState.usage_pct.desc(), AlertState.consumed_liters.desc(), AlertState.plate.asc())
        .all()
    )

    if not rows:
        return pd.DataFrame(columns=empty_columns)

    return pd.DataFrame([
        {
            'year_month': r.year_month,
            'plate': r.plate,
            'limit_bucket': r.limit_bucket,
            'first_threshold_pct': r.first_threshold_pct,
            'max_threshold_pct': r.max_threshold_pct,
            'usage_pct': r.usage_pct,
            'remaining_liters': r.remaining_liters,
            'status': r.status,
            'limit_liters': r.limit_liters,
            'consumed_liters': r.consumed_liters,
            'total_amount_try': r.total_amount_try,
            'mode': r.mode,
            'unlimited': r.unlimited,
            'sources': r.sources,
            'last_event_dt': r.last_event_dt,
            'first_triggered_at': r.first_triggered_at,
            'last_seen_at': r.last_seen_at,
        }
        for r in rows
    ])
