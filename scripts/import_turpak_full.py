import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import requests
from requests.structures import CaseInsensitiveDict

# ============================================================
# НАСТРОЙКИ
# ============================================================

PROJECT_DIR = Path("/root/fuel_monitor_real")
OUT_DIR = PROJECT_DIR / "turpak_import_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TURPAK_URL = "https://mersintransfer.turpakmonitor.com/api/Main/GetSales"

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "fuel_monitor"),
    "user": os.getenv("DB_USER", "fuel_user"),
    "password": os.getenv("DB_PASSWORD", "FuelMigrate2026"),
}

TABLE_NAME = "turpak_fuel_events_all"


# ============================================================
# SQL
# ============================================================

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    source,
    external_id,
    event_key,
    event_dt,
    year_month,
    plate,
    fuel_type_raw,
    fuel_type_norm,
    liters,
    unit_price_try,
    amount_try,
    discount_try,
    station_code,
    station_name,
    station_city,
    receipt_no,
    card_no,
    card_type,
    group_name,
    odometer,
    sale_type,
    department_code,
    raw_payload
)
VALUES (
    %(source)s,
    %(external_id)s,
    %(event_key)s,
    %(event_dt)s,
    %(year_month)s,
    %(plate)s,
    %(fuel_type_raw)s,
    %(fuel_type_norm)s,
    %(liters)s,
    %(unit_price_try)s,
    %(amount_try)s,
    %(discount_try)s,
    %(station_code)s,
    %(station_name)s,
    %(station_city)s,
    %(receipt_no)s,
    %(card_no)s,
    %(card_type)s,
    %(group_name)s,
    %(odometer)s,
    %(sale_type)s,
    %(department_code)s,
    %(raw_payload)s::jsonb
)
ON CONFLICT (event_key)
DO UPDATE SET
    external_id = EXCLUDED.external_id,
    event_dt = EXCLUDED.event_dt,
    year_month = EXCLUDED.year_month,
    plate = EXCLUDED.plate,
    fuel_type_raw = EXCLUDED.fuel_type_raw,
    fuel_type_norm = EXCLUDED.fuel_type_norm,
    liters = EXCLUDED.liters,
    unit_price_try = EXCLUDED.unit_price_try,
    amount_try = EXCLUDED.amount_try,
    discount_try = EXCLUDED.discount_try,
    station_code = EXCLUDED.station_code,
    station_name = EXCLUDED.station_name,
    station_city = EXCLUDED.station_city,
    receipt_no = EXCLUDED.receipt_no,
    card_no = EXCLUDED.card_no,
    card_type = EXCLUDED.card_type,
    group_name = EXCLUDED.group_name,
    odometer = EXCLUDED.odometer,
    sale_type = EXCLUDED.sale_type,
    department_code = EXCLUDED.department_code,
    raw_payload = EXCLUDED.raw_payload,
    created_at = CURRENT_TIMESTAMP;
"""


# ============================================================
# TOKEN
# ============================================================


def get_turpak_token() -> str:
    """
    1. Сначала пробуем переменную окружения TURPAK_TOKEN.
    2. Потом пробуем scripts/trpktoken.py, как в старом рабочем коде.
    """
    env_token = os.getenv("TURPAK_TOKEN")
    if env_token:
        return env_token

    try:
        import trpktoken

        return trpktoken.token()
    except Exception as e:
        raise RuntimeError(
            "Не удалось получить Turpak token. "
            "Задай TURPAK_TOKEN или проверь файл scripts/trpktoken.py. "
            f"Ошибка: {e}"
        )


# ============================================================
# HELPERS
# ============================================================


def first(row: dict, *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None

    text = text.replace(" ", "").replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def to_text(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    return text if text else None


def parse_event_dt(value: Any, fallback_date: str) -> str:
    """
    Возвращает строку timestamp с +03:00.

    Turpak в твоём ответе отдаёт:
    saleBegin = 2026-05-08T08:00:19

    Сохраняем как:
    2026-05-08 08:00:19+03:00
    """
    if not value:
        return f"{fallback_date} 00:00:00+03:00"

    text = str(value).strip()

    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S") + "+03:00"
        except ValueError:
            pass

    date_formats = [
        "%Y-%m-%d",
        "%d.%m.%Y",
    ]

    for fmt in date_formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d 00:00:00") + "+03:00"
        except ValueError:
            pass

    print(
        f"WARNING: не удалось распарсить дату/время: {text}. Использую fallback_date."
    )
    return f"{fallback_date} 00:00:00+03:00"


def normalize_fuel_type(value: Any) -> Optional[str]:
    text = to_text(value)
    if not text:
        return None

    upper = text.upper()

    if (
        "DIZEL" in upper
        or "DİZEL" in upper
        or "DIESEL" in upper
        or "MOTORIN" in upper
        or "MOTORİN" in upper
    ):
        return "DIESEL"

    if "95" in upper or "BENZIN" in upper or "BENZİN" in upper or "GASOLINE" in upper:
        return "GASOLINE_95"

    return text


def make_event_key(row: dict, fallback_date: str) -> str:
    """
    Основной уникальный ключ — Turpak id.

    Если id нет, делаем стабильный hash.
    """
    external_id = first(
        row,
        "id",
        "saleId",
        "salesId",
        "transactionId",
        "receiptNo",
        "receipt_no",
        "fisNo",
        "documentNo",
    )

    if external_id:
        return f"turpak_full:{external_id}"

    key_payload = {
        "date": first(
            row,
            "saleBegin",
            "saleEnd",
            "saleDate",
            "saleDateTime",
            "salesDate",
            "date",
            "createdDate",
            "transactionDate",
        )
        or fallback_date,
        "plate": first(row, "licensePlateNr", "plate", "plateNo", "vehiclePlate"),
        "group": first(row, "groupName", "group_name"),
        "volume": first(row, "volume", "liters", "litre", "quantity"),
        "amount": first(row, "total", "amount", "totalAmount", "totalPrice", "price"),
        "station": first(row, "stationName", "station_name", "station", "stationCode"),
        "card": first(row, "cardNo", "cardNumber", "card_no", "idUnitCode"),
    }

    raw = json.dumps(key_payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return f"turpak_full:hash:{digest}"


def map_sale(row: dict, target_date: str) -> dict:
    """
    Маппинг одной продажи Turpak в структуру turpak_fuel_events_all.

    Реальные ключи из твоего API:
    id, saleBegin, saleEnd, productID, productName, licensePlateNr,
    pumpNr, unitPrice, volume, total, idUnitCode, engineHour,
    odoMeter, fleetCode, stationCode, groupCode, groupName.
    """
    event_dt_raw = first(
        row,
        "saleBegin",
        "saleEnd",
        "saleDate",
        "saleDateTime",
        "salesDate",
        "date",
        "createdDate",
        "transactionDate",
        "saleDateStr",
    )

    event_dt = parse_event_dt(event_dt_raw, target_date)
    year_month = event_dt[:7]

    external_id = first(
        row,
        "id",
        "saleId",
        "salesId",
        "transactionId",
        "receiptNo",
        "receipt_no",
        "fisNo",
        "documentNo",
    )

    fuel_type_raw = first(
        row,
        "productName",
        "fuelType",
        "fuelTypeName",
        "product",
        "fuelName",
        "fuel_type",
    )

    liters = to_decimal(first(row, "volume", "liters", "litre", "quantity"))

    unit_price = to_decimal(
        first(
            row,
            "unitPrice",
            "unit_price",
            "pricePerLiter",
        )
    )

    amount = to_decimal(
        first(
            row,
            "total",
            "amount",
            "totalAmount",
            "totalPrice",
            "price",
            "netAmount",
        )
    )

    discount = to_decimal(
        first(
            row,
            "discount",
            "discountAmount",
            "discount_try",
        )
    )

    if amount is None and liters is not None and unit_price is not None:
        amount = liters * unit_price

    return {
        "source": "turpak_full",
        "external_id": to_text(external_id),
        "event_key": make_event_key(row, target_date),
        "event_dt": event_dt,
        "year_month": year_month,
        "plate": to_text(
            first(
                row,
                "licensePlateNr",
                "plate",
                "plateNo",
                "vehiclePlate",
            )
        ),
        "fuel_type_raw": to_text(fuel_type_raw),
        "fuel_type_norm": normalize_fuel_type(fuel_type_raw),
        "liters": liters,
        "unit_price_try": unit_price,
        "amount_try": amount,
        "discount_try": discount,
        "station_code": to_text(
            first(
                row,
                "stationCode",
                "station_code",
            )
        ),
        "station_name": to_text(
            first(
                row,
                "stationName",
                "station_name",
                "station",
                "stationCode",
            )
        ),
        "station_city": to_text(
            first(
                row,
                "stationCity",
                "city",
                "station_city",
            )
        ),
        "receipt_no": to_text(
            first(
                row,
                "receiptNo",
                "receipt_no",
                "fisNo",
                "documentNo",
                "id",
            )
        ),
        "card_no": to_text(
            first(
                row,
                "cardNo",
                "cardNumber",
                "card_no",
                "idUnitCode",
            )
        ),
        "card_type": to_text(
            first(
                row,
                "cardType",
                "card_type",
            )
        ),
        "group_name": to_text(
            first(
                row,
                "groupName",
                "group_name",
            )
        ),
        "odometer": to_decimal(
            first(
                row,
                "odoMeter",
                "odometer",
                "km",
                "kilometer",
            )
        ),
        "sale_type": to_text(
            first(
                row,
                "saleType",
                "sale_type",
            )
        ),
        "department_code": to_text(
            first(
                row,
                "departmentCode",
                "department_code",
                "fleetCode",
            )
        ),
        "raw_payload": json.dumps(row, ensure_ascii=False),
    }


# ============================================================
# TURPAK API
# ============================================================


def fetch_turpak_sales(target_date: str) -> list[dict]:
    token = get_turpak_token()

    headers = CaseInsensitiveDict()
    headers["accept"] = "text/plain"
    headers["Content-Type"] = "application/json"
    headers["authorization"] = "Bearer " + token

    payload = {
        "saleBegin": f"{target_date}T00:00:00.228Z",
        "saleEnd": f"{target_date}T23:59:59.228Z",
        "licensePlateNr": "",
        "groupName": "",  # ВАЖНО: пусто = все группы
    }

    print("=" * 80)
    print("TURPAK REQUEST")
    print("=" * 80)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    response = requests.post(
        TURPAK_URL,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False),
        timeout=120,
    )

    print(f"HTTP STATUS: {response.status_code}")
    response.raise_for_status()

    raw_path = OUT_DIR / f"turpak_raw_{target_date}.json"
    raw_path.write_text(response.text, encoding="utf-8")
    print(f"OK: raw response сохранён: {raw_path}")

    data = response.json()

    sales = data.get("salesList")
    if sales is None:
        print("WARNING: в ответе нет salesList. Ключи ответа:")
        print(list(data.keys()))
        return []

    if not isinstance(sales, list):
        raise RuntimeError(f"salesList не список: {type(sales)}")

    print(f"OK: получено продаж: {len(sales)}")

    if sales:
        print("=" * 80)
        print("КЛЮЧИ ПЕРВОЙ СТРОКИ")
        print("=" * 80)
        print(list(sales[0].keys()))
        print(json.dumps(sales[0], ensure_ascii=False, indent=2)[:3000])

    return sales


# ============================================================
# DATABASE
# ============================================================


def load_rows_to_db(rows: list[dict]) -> None:
    if not rows:
        print("DB: строк нет, загрузка пропущена.")
        return

    try:
        import psycopg2
        from psycopg2.extras import execute_batch
    except ImportError:
        raise RuntimeError("Установи psycopg2-binary: pip install psycopg2-binary")

    conn = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False

        with conn.cursor() as cur:
            execute_batch(cur, INSERT_SQL, rows, page_size=500)

        conn.commit()
        print(f"OK: загружено/обновлено строк: {len(rows)}")

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()


# ============================================================
# CLI
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="Import full Turpak sales to turpak_fuel_events_all"
    )

    parser.add_argument(
        "--date",
        help="Дата загрузки YYYY-MM-DD. Если не указана — предыдущие сутки.",
        default=None,
    )

    parser.add_argument(
        "--date-from",
        help="Дата начала периода YYYY-MM-DD.",
        default=None,
    )

    parser.add_argument(
        "--date-to",
        help="Дата конца периода YYYY-MM-DD.",
        default=None,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только получить и распарсить, без загрузки в БД.",
    )

    return parser.parse_args()


def daterange(date_from: str, date_to: str) -> list[str]:
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end = datetime.strptime(date_to, "%Y-%m-%d").date()

    if end < start:
        raise ValueError("date-to не может быть меньше date-from")

    result = []
    current = start

    while current <= end:
        result.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    return result


def process_one_day(target_date: str, dry_run: bool) -> None:
    print("=" * 80)
    print("TURPAK FULL IMPORT")
    print("=" * 80)
    print(f"Дата загрузки: {target_date}")
    print(f"Dry run: {dry_run}")

    sales = fetch_turpak_sales(target_date)

    rows = [map_sale(row, target_date) for row in sales]

    parsed_path = OUT_DIR / f"turpak_parsed_{target_date}.json"
    parsed_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"OK: parsed rows сохранены: {parsed_path}")

    groups: dict[str, Decimal] = {}
    for row in rows:
        group_name = row.get("group_name") or "Без группы"
        groups[group_name] = groups.get(group_name, Decimal("0")) + (
            row.get("liters") or Decimal("0")
        )

    print("=" * 80)
    print("ИТОГ ПО ГРУППАМ")
    print("=" * 80)

    for group_name, liters in sorted(groups.items()):
        print(f"{group_name}: {round(liters, 3)} л")

    if dry_run:
        print("DRY RUN: загрузка в БД пропущена.")
        return

    load_rows_to_db(rows)


def main():
    args = parse_args()

    if args.date_from or args.date_to:
        if not args.date_from or not args.date_to:
            raise RuntimeError(
                "Для периода нужно указать оба параметра: --date-from и --date-to"
            )

        days = daterange(args.date_from, args.date_to)
    elif args.date:
        days = [args.date]
    else:
        days = [(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")]

    print("=" * 80)
    print("TURPAK FULL IMPORT START")
    print("=" * 80)
    print(f"Дней к загрузке: {len(days)}")
    print(f"Период: {days[0]} — {days[-1]}")
    print(f"Dry run: {args.dry_run}")

    for day in days:
        process_one_day(day, args.dry_run)

    print("=" * 80)
    print("TURPAK FULL IMPORT DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
