from __future__ import annotations

import html
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

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

    summary = build_monthly_vehicle_summary(db, year_month=ym)
    if summary is None or summary.empty:
        return f'Нет данных по лимитам за {html.escape(ym)}.'

    data = summary.copy()
    data['plate'] = data['plate'].apply(normalize_plate)
    data = data[data['plate'] == normalized_plate].copy()

    if data.empty:
        return f'Не нашёл данные по госномеру <b>{html.escape(normalized_plate)}</b> за {html.escape(ym)}.'

    drivers = _latest_driver_registry()
    if not drivers.empty:
        data = data.merge(drivers, on='plate', how='left')
    else:
        for col in ['user_name', 'grade', 'position', 'directorate']:
            data[col] = ''

    row = data.iloc[0]
    status = str(row.get('status') or 'OK').upper()
    icon = STATUS_ICON.get(status, '⚪')

    return (
        f'{icon} <b>{html.escape(normalized_plate)}</b> — лимит топлива за {html.escape(ym)}\n\n'
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
