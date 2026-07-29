#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пробег Arvento по группе через GeneralReportWithDistance + топливо."""
from __future__ import annotations

import argparse
import os
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import vehicle_mileage_fuel_report as core

GENERAL_METHOD = "GeneralReportWithDistance"
GENERAL_URL = f"{core.ARVENTO_URL}/{GENERAL_METHOD}"
GENERAL_HEADERS = {
    **core.HEADERS,
    "Referer": f"{core.ARVENTO_URL}?op={GENERAL_METHOD}",
}


def xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in element:
        if xml_local_name(child.tag) in wanted:
            return (child.text or "").strip()
    return ""


def extract_general_xml(response_text: str) -> str:
    positions = [
        pos
        for marker in ("<DataSet", "<?xml")
        if (pos := response_text.find(marker)) >= 0
    ]
    if not positions:
        preview = " ".join(response_text[:500].split())
        raise RuntimeError(f"Ответ GeneralReportWithDistance не содержит DataSet: {preview}")
    return response_text[min(positions):]


def parse_general_rows(response_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(extract_general_xml(response_text))
    except ET.ParseError as exc:
        preview = " ".join(response_text[:500].split())
        raise RuntimeError(f"Некорректный XML GeneralReportWithDistance: {preview}") from exc

    errors: list[str] = []
    for element in root.iter():
        if xml_local_name(element.tag) in {"Error", "e", "ErrorMessage"}:
            value = (element.text or "").strip()
            if value:
                errors.append(value)
    if errors:
        raise RuntimeError("Ошибка Arvento: " + "; ".join(dict.fromkeys(errors)))

    rows: list[dict[str, Any]] = []
    for element in root.iter():
        if xml_local_name(element.tag) != "General_x0020_Report":
            continue

        plate_raw = text_of(element, "License_x0020_Plate", "Plaka")
        plate = core.norm_plate(plate_raw)
        event_raw = text_of(element, "Date_x002F_Time", "Tarih_x002F_Saat")
        event_dt = core.local_dt(event_raw)
        if not plate or pd.isna(event_dt):
            continue

        distance = core.number(text_of(element, "Distance", "Mesafe"))
        rows.append(
            {
                "timestamp": pd.Timestamp(event_dt),
                "plate": plate,
                "plate_display": str(plate_raw).strip().upper() or plate,
                "km": max(distance, 0.0),
                "driver": text_of(element, "Driver", "Surucu", "Sürücü"),
                "node": text_of(element, "Device_x0020_No", "Cihaz_x0020_No"),
                "latitude": text_of(element, "Latitude"),
                "longitude": text_of(element, "Longitude"),
                "event_type": text_of(element, "Type"),
            }
        )
    return rows


def general_payload(
    cred: core.Credentials,
    group: str,
    start_dt: datetime,
    end_dt: datetime,
    minute_dif: int,
) -> dict[str, str]:
    return {
        "Username": cred.username,
        "PIN1": cred.pin1,
        "PIN2": cred.pin2,
        "StartDate": start_dt.strftime(core.DATE_FMT),
        "EndDate": end_dt.strftime(core.DATE_FMT),
        "Node": "",
        "Group": group,
        "Compress": "",
        "chkLocation": "1",
        "chkSpeed": "",
        "chkPause": "",
        "chkMotion": "",
        "chkRegion": "",
        "txtSpeedMin": "",
        "txtSpeedMax": "",
        "chkTemperatureSensor1": "",
        "chkTemperatureSensorPer1": "",
        "chkTemperatureSensorAlm1": "",
        "chkTemperatureSensor2": "",
        "chkTemperatureSensorPer2": "",
        "chkTemperatureSensorAlm2": "",
        "chkTemperatureSensor3": "",
        "chkTemperatureSensorPer3": "",
        "chkTemperatureSensorAlm3": "",
        "chkTemperatureSensor4": "",
        "chkTemperatureSensorPer4": "",
        "chkTemperatureSensorAlm4": "",
        "txtTemperatureMin": "",
        "txtTemperatureMax": "",
        "chkEmergency": "",
        "chkDoor": "",
        "chkPauseTime": "",
        "chkContactAlarm": "1",
        "chkIdlingTime": "1",
        "chkIdlingAlarm": "",
        "chkFuelLevel": "",
        "chkPower": "",
        "chkDriverIdentification": "",
        "chkHumiditySensor1": "",
        "chkHumiditySensor2": "",
        "chkHumiditySensor3": "",
        "chkHumiditySensor4": "",
        "chkPossibleAccident": "",
        "chkAcceleration": "",
        "chkVehicleMovedWithoutDriverCard": "",
        "MinuteDif": str(minute_dif),
        "Language": "1",
    }


def request_general_chunk(
    session: requests.Session,
    cred: core.Credentials,
    group: str,
    start_dt: datetime,
    end_dt: datetime,
    *,
    minute_dif: int,
    timeout: int,
    retries: int,
) -> list[dict[str, Any]]:
    payload = general_payload(cred, group, start_dt, end_dt, minute_dif)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = session.post(
                GENERAL_URL,
                data=payload,
                headers=GENERAL_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            return parse_general_rows(response.text)
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if "access denied" in str(exc).lower():
                raise RuntimeError(
                    "Access denied и для GeneralReportWithDistance. "
                    "Проверьте, что используются те же ARVENTO_USER/PIN1/PIN2, "
                    "что в рабочем arvento-kpp-report."
                ) from exc
            if attempt < retries:
                delay = min(attempt * 3, 10)
                print(f"    повтор через {delay} сек.: {exc}")
                time.sleep(delay)

    raise RuntimeError(str(last_error))


def fetch_adaptive_chunk(
    session: requests.Session,
    cred: core.Credentials,
    group: str,
    start_dt: datetime,
    end_dt: datetime,
    *,
    minute_dif: int,
    timeout: int,
    retries: int,
    min_chunk_minutes: int,
) -> list[dict[str, Any]]:
    try:
        return request_general_chunk(
            session,
            cred,
            group,
            start_dt,
            end_dt,
            minute_dif=minute_dif,
            timeout=timeout,
            retries=retries,
        )
    except RuntimeError as exc:
        duration_minutes = int((end_dt - start_dt).total_seconds() // 60)
        if "access denied" in str(exc).lower() or duration_minutes <= min_chunk_minutes:
            raise

        half_seconds = int((end_dt - start_dt).total_seconds() // 2)
        middle = start_dt + timedelta(seconds=half_seconds)
        print(
            f"    чанк {start_dt:%H:%M}–{end_dt:%H:%M} слишком большой; "
            f"делю на {start_dt:%H:%M}–{middle:%H:%M} и "
            f"{middle:%H:%M}–{end_dt:%H:%M}"
        )
        left = fetch_adaptive_chunk(
            session,
            cred,
            group,
            start_dt,
            middle,
            minute_dif=minute_dif,
            timeout=timeout,
            retries=retries,
            min_chunk_minutes=min_chunk_minutes,
        )
        right = fetch_adaptive_chunk(
            session,
            cred,
            group,
            middle,
            end_dt,
            minute_dif=minute_dif,
            timeout=timeout,
            retries=retries,
            min_chunk_minutes=min_chunk_minutes,
        )
        return left + right


def get_group_mileage(
    session: requests.Session,
    cred: core.Credentials,
    group: str,
    start: date,
    end: date,
    *,
    initial_chunk_hours: int,
    min_chunk_minutes: int,
    minute_dif: int,
    timeout: int,
    retries: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    records: list[dict[str, Any]] = []
    current_day = start
    total_days = (end - start).days + 1
    day_index = 0

    while current_day <= end:
        day_index += 1
        day_start = datetime.combine(current_day, dt_time.min)
        day_end = day_start + timedelta(days=1)
        print(f"Arvento {day_index}/{total_days}: {current_day:%d.%m.%Y}")

        chunk_start = day_start
        while chunk_start < day_end:
            chunk_end = min(chunk_start + timedelta(hours=initial_chunk_hours), day_end)
            chunk_rows = fetch_adaptive_chunk(
                session,
                cred,
                group,
                chunk_start,
                chunk_end,
                minute_dif=minute_dif,
                timeout=timeout,
                retries=retries,
                min_chunk_minutes=min_chunk_minutes,
            )
            print(f"  {chunk_start:%H:%M}–{chunk_end:%H:%M}: строк={len(chunk_rows)}")
            records.extend(chunk_rows)
            chunk_start = chunk_end

        current_day += timedelta(days=1)

    if not records:
        return core.empty_mileage(), pd.DataFrame(
            columns=["plate", "display", "driver", "node"]
        )

    raw = pd.DataFrame(records)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce")
    raw = raw[raw["timestamp"].notna() & raw["plate"].astype(str).ne("")].copy()
    raw["date"] = raw["timestamp"].dt.normalize()
    raw = raw.drop_duplicates(
        subset=[
            "plate",
            "timestamp",
            "node",
            "latitude",
            "longitude",
            "event_type",
            "km",
        ],
        keep="first",
    )

    mileage = (
        raw.groupby(["date", "plate"], as_index=False)
        .agg(
            km=("km", "sum"),
            driver=("driver", lambda s: next((str(v) for v in s if str(v).strip()), "")),
            node=("node", lambda s: next((str(v) for v in s if str(v).strip()), "")),
        )
        .sort_values(["date", "plate"])
        .reset_index(drop=True)
    )

    vehicles = (
        raw.sort_values("timestamp")
        .groupby("plate", as_index=False)
        .agg(
            display=("plate_display", "first"),
            driver=("driver", lambda s: next((str(v) for v in s if str(v).strip()), "")),
            node=("node", lambda s: next((str(v) for v in s if str(v).strip()), "")),
        )
        .sort_values("plate")
        .reset_index(drop=True)
    )
    return mileage, vehicles


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Пробег GeneralReportWithDistance по группе + Turpak/Shell/Petrol"
    )
    parser.add_argument("--date-from", type=core.parse_date)
    parser.add_argument("--date-to", type=core.parse_date)
    parser.add_argument("--group")
    parser.add_argument("--output")
    parser.add_argument(
        "--arvento-chunk-hours",
        type=int,
        default=int(os.getenv("ARVENTO_CHUNK_HOURS", "24")),
        help="Начальный размер чанка в часах; при ошибке делится автоматически",
    )
    parser.add_argument(
        "--arvento-min-chunk-minutes",
        type=int,
        default=int(os.getenv("ARVENTO_MIN_CHUNK_MINUTES", "60")),
    )
    parser.add_argument(
        "--arvento-minute-dif",
        type=int,
        default=int(os.getenv("ARVENTO_MINUTE_DIF", "180")),
    )
    parser.add_argument(
        "--arvento-timeout",
        type=int,
        default=int(os.getenv("ARVENTO_HTTP_TIMEOUT", "180")),
    )
    parser.add_argument(
        "--arvento-retries",
        type=int,
        default=int(os.getenv("ARVENTO_HTTP_RETRIES", "3")),
    )
    parser.add_argument("--sync-fuel", action="store_true")
    args = parser.parse_args()

    today = date.today()
    start = args.date_from or today.replace(day=1)
    end = args.date_to or today
    if end < start:
        parser.error("--date-to раньше --date-from")
    if args.arvento_chunk_hours < 1:
        parser.error("--arvento-chunk-hours должен быть не меньше 1")
    if args.arvento_min_chunk_minutes < 15:
        parser.error("--arvento-min-chunk-minutes должен быть не меньше 15")

    group = (args.group or os.getenv("ARVENTO_GROUP") or input("Группа Arvento: ")).strip()
    if not group:
        parser.error("Не указана группа")

    output = Path(
        args.output or f"mileage_fuel_{group}_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
    ).resolve()
    cred = core.credentials()

    print(
        f"Группа: {group}; период: {start:%d.%m.%Y}—{end:%d.%m.%Y}; "
        f"источник: {GENERAL_METHOD}"
    )
    with requests.Session() as session:
        mileage, vehicles = get_group_mileage(
            session,
            cred,
            group,
            start,
            end,
            initial_chunk_hours=args.arvento_chunk_hours,
            min_chunk_minutes=args.arvento_min_chunk_minutes,
            minute_dif=args.arvento_minute_dif,
            timeout=args.arvento_timeout,
            retries=args.arvento_retries,
        )

    if vehicles.empty:
        raise RuntimeError(
            f"GeneralReportWithDistance не вернул автомобили группы {group!r} "
            f"за {start:%d.%m.%Y}—{end:%d.%m.%Y}."
        )
    plates = set(vehicles["plate"])

    if args.sync_fuel:
        from app.services.sync_service import sync_all

        db = core.SessionLocal()
        try:
            sync_all(db, build_report=False, send_report=False)
        finally:
            db.close()

    fuel, source = core.get_fuel(core.engine, start, end, plates)
    summary, detail = core.reports(vehicles, mileage, fuel, start, end)
    core.write_excel(summary, detail, output)
    print(f"Автомобилей: {len(vehicles)}; источник топлива: {source}; Excel: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
