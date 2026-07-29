#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск отчёта без GetNodes: автомобили берутся из отчёта по пробегам."""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import vehicle_mileage_fuel_report as core


def vehicles_from_mileage(mileage: pd.DataFrame, group: str) -> pd.DataFrame:
    if mileage.empty:
        raise RuntimeError(
            f"Отчёт по пробегам не вернул автомобили для группы {group!r}. "
            "Проверьте название группы, период и доступ к VehicleDistanceReport."
        )
    vehicles = (
        mileage.sort_values(["plate", "date"])
        .groupby("plate", as_index=False)
        .agg(driver=("driver", "first"), node=("node", "first"))
    )
    vehicles["display"] = vehicles["plate"]
    return vehicles[["plate", "display", "driver", "node"]].sort_values("plate").reset_index(drop=True)


def get_group_mileage(
    session: requests.Session,
    cred: core.Credentials,
    group: str,
    start: date,
    end: date,
    mode: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    intervals = list(core.chunks(start, end, mode))
    detailed_allowed = mode != "daily"

    for index, (left, right) in enumerate(intervals, 1):
        print(f"Arvento {index}/{len(intervals)}: {left:%d.%m.%Y} — {right:%d.%m.%Y}")
        frame = core.empty_mileage()

        if detailed_allowed:
            try:
                frame = core.detailed_block(session, cred, group, left, right)
            except RuntimeError as exc:
                print(f"  VehicleDistanceReport2 недоступен: {exc}")
                if "access denied" in str(exc).lower():
                    detailed_allowed = False
                    print("  Дальше используется разрешённый VehicleDistanceReport с параметром Group.")

        if frame.empty:
            print("  Получаю дневной пробег через VehicleDistanceReport.")
            frame = core.daily_block(session, cred, group, left, right)

        if not frame.empty:
            frames.append(frame)

    if not frames:
        return core.empty_mileage()
    return core.aggregate(pd.concat(frames, ignore_index=True).to_dict("records"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Пробег Arvento по группе + Turpak/Shell/Petrol")
    parser.add_argument("--date-from", type=core.parse_date)
    parser.add_argument("--date-to", type=core.parse_date)
    parser.add_argument("--group")
    parser.add_argument("--output")
    parser.add_argument(
        "--arvento-chunk-mode",
        choices=("14d", "month", "daily"),
        default=os.getenv("ARVENTO_CHUNK_MODE", "14d"),
    )
    parser.add_argument("--sync-fuel", action="store_true")
    args = parser.parse_args()

    today = date.today()
    start = args.date_from or today.replace(day=1)
    end = args.date_to or today
    if end < start:
        parser.error("--date-to раньше --date-from")

    group = (args.group or os.getenv("ARVENTO_GROUP") or input("Группа Arvento: ")).strip()
    if not group:
        parser.error("Не указана группа")

    output = Path(
        args.output or f"mileage_fuel_{group}_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
    ).resolve()
    cred = core.credentials()
    session = requests.Session()

    print(
        f"Группа: {group}; период: {start:%d.%m.%Y}—{end:%d.%m.%Y}; "
        f"режим: {args.arvento_chunk_mode}"
    )
    mileage = get_group_mileage(
        session, cred, group, start, end, args.arvento_chunk_mode
    )
    vehicles = vehicles_from_mileage(mileage, group)
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
