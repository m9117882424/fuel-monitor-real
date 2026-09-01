from __future__ import annotations

import html
from typing import Any

import pandas as pd
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import FuelEvent, VehicleLimit
from ..services.driver_registry_service import load_driver_registry
from ..utils import current_year_month, normalize_plate


STATUS_ICON = {
    'OK': '🟢',
    'WARNING': '🟡',
    'CRITICAL': '🟠',
    'EXCEEDED': '🔴',
    'UNLIMITED': '♾️',
}
STATUS_ORDER = {'OK': 0, 'WARNING': 1, 'CRITICAL': 2, 'EXCEEDED': 3, 'UNLIMITED': 4}


def _safe_text(value: Any, default: str = '—') -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip()
    return text or default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_liters(value: Any) -> str:
    number = _safe_float(value)
    if abs(number) >= 100:
        text = f'{number:,.0f}'.replace(',', ' ')
    else:
        text = f'{number:,.1f}'.replace(',', ' ')
    return f'{text} л'


def _fmt_pct(value: Any) -> str:
    return f'{_safe_float(value):.0f}%'


def _overrun_from_remaining(value: Any) -> float:
    remaining = _safe_float(value)
    return abs(remaining) if remaining < 0 else 0.0


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


def _combine_status(*statuses: tuple[str, str]) -> tuple[str, str | None]:
    best = 'OK'
    best_bucket = None
    for bucket, status in statuses:
        if STATUS_ORDER.get(status, 0) > STATUS_ORDER.get(best, 0):
            best = status
            best_bucket = bucket
    return best, best_bucket


def _latest_driver_registry() -> pd.DataFrame:
    registry = load_driver_registry()
    if registry is None or registry.empty:
        return pd.DataFrame(columns=['plate', 'user_name', 'grade', 'position', 'directorate'])

    data = registry.copy()
    data['plate'] = data['plate'].apply(normalize_plate)

    sort_cols = [
        col for col in ['plate', 'roster_date', 'driver_file_name', 'driver_sheet_name']
        if col in data.columns
    ]
    if sort_cols:
        data = data.sort_values(sort_cols)

    data = data.drop_duplicates('plate', keep='last')

    for col in ['user_name', 'grade', 'position', 'directorate']:
        if col not in data.columns:
            data[col] = ''

    return data[['plate', 'user_name', 'grade', 'position', 'directorate']]


def _vehicle_limit_values(db: Session, normalized_plate: str) -> dict[str, Any] | None:
    limit = db.query(VehicleLimit).filter(VehicleLimit.plate == normalized_plate).first()
    if limit is None:
        return None

    return {
        'limit_mode': str(limit.limit_mode or 'combined').lower(),
        'unlimited': bool(limit.unlimited),
        'combined_limit_liters': (
            float(limit.combined_limit_liters)
            if limit.combined_limit_liters is not None
            else float(settings.default_monthly_limit_liters)
        ),
        'turpak_limit_liters': (
            float(limit.turpak_limit_liters)
            if limit.turpak_limit_liters is not None
            else float(settings.default_monthly_limit_liters)
        ),
        'cards_limit_liters': (
            float(limit.cards_limit_liters)
            if limit.cards_limit_liters is not None
            else float(settings.default_monthly_limit_liters)
        ),
    }


def _event_totals(db: Session, normalized_plate: str, year_month: str) -> dict[str, Any] | None:
    row = (
        db.query(
            func.count(FuelEvent.id).label('tx_count'),
            func.max(FuelEvent.event_dt).label('last_event_dt'),
            func.sum(case((FuelEvent.source == 'turpak', FuelEvent.liters), else_=0)).label('turpak_liters'),
            func.sum(case((FuelEvent.source == 'shell_excel', FuelEvent.liters), else_=0)).label('shell_liters'),
            func.sum(case((FuelEvent.source == 'petrol', FuelEvent.liters), else_=0)).label('petrol_liters'),
            func.sum(FuelEvent.liters).label('total_liters'),
            func.sum(FuelEvent.amount_try).label('total_amount_try'),
        )
        .filter(FuelEvent.year_month == year_month)
        .filter(FuelEvent.plate == normalized_plate)
        .one()
    )

    tx_count = int(row.tx_count or 0)
    if tx_count <= 0:
        return None

    source_rows = (
        db.query(FuelEvent.source)
        .filter(FuelEvent.year_month == year_month)
        .filter(FuelEvent.plate == normalized_plate)
        .distinct()
        .all()
    )

    shell_liters = _safe_float(row.shell_liters)
    petrol_liters = _safe_float(row.petrol_liters)
    turpak_liters = _safe_float(row.turpak_liters)

    return {
        'tx_count': tx_count,
        'sources': ', '.join(sorted(str(x[0]) for x in source_rows if x and x[0])),
        'last_event_dt': row.last_event_dt,
        'turpak_liters': round(turpak_liters, 3),
        'shell_liters': round(shell_liters, 3),
        'petrol_liters': round(petrol_liters, 3),
        'cards_liters': round(shell_liters + petrol_liters, 3),
        'total_liters': round(_safe_float(row.total_liters), 3),
        'total_amount_try': round(_safe_float(row.total_amount_try), 2),
    }


def _build_vehicle_row(db: Session, normalized_plate: str, year_month: str) -> tuple[pd.Series | None, bool]:
    """Fast one-vehicle summary for Telegram. Does not build a full monthly dashboard dataframe."""
    limit = _vehicle_limit_values(db, normalized_plate)
    totals = _event_totals(db, normalized_plate, year_month)

    if totals is None and limit is None:
        return None, False

    limit_only = totals is None
    if totals is None:
        totals = {
            'tx_count': 0,
            'sources': '',
            'last_event_dt': None,
            'turpak_liters': 0.0,
            'shell_liters': 0.0,
            'petrol_liters': 0.0,
            'cards_liters': 0.0,
            'total_liters': 0.0,
            'total_amount_try': 0.0,
        }

    if limit is None:
        limit = {
            'limit_mode': 'combined',
            'unlimited': False,
            'combined_limit_liters': float(settings.default_monthly_limit_liters),
            'turpak_limit_liters': float(settings.default_monthly_limit_liters),
            'cards_limit_liters': float(settings.default_monthly_limit_liters),
        }

    limit_mode = str(limit['limit_mode'] or 'combined').lower()
    unlimited = bool(limit['unlimited'])
    combined_limit = _safe_float(limit['combined_limit_liters'], float(settings.default_monthly_limit_liters))
    turpak_limit = _safe_float(limit['turpak_limit_liters'], float(settings.default_monthly_limit_liters))
    cards_limit = _safe_float(limit['cards_limit_liters'], float(settings.default_monthly_limit_liters))

    total_liters = _safe_float(totals['total_liters'])
    turpak_liters = _safe_float(totals['turpak_liters'])
    cards_liters = _safe_float(totals['cards_liters'])

    combined_remaining = None if unlimited else round(combined_limit - total_liters, 2)
    turpak_remaining = None if unlimited else round(turpak_limit - turpak_liters, 2)
    cards_remaining = None if unlimited else round(cards_limit - cards_liters, 2)

    combined_usage = 0.0 if unlimited or combined_limit == 0 else round(total_liters / combined_limit * 100, 2)
    turpak_usage = 0.0 if unlimited or turpak_limit == 0 else round(turpak_liters / turpak_limit * 100, 2)
    cards_usage = 0.0 if unlimited or cards_limit == 0 else round(cards_liters / cards_limit * 100, 2)

    if unlimited:
        status = 'UNLIMITED'
        worst_bucket = None
        display_usage = 0.0
        display_remaining = None
    elif limit_mode == 'separate':
        status, worst_bucket = _combine_status(
            ('turpak', _status_from_pct(turpak_usage)),
            ('cards', _status_from_pct(cards_usage)),
        )
        display_usage = max(turpak_usage, cards_usage)
        display_remaining = min(float(turpak_remaining), float(cards_remaining))
    else:
        status = _status_from_pct(combined_usage)
        worst_bucket = 'combined' if status != 'OK' else None
        display_usage = combined_usage
        display_remaining = combined_remaining

    row = {
        'year_month': year_month,
        'plate': normalized_plate,
        **totals,
        'limit_mode': limit_mode,
        'unlimited': unlimited,
        'combined_limit_liters': combined_limit,
        'turpak_limit_liters': turpak_limit,
        'cards_limit_liters': cards_limit,
        'combined_remaining_liters': combined_remaining,
        'turpak_remaining_liters': turpak_remaining,
        'cards_remaining_liters': cards_remaining,
        'combined_usage_pct': combined_usage,
        'turpak_usage_pct': turpak_usage,
        'cards_usage_pct': cards_usage,
        'display_usage_pct': display_usage,
        'display_remaining_liters': display_remaining,
        'status': status,
        'worst_bucket': worst_bucket,
    }

    return pd.Series(row), limit_only


def _limit_text(row: pd.Series) -> str:
    if bool(row.get('unlimited', False)):
        return 'без лимита'

    mode = str(row.get('limit_mode') or 'combined').lower()
    if mode == 'separate':
        return (
            f"Turpak {_fmt_liters(row.get('turpak_limit_liters'))} / "
            f"Shell+Petrol {_fmt_liters(row.get('cards_limit_liters'))}"
        )

    return _fmt_liters(row.get('combined_limit_liters'))


def _remaining_text(row: pd.Series) -> str:
    if bool(row.get('unlimited', False)):
        return '—'

    mode = str(row.get('limit_mode') or 'combined').lower()
    if mode == 'separate':
        return (
            f"Turpak {_fmt_liters(row.get('turpak_remaining_liters'))} / "
            f"Shell+Petrol {_fmt_liters(row.get('cards_remaining_liters'))}"
        )

    return _fmt_liters(row.get('combined_remaining_liters'))


def _overrun_text(row: pd.Series) -> str:
    if bool(row.get('unlimited', False)):
        return '0 л'

    mode = str(row.get('limit_mode') or 'combined').lower()
    if mode == 'separate':
        turpak_overrun = _overrun_from_remaining(row.get('turpak_remaining_liters'))
        cards_overrun = _overrun_from_remaining(row.get('cards_remaining_liters'))
        return f'Turpak {_fmt_liters(turpak_overrun)} / Shell+Petrol {_fmt_liters(cards_overrun)}'

    return _fmt_liters(_overrun_from_remaining(row.get('combined_remaining_liters')))


def _last_event_text(row: pd.Series) -> str:
    value = row.get('last_event_dt')
    if value is None:
        return '—'
    try:
        if pd.isna(value):
            return '—'
    except Exception:
        pass
    try:
        return pd.to_datetime(value).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return _safe_text(value)


def build_vehicle_limit_message(db: Session, plate: str, year_month: str | None = None) -> str:
    """Build Telegram HTML response for one vehicle fuel limit card."""
    ym = year_month or current_year_month()
    normalized_plate = normalize_plate(plate)

    if not normalized_plate:
        return 'Укажи госномер: <code>/limit 34ABC123</code>'

    row, limit_only = _build_vehicle_row(db, normalized_plate, ym)
    if row is None:
        return f'Не нашёл лимит или заправки по госномеру <b>{html.escape(normalized_plate)}</b> за {html.escape(ym)}.'

    data = pd.DataFrame([row.to_dict()])
    drivers = _latest_driver_registry()
    if not drivers.empty:
        data = data.merge(drivers, on='plate', how='left')
    else:
        for col in ['user_name', 'grade', 'position', 'directorate']:
            data[col] = ''

    row = data.iloc[0]
    status = str(row.get('status') or 'OK').upper()
    icon = STATUS_ICON.get(status, '⚪')
    note = '\n<i>За выбранный месяц заправок нет, показан установленный лимит.</i>\n' if limit_only else ''

    return (
        f'{icon} <b>{html.escape(normalized_plate)}</b> — лимит топлива за {html.escape(ym)}\n'
        f'{note}\n'
        f'Лимит: <b>{html.escape(_limit_text(row))}</b>\n'
        f'Перерасход: <b>{html.escape(_overrun_text(row))}</b>\n'
        f'Остаток: <b>{html.escape(_remaining_text(row))}</b>\n'
        f'Использование: <b>{html.escape(_fmt_pct(row.get("display_usage_pct")))}</b>\n\n'
        f'Заправлено Shell: <b>{html.escape(_fmt_liters(row.get("shell_liters")))}</b>\n'
        f'Заправлено Petrol: <b>{html.escape(_fmt_liters(row.get("petrol_liters")))}</b>\n'
        f'Заправлено Turpak: <b>{html.escape(_fmt_liters(row.get("turpak_liters")))}</b>\n\n'
        f'Водитель: <b>{html.escape(_safe_text(row.get("user_name")))}</b>\n'
        f'Грейд: {html.escape(_safe_text(row.get("grade")))}\n'
        f'Должность: {html.escape(_safe_text(row.get("position")))}\n'
        f'Дирекция: {html.escape(_safe_text(row.get("directorate")))}\n'
        f'Последняя заправка: {html.escape(_last_event_text(row))}'
    )
