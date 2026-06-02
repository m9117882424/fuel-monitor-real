import csv
import json
import os
import re
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests


# ============================================================
# НАСТРОЙКИ WIALON
# ============================================================

# Лучше хранить в переменной окружения:
# export WIALON_TOKEN='...'
WIALON_TOKEN = os.getenv("WIALON_TOKEN", "7885fa13ecefabde8eccb765e7f4a6971ADD9CB58E6FEDFA889EBA6F87FB092141B0CF37")

BASE_URL = "https://hst-api.wialon.com/wialon/ajax.html"

REPORT_RESOURCE_ID = 600148718
REPORT_TEMPLATE_ID = 4
REPORT_OBJECT_ID = 601100490

# None = автоопределение таблицы по колонке "Положение" и геозоне Yakıt istasyon / АЗС.
# Если хочешь жёстко оставить конкретную таблицу, поставь TARGET_TABLE_INDEX = номер_таблицы.
TARGET_TABLE_INDEX: Optional[int] = None

# Если None — берём сегодняшний день по Europe/Istanbul.
# Можно указать вручную: "2026-05-08"
REPORT_DATE = None

WIALON_TZ = ZoneInfo("Europe/Istanbul")

# Wialon API в этом отчёте отдаёт время события на 3 часа раньше
# фактического времени отчёта. Например: 05:31:27 вместо 08:31:27.
# Поэтому при записи в витрину добавляем сдвиг. Если Wialon позже начнёт
# отдавать уже корректное локальное время, установи REPORT_EVENT_TIME_SHIFT_HOURS=0.
REPORT_EVENT_TIME_SHIFT_HOURS = int(os.getenv("REPORT_EVENT_TIME_SHIFT_HOURS", "3"))

# Оставляем только заправки, где Положение = эта геозона.
ALLOWED_FILLING_SOURCES = ("Yakıt istasyon / АЗС",)

# Колонка, где в отчёте Wialon находится геозона/положение.
FILLING_SOURCE_HEADER = "Положение"

# В разных версиях отчёта процент перед заправкой может быть в одной из этих колонок.
FUEL_PERCENT_HEADERS = (
    "Уровень топлива перед заправкой, %",
    "Нач. значение произв. датчика",
)

# Таблица "Заправки" даёт факт заправки и геозону, но не всегда содержит фирму/дирекцию.
# Таблица "Заполненость баков" содержит фирму/дирекцию, поэтому используем её как справочник
# и подставляем эти поля в итоговую витрину по госномеру/времени.
METADATA_REQUIRED_HEADERS = (
    "Grouping",
    "Время",
    "Фирма",
    "Дирекция",
)


# ============================================================
# НАСТРОЙКИ ВЫГРУЗКИ
# ============================================================

PROJECT_DIR = Path("/root/fuel_monitor_real")
OUT_DIR = PROJECT_DIR / "wialon_fuel_tank_output"


# ============================================================
# НАСТРОЙКИ ЗАГРУЗКИ В БД
# ============================================================

LOAD_TO_DB = True
CREATE_TABLE_IF_NOT_EXISTS = True

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "fuel_monitor"),
    "user": os.getenv("DB_USER", "fuel_user"),
    "password": os.getenv("DB_PASSWORD", "FuelMigrate2026"),
}

TABLE_NAME = "wialon_fuel_tank_dashboard"


# ============================================================
# SQL
# ============================================================

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id BIGSERIAL PRIMARY KEY,

    event_date DATE NOT NULL,
    event_time TIME,
    event_datetime TIMESTAMPTZ NOT NULL,

    gos_number TEXT NOT NULL,
    vehicle_type TEXT,
    firm TEXT,
    department TEXT,

    filling_source TEXT,

    fuel_level_before_filling_pct NUMERIC(6,2),
    risk_zone TEXT,

    source_report_name TEXT,
    source_table_index INTEGER,
    source_row_number INTEGER,

    loaded_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (event_datetime, gos_number)
);
"""

ALTER_TABLE_SQL = f"""
ALTER TABLE {TABLE_NAME}
ADD COLUMN IF NOT EXISTS filling_source TEXT;
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    event_date,
    event_time,
    event_datetime,
    gos_number,
    vehicle_type,
    firm,
    department,
    filling_source,
    fuel_level_before_filling_pct,
    risk_zone,
    source_report_name,
    source_table_index,
    source_row_number
)
VALUES (
    %(event_date)s,
    %(event_time)s,
    %(event_datetime)s,
    %(gos_number)s,
    %(vehicle_type)s,
    %(firm)s,
    %(department)s,
    %(filling_source)s,
    %(fuel_level_before_filling_pct)s,
    %(risk_zone)s,
    %(source_report_name)s,
    %(source_table_index)s,
    %(source_row_number)s
)
ON CONFLICT (event_datetime, gos_number)
DO UPDATE SET
    vehicle_type = EXCLUDED.vehicle_type,
    firm = EXCLUDED.firm,
    department = EXCLUDED.department,
    filling_source = EXCLUDED.filling_source,
    fuel_level_before_filling_pct = EXCLUDED.fuel_level_before_filling_pct,
    risk_zone = EXCLUDED.risk_zone,
    source_report_name = EXCLUDED.source_report_name,
    source_table_index = EXCLUDED.source_table_index,
    source_row_number = EXCLUDED.source_row_number,
    loaded_at = now();
"""


# ============================================================
# WIALON CLIENT
# ============================================================

class WialonClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self.sid: Optional[str] = None

    def call(
        self,
        svc: str,
        params: Optional[Dict[str, Any]] = None,
        use_sid: bool = True,
    ) -> Any:
        if params is None:
            params = {}

        request_params = {
            "svc": svc,
            "params": json.dumps(params, ensure_ascii=False),
        }

        if use_sid:
            if not self.sid:
                raise RuntimeError("SID отсутствует. Сначала нужен login().")
            request_params["sid"] = self.sid

        response = requests.get(self.base_url, params=request_params, timeout=120)
        response.raise_for_status()

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                f"Не удалось разобрать JSON от Wialon.\n"
                f"HTTP: {response.status_code}\n"
                f"TEXT: {response.text[:1000]}"
            )

        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(
                f"Wialon API error={data.get('error')}\n"
                f"svc={svc}\n"
                f"params={json.dumps(params, ensure_ascii=False, indent=2)}\n"
                f"response={json.dumps(data, ensure_ascii=False, indent=2)}"
            )

        return data

    def login(self) -> Dict[str, Any]:
        data = self.call(
            "token/login",
            {"token": self.token},
            use_sid=False,
        )
        self.sid = data["eid"]

        user_name = data.get("user", {}).get("nm") or data.get("au")
        print(f"OK: Wialon login. User: {user_name}")
        return data

    def logout(self) -> None:
        if not self.sid:
            return

        try:
            self.call("core/logout", {})
            print("OK: Wialon logout.")
        except Exception as e:
            print(f"WARNING: logout не выполнен: {e}")

    def cleanup_report(self) -> None:
        try:
            self.call("report/cleanup_result", {})
            print("OK: старый результат отчёта очищен.")
        except Exception as e:
            print(f"WARNING: cleanup_result пропущен: {e}")

    def exec_report(self, ts_from: int, ts_to: int) -> Dict[str, Any]:
        params = {
            "reportResourceId": REPORT_RESOURCE_ID,
            "reportTemplateId": REPORT_TEMPLATE_ID,
            "reportObjectId": REPORT_OBJECT_ID,
            "reportObjectSecId": 0,
            "interval": {
                "from": ts_from,
                "to": ts_to,
                "flags": 0,
            },
        }

        print("=" * 80)
        print("ЗАПУСК ГРУППОВОГО ОТЧЁТА WIALON")
        print("=" * 80)
        print(json.dumps(params, ensure_ascii=False, indent=2))

        return self.call("report/exec_report", params)

    def get_result_rows(
        self,
        table_index: int,
        index_from: int,
        index_to: int,
    ) -> List[Dict[str, Any]]:
        params = {
            "tableIndex": table_index,
            "indexFrom": index_from,
            "indexTo": index_to,
        }
        return self.call("report/get_result_rows", params)

    def get_result_subrows(
        self,
        table_index: int,
        row_index: int,
    ) -> List[Dict[str, Any]]:
        """
        Для многоуровневого отчёта Wialon: получаем вложенные строки следующего уровня.
        В нашем отчёте верхний уровень часто = дата, а вложенный уровень = техника/заправка.
        """
        params = {
            "tableIndex": table_index,
            "rowIndex": row_index,
        }
        return self.call("report/get_result_subrows", params)


# ============================================================
# HELPERS
# ============================================================

def parse_report_date(value: Optional[str]) -> date:
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()

    return datetime.now(WIALON_TZ).date()


def day_interval_unix(report_day: date) -> Tuple[int, int, datetime, datetime]:
    """
    Корректный интервал локального дня Europe/Istanbul.
    Без ручного вычитания UTC+3.
    """
    start_local = datetime.combine(report_day, dtime(0, 0, 0), tzinfo=WIALON_TZ)
    end_local = datetime.combine(report_day, dtime(23, 59, 59), tzinfo=WIALON_TZ)

    return int(start_local.timestamp()), int(end_local.timestamp()), start_local, end_local


def collect_text_values(value: Any) -> List[str]:
    """
    Рекурсивно собирает все текстовые/числовые представления из ячейки Wialon.
    Нужно потому, что Wialon иногда прячет отображаемое значение внутри dict/list.
    """
    result: List[str] = []

    if value is None:
        return result

    if isinstance(value, dict):
        for v in value.values():
            result.extend(collect_text_values(v))
        return result

    if isinstance(value, list):
        for item in value:
            result.extend(collect_text_values(item))
        return result

    text = str(value).strip()
    if text:
        result.append(text)

    return result


def normalize_for_compare(value: Any) -> str:
    return str(value or "").strip().casefold().replace("ё", "е")


def find_allowed_filling_source(*values: Any) -> Optional[str]:
    """
    Определяет нужную геозону по колонке "Положение".

    Целевое значение: Yakıt istasyon / АЗС.
    Проверяем и raw-ячейку Wialon, и нормализованное значение, потому что
    Wialon может возвращать ячейку строкой, dict или list.
    """
    allowed_normalized = [normalize_for_compare(x) for x in ALLOWED_FILLING_SOURCES]

    def walk(value: Any) -> Optional[str]:
        if value is None:
            return None

        if isinstance(value, dict):
            # Сначала смотрим основные человекочитаемые поля.
            for key in ("t", "text", "name", "n", "v"):
                if key in value and value[key] not in (None, ""):
                    found = walk(value[key])
                    if found:
                        return found

            # Потом рекурсивно проверяем всё остальное.
            for nested_value in value.values():
                found = walk(nested_value)
                if found:
                    return found

            return None

        if isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
            return None

        text_norm = normalize_for_compare(value)
        if not text_norm:
            return None

        for source, source_norm in zip(ALLOWED_FILLING_SOURCES, allowed_normalized):
            # Основной вариант: точное совпадение.
            if text_norm == source_norm:
                return source

            # Резерв: если Wialon добавит адрес/комментарий вокруг названия геозоны.
            if source_norm in text_norm:
                return source

        return None

    for value in values:
        found = walk(value)
        if found:
            return found

    return None

def extract_cell_value(cell: Any) -> Any:
    """
    Универсальное человекочитаемое значение ячейки Wialon.
    Для фильтра по геозоне используется отдельная функция,
    чтобы корректно обработать строку/dict/list от Wialon.
    """
    if isinstance(cell, dict):
        # Сначала пытаемся взять строковые отображаемые значения.
        for key in ("t", "text", "name", "n", "v", "y"):
            if key in cell and cell[key] not in (None, ""):
                return cell[key]
        return json.dumps(cell, ensure_ascii=False)

    if isinstance(cell, list):
        return " | ".join(str(extract_cell_value(x)) for x in cell)

    return cell


def get_raw_cell(row: Dict[str, Any], headers: List[str], header_name: str) -> Any:
    if header_name not in headers:
        return None

    idx = headers.index(header_name)
    cells = row.get("c", [])

    if idx >= len(cells):
        return None

    return cells[idx]


def get_first_existing_value(row: Dict[str, Any], headers: List[str], header_names: Tuple[str, ...]) -> Any:
    for header in header_names:
        if header in row:
            value = row.get(header)
            if value not in (None, ""):
                return value

    return None




def is_date_string(value: Any) -> bool:
    if value is None:
        return False
    return bool(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", str(value).strip()))


def extract_date_from_value(value: Any) -> Optional[str]:
    """Возвращает дату в формате DD.MM.YYYY, если value похож на дату."""
    if value is None:
        return None

    text = str(value).strip()

    if is_date_string(text):
        return text

    match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text)
    if match:
        return match.group(1)

    return None


def extract_text_from_cell(cell: Any) -> str:
    """
    Для ячеек Wialon берём только отображаемый текст.

    Важно: для колонки "Время" нельзя брать техническое поле v,
    потому что это Unix timestamp и он даёт смещение в дашборде.
    Берём только t/text/name/n.
    """
    if cell is None:
        return ""

    if isinstance(cell, dict):
        for key in ("t", "text", "name", "n"):
            value = cell.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    if isinstance(cell, list):
        parts = [extract_text_from_cell(x) for x in cell]
        return " ".join(x for x in parts if x).strip()

    return str(cell).strip()


def expand_table_rows(
    client: WialonClient,
    table_index: int,
    headers: List[str],
    rows_count: int,
) -> List[Dict[str, Any]]:
    """
    Возвращает рабочие строки таблицы.

    В отчёте Wialon верхний уровень может быть датой, например:
    Grouping = 08.05.2026, d = 17.

    Для дашборда нужны не эти агрегаты, а вложенные строки с техникой.
    Поэтому если у строки есть вложенные строки, берём subrows.
    Если вложенных строк нет — оставляем саму строку.
    """
    top_rows = client.get_result_rows(
        table_index=table_index,
        index_from=0,
        index_to=rows_count - 1,
    )

    expanded_rows: List[Dict[str, Any]] = []

    for top_idx, top_row in enumerate(top_rows):
        normalized_top = normalize_wialon_row(top_row, headers)
        parent_grouping = normalized_top.get("Grouping")
        parent_date = extract_date_from_value(parent_grouping)

        nested_count = int(top_row.get("d") or 0)

        if nested_count > 0:
            try:
                subrows = client.get_result_subrows(table_index=table_index, row_index=top_idx)
            except Exception as e:
                print(
                    f"WARNING: не удалось раскрыть subrows для table={table_index}, "
                    f"row={top_idx}: {e}. Берём верхнюю строку."
                )
                subrows = []

            if subrows:
                for subrow in subrows:
                    subrow["_parent_grouping"] = parent_grouping
                    subrow["_parent_date"] = parent_date
                    expanded_rows.append(subrow)
                continue

        top_row["_parent_grouping"] = parent_grouping
        top_row["_parent_date"] = parent_date
        expanded_rows.append(top_row)

    print(f"OK: таблица {table_index}: верхних строк={len(top_rows)}, рабочих строк после раскрытия={len(expanded_rows)}")
    return expanded_rows


def parse_event_datetime_from_row(
    row: Dict[str, Any],
    raw_row: Optional[Dict[str, Any]] = None,
    headers: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Корректно собирает дату/время для многоуровневого отчёта.

    Ключевой фикс: для колонки "Время" берём отображаемое поле t
    из raw-ячейки Wialon. Поле v/Unix timestamp не используем вообще.

    Варианты Wialon:
    1) Время = '08.05.2026 08:31:27'
    2) Время = {'t': '08:31:27', 'v': ...}, дата в _parent_date = '08.05.2026'
    """
    raw_time = ""

    # Главный путь: raw Wialon-ячейка. Для dict вернёт только cell['t'].
    if raw_row is not None and headers is not None:
        time_cell = get_raw_cell(raw_row, headers, "Время")
        raw_time = extract_text_from_cell(time_cell)

    # Резерв для старых вызовов, например справочник фирмы/дирекции.
    if not raw_time:
        raw_time = extract_text_from_cell(row.get("Время"))

    if not raw_time:
        return None, None, None

    # Полная дата и время уже в одной колонке.
    if re.search(r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}", raw_time):
        return parse_datetime_value(raw_time)

    # Только время. Дату берём из parent_date или из Grouping, если Grouping = дата.
    if re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", raw_time):
        date_text = (
            row.get("_parent_date")
            or (raw_row or {}).get("_parent_date")
            or extract_date_from_value(row.get("Grouping"))
        )

        if not date_text:
            normalized_time = raw_time if len(raw_time) == 8 else raw_time + ":00"
            return None, normalized_time, None

        normalized_time = raw_time if len(raw_time) == 8 else raw_time + ":00"
        dt_text = f"{date_text} {normalized_time}"
        return parse_datetime_value(dt_text)

    return parse_datetime_value(raw_time)

def normalize_wialon_row(row: Dict[str, Any], headers: List[str]) -> Dict[str, Any]:
    cells = row.get("c", [])
    result = {}

    for i, header in enumerate(headers):
        result[header] = extract_cell_value(cells[i]) if i < len(cells) else None

    result["_row_n"] = row.get("n")
    result["_row_i1"] = row.get("i1")
    result["_row_i2"] = row.get("i2")
    result["_row_level_or_d"] = row.get("d")

    # Метаданные, которые добавляются при раскрытии многоуровневых строк.
    result["_parent_grouping"] = row.get("_parent_grouping")
    result["_parent_date"] = row.get("_parent_date")

    return result


def split_grouping(value: Any) -> Tuple[str, str]:
    """
    Пример:
    01ALT65_HI-UP/КРАН МАНИПУЛЯТОР
    ->
    01ALT65
    HI-UP/КРАН МАНИПУЛЯТОР
    """
    if not value:
        return "", ""

    value = str(value).strip()

    if "_" in value:
        gos_number, vehicle_type = value.split("_", 1)
        return gos_number.strip(), vehicle_type.strip()

    parts = value.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()

    return value, ""


def parse_datetime_value(value: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Вход:
    08.05.2026 05:05:06

    Выход:
    event_date: 2026-05-08
    event_time: 08:05:06, если REPORT_EVENT_TIME_SHIFT_HOURS=3
    event_datetime: 2026-05-08 08:05:06+03:00
    """
    if not value:
        return None, None, None

    value = str(value).strip()

    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            dt = datetime.strptime(value, fmt)

            if REPORT_EVENT_TIME_SHIFT_HOURS:
                dt = dt + timedelta(hours=REPORT_EVENT_TIME_SHIFT_HOURS)

            dt = dt.replace(tzinfo=WIALON_TZ)

            event_date = dt.strftime("%Y-%m-%d")
            event_time = dt.strftime("%H:%M:%S")
            event_datetime = dt.isoformat()

            return event_date, event_time, event_datetime
        except ValueError:
            pass

    return None, None, value


def parse_percent(value: Any) -> Optional[float]:
    """
    '24.24' -> 24.24
    '15,97' -> 15.97
    '-----' -> None
    """
    if value is None:
        return None

    value = str(value).strip().replace(",", ".")

    if value in ("", "-----", "-", "—", "None", "null"):
        return None

    match = re.search(r"-?\d+(\.\d+)?", value)
    if not match:
        return None

    return round(float(match.group(0)), 2)


def risk_zone(percent: Optional[float]) -> str:
    if percent is None:
        return "нет данных"

    if percent < 20:
        return "критично"

    if percent < 40:
        return "низкий остаток"

    if percent < 70:
        return "норма"

    return "высокий остаток"


def safe_filename(name: str) -> str:
    bad_chars = '<>:"/\\|?*'
    for ch in bad_chars:
        name = name.replace(ch, "_")
    return name.strip() or "file"


def save_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    headers = [
        "№",
        "Госномер",
        "Тип техники",
        "Фирма",
        "Дирекция",
        "Место заправки",
        "Дата",
        "Время заправки",
        "Дата и время",
        "Начальный уровень топлива в баке при въезде на АЗС %",
        "Зона риска",
        "Источник",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


# ============================================================
# REPORT PARSING
# ============================================================

def table_has_needed_headers(headers: List[str]) -> bool:
    has_fuel_percent = any(h in headers for h in FUEL_PERCENT_HEADERS)

    required = [
        "Grouping",
        "Время",
        FILLING_SOURCE_HEADER,
    ]

    return all(h in headers for h in required) and has_fuel_percent


def get_report_tables(report_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    tables = report_result.get("reportResult", {}).get("tables", [])

    if not tables:
        raise RuntimeError("В результате отчёта нет таблиц.")

    return tables


def detect_target_table(
    client: WialonClient,
    report_result: Dict[str, Any],
) -> Tuple[int, str, List[str], int, List[Dict[str, Any]]]:
    """
    Если TARGET_TABLE_INDEX задан — берём его.
    Если None — ищем таблицу, где в колонке "Положение" реально есть Yakıt istasyon / АЗС.
    """
    tables = get_report_tables(report_result)

    print("=" * 80)
    print("СТРУКТУРА ОТЧЁТА")
    print("=" * 80)

    for idx, table in enumerate(tables):
        print(f"TABLE_INDEX: {idx}")
        print(f"NAME: {table.get('name')}")
        print(f"LABEL: {table.get('label')}")
        print(f"ROWS: {table.get('rows')}")
        print(f"LEVEL: {table.get('level')}")
        print(f"HEADER: {table.get('header')}")
        print("-" * 80)

    candidate_indexes: List[int]

    if TARGET_TABLE_INDEX is not None:
        if TARGET_TABLE_INDEX >= len(tables):
            raise RuntimeError(
                f"В отчёте нет таблицы с индексом {TARGET_TABLE_INDEX}. "
                f"Всего таблиц: {len(tables)}"
            )
        candidate_indexes = [TARGET_TABLE_INDEX]
    else:
        candidate_indexes = [
            idx
            for idx, table in enumerate(tables)
            if table_has_needed_headers(table.get("header") or [])
        ]

    if not candidate_indexes:
        raise RuntimeError(
            "Не найдена таблица с нужными колонками: "
            "Grouping, Время, Положение и колонкой начального процента."
        )

    best: Optional[Tuple[int, str, List[str], int, List[Dict[str, Any]], int]] = None

    for table_index in candidate_indexes:
        table = tables[table_index]
        table_name = table.get("label") or table.get("name") or f"table_{table_index}"
        headers = table.get("header") or []
        rows_count = int(table.get("rows") or 0)

        if rows_count <= 0:
            continue

        raw_rows = expand_table_rows(
            client=client,
            table_index=table_index,
            headers=headers,
            rows_count=rows_count,
        )

        source_hits = 0

        for raw_row in raw_rows:
            description_raw = get_raw_cell(raw_row, headers, FILLING_SOURCE_HEADER)
            normalized_row = normalize_wialon_row(raw_row, headers)
            description_norm = normalized_row.get(FILLING_SOURCE_HEADER)

            if find_allowed_filling_source(description_raw, description_norm):
                source_hits += 1

        print("=" * 80)
        print("ПРОВЕРКА ТАБЛИЦЫ-КАНДИДАТА")
        print("=" * 80)
        print(f"TABLE_INDEX: {table_index}")
        print(f"TABLE_NAME: {table_name}")
        print(f"ROWS: {rows_count}")
        print(f"Геозона АЗС в колонке '{FILLING_SOURCE_HEADER}': {source_hits}")

        if best is None or source_hits > best[5]:
            best = (table_index, table_name, headers, rows_count, raw_rows, source_hits)

    if best is None:
        raise RuntimeError("Не удалось получить строки ни из одной таблицы-кандидата.")

    table_index, table_name, headers, rows_count, raw_rows, source_hits = best

    if source_hits == 0:
        # Не падаем молча — даём нормальную диагностику.
        sample = raw_rows[:3]
        raise RuntimeError(
            f"В выбранных таблицах не найдена геозона {ALLOWED_FILLING_SOURCES} в колонке '{FILLING_SOURCE_HEADER}'.\n"
            f"Выбранная таблица: index={table_index}, name={table_name}\n"
            f"Заголовки: {headers}\n"
            f"Первые raw-строки для диагностики:\n"
            f"{json.dumps(sample, ensure_ascii=False, indent=2)}"
        )

    print("=" * 80)
    print("ВЫБРАНА ЦЕЛЕВАЯ ТАБЛИЦА")
    print("=" * 80)
    print(f"TABLE_INDEX: {table_index}")
    print(f"TABLE_NAME: {table_name}")
    print(f"ROWS: {rows_count}")
    print(f"HEADERS: {headers}")

    return table_index, table_name, headers, rows_count, raw_rows



def table_has_metadata_headers(headers: List[str]) -> bool:
    return all(header in headers for header in METADATA_REQUIRED_HEADERS)


def build_vehicle_metadata_lookup(
    client: WialonClient,
    report_result: Dict[str, Any],
) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """
    Собирает справочник фирмы/дирекции из таблицы отчёта, где есть колонки:
    Grouping, Время, Фирма, Дирекция.

    Обычно это TABLE_INDEX 4 / "Заполненость баков".
    Основная таблица "Заправки" нужна для геозоны, но в ней этих полей нет.
    """
    lookup: Dict[str, Dict[Any, Dict[str, str]]] = {
        "by_exact": {},  # key = (gos_number, event_datetime)
        "by_gos": {},    # key = gos_number
    }

    tables = get_report_tables(report_result)

    for table_index, table in enumerate(tables):
        headers = table.get("header") or []

        if not table_has_metadata_headers(headers):
            continue

        rows_count = int(table.get("rows") or 0)
        if rows_count <= 0:
            continue

        table_name = table.get("label") or table.get("name") or f"table_{table_index}"

        try:
            raw_rows = client.get_result_rows(
                table_index=table_index,
                index_from=0,
                index_to=rows_count - 1,
            )
        except Exception as e:
            print(f"WARNING: не удалось прочитать таблицу метаданных {table_index} ({table_name}): {e}")
            continue

        normalized_rows = [normalize_wialon_row(row, headers) for row in raw_rows]

        exact_count = 0
        gos_count = 0

        for row in normalized_rows:
            gos_number, _vehicle_type = split_grouping(row.get("Grouping"))
            if not gos_number:
                continue

            firm = str(row.get("Фирма") or "").strip()
            department = str(row.get("Дирекция") or "").strip()

            if not firm and not department:
                continue

            metadata = {
                "firm": firm,
                "department": department,
                "metadata_source_table": table_name,
            }

            _event_date, _event_time, event_datetime = parse_event_datetime_from_row(row)

            if event_datetime:
                lookup["by_exact"][(gos_number, event_datetime)] = metadata
                exact_count += 1

            # Фирма/дирекция обычно постоянные для техники в рамках отчётного дня,
            # поэтому fallback по госномеру нормален и закрывает разницу между таблицами.
            lookup["by_gos"][gos_number] = metadata
            gos_count += 1

        print(
            f"OK: справочник фирмы/дирекции из таблицы {table_index} ({table_name}): "
            f"точных ключей={exact_count}, госномеров={len(lookup['by_gos'])}"
        )

    if not lookup["by_exact"] and not lookup["by_gos"]:
        print("WARNING: справочник фирмы/дирекции не найден. Поля firm/department останутся пустыми.")

    return lookup



EMPTY_METADATA_VALUES = {
    "",
    "-",
    "--",
    "---",
    "-----",
    "нет данных",
    "Нет данных",
    "N/A",
    "n/a",
    "None",
    "none",
}


def clean_metadata_value(value: Any) -> str:
    text = str(value or "").strip()
    if text in EMPTY_METADATA_VALUES:
        return ""
    return text


def get_profile_field(item: Dict[str, Any], field_name: str) -> str:
    for block_name in ("pflds", "profile", "flds", "aflds"):
        block = item.get(block_name)
        if not isinstance(block, dict):
            continue

        for field in block.values():
            if not isinstance(field, dict):
                continue
            if str(field.get("n") or "").strip() == field_name:
                return clean_metadata_value(field.get("v"))

    return ""


def build_unit_properties_lookup(client: WialonClient) -> Dict[str, Dict[str, str]]:
    """
    Основной справочник оргструктуры из свойств объектов Wialon:
    Фирма    = pflds.brand
    Дирекция = pflds.vehicle_type
    Госномер = pflds.registration_plate или имя объекта
    """
    data = client.call(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name",
            },
            "force": 1,
            "flags": 4611686018427387903,
            "from": 0,
            "to": 0,
        },
    )

    items = data.get("items", []) if isinstance(data, dict) else []
    lookup: Dict[str, Dict[str, str]] = {}

    for item in items:
        unit_name = clean_metadata_value(item.get("nm"))
        registration_plate = get_profile_field(item, "registration_plate")
        gos_number = registration_plate or split_grouping(unit_name)[0] or unit_name
        gos_number = clean_metadata_value(gos_number)

        if not gos_number:
            continue

        firm = get_profile_field(item, "brand")
        department = get_profile_field(item, "vehicle_type")

        lookup[gos_number] = {
            "firm": firm,
            "department": department,
            "metadata_source": "wialon_unit_pflds",
        }

    print(
        "OK: справочник из свойств объектов Wialon: "
        f"объектов={len(items)}, госномеров={len(lookup)}"
    )
    return lookup


def find_unit_properties(
    unit_lookup: Dict[str, Dict[str, str]],
    gos_number: str,
) -> Dict[str, str]:
    if not unit_lookup:
        return {}

    if gos_number in unit_lookup:
        return unit_lookup[gos_number]

    target = re.sub(r"[^A-ZА-ЯЁ0-9]", "", str(gos_number or "").upper())

    for key, value in unit_lookup.items():
        key_norm = re.sub(r"[^A-ZА-ЯЁ0-9]", "", str(key or "").upper())
        if key_norm == target:
            return value

    return {}

def find_vehicle_metadata(
    metadata_lookup: Dict[str, Dict[Any, Dict[str, str]]],
    gos_number: str,
    event_datetime: Optional[str],
) -> Dict[str, str]:
    if not metadata_lookup:
        return {}

    if event_datetime:
        exact = metadata_lookup.get("by_exact", {}).get((gos_number, event_datetime))
        if exact:
            return exact

    by_gos = metadata_lookup.get("by_gos", {}).get(gos_number)
    if by_gos:
        return by_gos

    return {}


def parse_dashboard_rows(
    raw_rows: List[Dict[str, Any]],
    headers: List[str],
    table_name: str,
    table_index: int,
    metadata_lookup: Optional[Dict[str, Dict[Any, Dict[str, str]]]] = None,
    unit_properties_lookup: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    normalized_rows = [
        normalize_wialon_row(row, headers)
        for row in raw_rows
    ]

    dashboard_rows = []

    for idx, (raw_row, row) in enumerate(zip(raw_rows, normalized_rows), start=1):
        description_raw = get_raw_cell(raw_row, headers, FILLING_SOURCE_HEADER)
        description_norm = row.get(FILLING_SOURCE_HEADER)

        filling_source = find_allowed_filling_source(description_raw, description_norm)

        if not filling_source:
            print(
                f"INFO: строка {idx}: положение не относится к "
                f"{', '.join(ALLOWED_FILLING_SOURCES)}, строка пропущена. "
                f"Положение: {description_norm}"
            )
            continue

        grouping = row.get("Grouping")
        gos_number, vehicle_type = split_grouping(grouping)

        event_date, event_time, event_datetime = parse_event_datetime_from_row(row, raw_row=raw_row, headers=headers)

        fuel_source_value = get_first_existing_value(row, headers, FUEL_PERCENT_HEADERS)
        fuel_pct = parse_percent(fuel_source_value)

        if not gos_number:
            print(f"WARNING: строка {idx}: пустой госномер, строка пропущена: {row}")
            continue

        if not event_datetime:
            print(f"WARNING: строка {idx}: пустое время, строка пропущена: {row}")
            continue

        unit_properties = find_unit_properties(unit_properties_lookup or {}, gos_number)
        metadata = find_vehicle_metadata(metadata_lookup or {}, gos_number, event_datetime)

        firm = (
            clean_metadata_value(unit_properties.get("firm"))
            or clean_metadata_value(metadata.get("firm"))
            or clean_metadata_value(row.get("Фирма"))
            or "-----"
        )
        department = (
            clean_metadata_value(unit_properties.get("department"))
            or clean_metadata_value(metadata.get("department"))
            or clean_metadata_value(row.get("Дирекция"))
            or "-----"
        )

        if not firm and not department:
            print(
                f"WARNING: строка {idx}: не найдены Фирма/Дирекция для {gos_number} "
                f"на {event_datetime}"
            )

        dashboard_rows.append({
            "source_row_number": idx,
            "gos_number": gos_number,
            "vehicle_type": vehicle_type,
            "firm": firm,
            "department": department,
            "filling_source": filling_source,
            "event_date": event_date,
            "event_time": event_time,
            "event_datetime": event_datetime,
            "fuel_level_before_filling_pct": fuel_pct,
            "risk_zone": risk_zone(fuel_pct),
            "source_report_name": table_name,
            "source_table_index": table_index,
        })

    def sort_key(item: Dict[str, Any]):
        fuel = item["fuel_level_before_filling_pct"]
        if fuel is None:
            return (1, 999999)
        return (0, fuel)

    dashboard_rows.sort(key=sort_key)

    return dashboard_rows, normalized_rows


def to_user_csv_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []

    for idx, row in enumerate(rows, start=1):
        result.append({
            "№": idx,
            "Госномер": row["gos_number"],
            "Тип техники": row["vehicle_type"],
            "Фирма": row["firm"],
            "Дирекция": row["department"],
            "Место заправки": row["filling_source"],
            "Дата": row["event_date"],
            "Время заправки": row["event_time"],
            "Дата и время": row["event_datetime"],
            "Начальный уровень топлива в баке при въезде на АЗС %": row["fuel_level_before_filling_pct"],
            "Зона риска": row["risk_zone"],
            "Источник": f"Wialon group report / {row['source_report_name']}",
        })

    return result


# ============================================================
# DATABASE
# ============================================================

def load_to_db_stub(rows: List[Dict[str, Any]]) -> None:
    print("=" * 80)
    print("DB LOAD: ТЕСТОВАЯ ЗАГЛУШКА")
    print("=" * 80)
    print(f"LOAD_TO_DB = {LOAD_TO_DB}")
    print(f"Строк подготовлено к загрузке: {len(rows)}")
    print(f"Целевая таблица: {TABLE_NAME}")
    print()
    print("Первые строки, которые будут загружены:")

    for row in rows[:10]:
        print(json.dumps(row, ensure_ascii=False, indent=2))

    print()
    print("SQL создания таблицы на будущее:")
    print(CREATE_TABLE_SQL.strip())


def load_to_postgres(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("DB LOAD: строк нет, загрузка пропущена.")
        return

    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        raise RuntimeError(
            "Не установлен psycopg2-binary.\n"
            "Установи:\n"
            "pip install psycopg2-binary"
        )

    conn = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False

        with conn.cursor() as cur:
            if CREATE_TABLE_IF_NOT_EXISTS:
                cur.execute(CREATE_TABLE_SQL)
                try:
                    cur.execute(ALTER_TABLE_SQL)
                except Exception as alter_error:
                    # Если таблица уже создана не пользователем fuel_user, ALTER TABLE может быть запрещён.
                    # Для штатной загрузки это не критично, если колонка filling_source уже есть.
                    print(f"WARNING: ALTER TABLE пропущен: {alter_error}")
                    conn.rollback()
                    conn.autocommit = False
                    cur = conn.cursor()

            execute_batch(cur, INSERT_SQL, rows, page_size=100)

        conn.commit()
        print(f"OK: в PostgreSQL загружено/обновлено строк: {len(rows)}")

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    if not WIALON_TOKEN or WIALON_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН":
        raise RuntimeError(
            "Сначала вставь Wialon token в WIALON_TOKEN "
            "или задай переменную окружения WIALON_TOKEN."
        )

    if LOAD_TO_DB and not DB_CONFIG.get("password"):
        raise RuntimeError(
            "Сначала задай пароль БД через переменную окружения DB_PASSWORD."
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    report_day = parse_report_date(REPORT_DATE)
    ts_from, ts_to, start_local, end_local = day_interval_unix(report_day)

    print("=" * 80)
    print("WIALON FUEL TANK DASHBOARD SYNC")
    print("=" * 80)
    print(f"Дата отчёта: {report_day}")
    print(f"Период Wialon: {start_local.isoformat()} — {end_local.isoformat()}")
    print(f"UNIX FROM: {ts_from}")
    print(f"UNIX TO:   {ts_to}")
    print(f"Фильтр геозоны: {', '.join(ALLOWED_FILLING_SOURCES)}")
    print(f"Сдвиг времени событий: +{REPORT_EVENT_TIME_SHIFT_HOURS} ч")
    print(f"LOAD_TO_DB: {LOAD_TO_DB}")
    print()

    client = WialonClient(BASE_URL, WIALON_TOKEN)

    try:
        client.login()
        client.cleanup_report()

        report_result = client.exec_report(ts_from, ts_to)

        raw_exec_path = OUT_DIR / f"raw_exec_report_{report_day}.json"
        save_json(raw_exec_path, report_result)
        print(f"OK: raw exec_report сохранён: {raw_exec_path}")

        table_index, table_name, headers, rows_count, raw_rows = detect_target_table(
            client=client,
            report_result=report_result,
        )

        raw_rows_path = OUT_DIR / f"raw_table_{table_index}_{safe_filename(table_name)}_{report_day}.json"
        save_json(raw_rows_path, raw_rows)
        print(f"OK: raw rows сохранены: {raw_rows_path}")

        metadata_lookup = build_vehicle_metadata_lookup(
            client=client,
            report_result=report_result,
        )
        unit_properties_lookup = build_unit_properties_lookup(client)

        dashboard_rows, normalized_rows = parse_dashboard_rows(
            raw_rows=raw_rows,
            headers=headers,
            table_name=table_name,
            table_index=table_index,
            metadata_lookup=metadata_lookup,
            unit_properties_lookup=unit_properties_lookup,
        )

        normalized_path = OUT_DIR / f"normalized_table_{table_index}_{safe_filename(table_name)}_{report_day}.json"
        db_rows_path = OUT_DIR / f"db_rows_{report_day}.json"
        csv_path = OUT_DIR / f"fuel_tank_dashboard_{report_day}.csv"

        save_json(normalized_path, normalized_rows)
        save_json(db_rows_path, dashboard_rows)
        save_csv(csv_path, to_user_csv_rows(dashboard_rows))

        print("=" * 80)
        print("ФАЙЛЫ СФОРМИРОВАНЫ")
        print("=" * 80)
        print(f"CSV для просмотра:       {csv_path}")
        print(f"JSON для загрузки в БД:  {db_rows_path}")
        print(f"Нормализованные строки:  {normalized_path}")
        print()

        print("=" * 80)
        print("ИТОГ ПАРСИНГА")
        print("=" * 80)
        print(f"Строк из Wialon: {rows_count}")
        print(f"Строк после фильтра геозоны АЗС: {len(dashboard_rows)}")

        critical = sum(1 for r in dashboard_rows if r["risk_zone"] == "критично")
        low = sum(1 for r in dashboard_rows if r["risk_zone"] == "низкий остаток")
        no_data = sum(1 for r in dashboard_rows if r["risk_zone"] == "нет данных")

        print(f"Критично < 20%: {critical}")
        print(f"Низкий остаток 20–40%: {low}")
        print(f"Нет данных: {no_data}")
        print()

        if LOAD_TO_DB:
            load_to_postgres(dashboard_rows)
        else:
            load_to_db_stub(dashboard_rows)

        print("=" * 80)
        print("ГОТОВО")
        print("=" * 80)

    finally:
        try:
            client.cleanup_report()
        except Exception:
            pass
        client.logout()


if __name__ == "__main__":
    main()
