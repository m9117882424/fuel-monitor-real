from __future__ import annotations

import html
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..config import settings
from ..models import VehicleLimit
from ..services.driver_registry_service import load_driver_registry
from ..services.summary_service import build_monthly_vehicle_summary
from ..utils import current_year_month, normalize_plate


STATUS_ICON = {
    'OK': '🟢',
    'WARNING': '🟡',
    'CRITICAL': '🟠',
    'EXCEEDED': '🔴',
    'UNLIMITED': '♾️',
}


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


def _vehicle_limit_row(db: Session, normalized_plate: str) -> dict[str, Any] | None:
    limit = db.query(VehicleLimit).filter(VehicleLimit.plate == normalized_plate).first()
    if limit is None:
        return None

    limit_mode = str(limit.limit_mode or 'combined').lower()
    unlimited = bool(limit.unlimited)
    combined_limit = (
        float(limit.combined_limit_liters)
        if limit.combined_limit_liters is not None
        else float(settings.default_monthly_limit_liters)
    )
    turpak_limit = (
        float(limit.turpak_limit_liters)
        if limit.turpak_limit_liters is not None
        else float(settings.default_monthly_limit_liters)
    )
    cards_limit = (
        float(limit.cards_limit_liters)
        if limit.cards_limit_liters is not None
        else float(settings.default_monthly_limit_liters)
    )

    return {
        'plate': normalized_plate,
        'tx_count': 0,
        'sources': '',
        'last_event_dt': None,
        'turpak_liters': 0.0,
        'shell_liters': 0.0,
        'petrol_liters': 0.0,
        'cards_liters': 0.0,
        'total_liters': 0.0,
        'total_amount_try': 0.0,
        'limit_mode': limit_mode,
        'unlimited': unlimited,
        'combined_limit_liters': combined_limit,
        'turpak_limit_liters': turpak_limit,
        'cards_limit_liters': cards_limit,
        'combined_remaining_liters': None if unlimited else combined_limit,
        'turpak_remaining_liters': None if unlimited else turpak_limit,
        'cards_remaining_liters': None if unlimited else cards_limit,
        'combined_usage_pct': 0.0,
        'turpak_usage_pct': 0.0,
        'cards_usage_pct': 0.0,
        'display_usage_pct': 0.0,
        'display_remaining_liters': None if unlimited else (min(turpak_limit, cards_limit) if limit_mode == 'separate' else combined_limit),
        'status': 'UNLIMITED' if unlimited else 'OK',
        'worst_bucket': None,
    }


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


def _select_vehicle_row(db: Session, normalized_plate: str, year_month: str) -> tuple[pd.Series | None, bool]:
    """Return vehicle row and flag showing whether it was built from limit-only data."""
    summary = build_monthly_vehicle_summary(db, year_month=year_month)

    if summary is not None and not summary.empty:
        data = summary.copy()
        data['plate'] = data['plate'].apply(normalize_plate)
        data = data[data['plate'] == normalized_plate].copy()
        if not data.empty:
            return data.iloc[0], False

    limit_only = _vehicle_limit_row(db, normalized_plate)
    if limit_only is None:
        return None, False

    return pd.Series(limit_only), True


def build_vehicle_limit_message(db: Session, plate: str, year_month: str | None = None) -> str:
    """Build Telegram HTML response for one vehicle fuel limit card."""
    ym = year_month or current_year_month()
    normalized_plate = normalize_plate(plate)

    if not normalized_plate:
        return 'Укажи госномер: <code>/limit 34ABC123</code>'

    row, limit_only = _select_vehicle_row(db, normalized_plate, ym)
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
