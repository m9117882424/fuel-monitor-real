from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd

from .config import settings


COMMON_EVENT_COLUMNS = [
    'source',
    'external_id',
    'event_key',
    'event_dt',
    'year_month',
    'plate',
    'fuel_type_raw',
    'fuel_type_norm',
    'liters',
    'unit_price_try',
    'amount_try',
    'discount_try',
    'station_code',
    'station_name',
    'station_city',
    'receipt_no',
    'card_no',
    'card_type',
    'group_name',
    'odometer',
    'sale_type',
    'department_code',
]


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def month_start_local(dt: datetime | None = None) -> datetime:
    base = dt or now_local()
    return base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def current_year_month() -> str:
    return now_local().strftime('%Y-%m')


def build_date_window(days_back: int) -> tuple[str, str]:
    end_dt = now_local().replace(microsecond=0)
    start_dt = (end_dt - timedelta(days=days_back)).replace(microsecond=0)
    return start_dt.isoformat(), end_dt.isoformat()


def build_petrol_date_window(days_back: int) -> tuple[str, str]:
    end_dt = now_local().replace(microsecond=0)
    start_dt = (end_dt - timedelta(days=days_back)).replace(microsecond=0)
    return start_dt.strftime('%Y-%m-%dT%H:%M:%S'), end_dt.strftime('%Y-%m-%dT%H:%M:%S')


def format_petrol_dt(dt: datetime) -> str:
    return dt.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')


def parse_float(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return 0.0

    s = s.replace(' ', '').replace(' ', '')
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    else:
        s = s.replace(',', '.')

    try:
        return float(s)
    except ValueError:
        return 0.0


def normalize_plate(value: Any) -> str:
    if pd.isna(value):
        return ''

    s = str(value).upper().strip()
    s = s.replace(' ', '').replace('-', '')
    if not s:
        return ''

    m = re.match(r'^(\d+)([A-Z].*)$', s)
    if not m:
        return s

    raw_prefix = m.group(1)
    rest = m.group(2)

    try:
        region_num = int(raw_prefix)
    except ValueError:
        return s

    region = f'{region_num:02d}'
    return f'{region}{rest}'


def normalize_card_no(value: Any) -> str:
    if pd.isna(value):
        return ''
    s = str(value).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s


def normalize_fuel_type(raw: Any) -> str:
    s = str(raw or '').strip().lower()
    mapping = {
        'kurşunsuz 95 - shell vp': 'gasoline',
        'motorin - shell vp diesel': 'diesel',
        'motorin': 'diesel',
        'motorin ultra force': 'diesel',
        'motorin ecoforce': 'diesel',
        'diesel': 'diesel',
        'eurodiesel': 'diesel',
        'benzin': 'gasoline',
        'kurşunsuz benzin': 'gasoline',
        '95 oktan': 'gasoline',
        '98 oktan': 'gasoline',
        'lpg': 'lpg',
        'otogaz': 'lpg',
    }
    return mapping.get(s, 'other')


def sha256_key(prefix: str, values: list[Any]) -> str:
    payload = '|'.join('' if v is None else str(v) for v in values)
    return f'{prefix}:{hashlib.sha256(payload.encode("utf-8")).hexdigest()}'


def normalize_plate(value) -> str:
    """
    Канонический формат номера:
    6EMY474   -> 06EMY474
    033EA665  -> 33EA665
    1AIF862   -> 01AIF862
    06EMY474  -> 06EMY474
    33EA665   -> 33EA665
    """
    if pd.isna(value):
        return ""

    s = str(value).upper().strip()
    s = s.replace(" ", "").replace("-", "")

    if not s:
        return ""

    m = re.match(r"^(\d+)([A-Z].*)$", s)
    if not m:
        return s

    raw_prefix = m.group(1)
    rest = m.group(2)

    raw_prefix = raw_prefix.lstrip("0") or "0"

    try:
        region_num = int(raw_prefix)
    except ValueError:
        return s

    region = f"{region_num:02d}"
    return f"{region}{rest}"