#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Адаптивное чтение заправок из разных схем Fuel Monitor."""
from __future__ import annotations

import re
from datetime import date, datetime, time as dt_time, timedelta
from typing import Iterable

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

import vehicle_mileage_fuel_report as core


COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "source": (
        "source",
        "source_name",
        "fuel_source",
        "provider",
        "provider_name",
        "system",
        "system_name",
        "data_source",
    ),
    "event_dt": (
        "event_dt",
        "event_time",
        "event_datetime",
        "transaction_dt",
        "transaction_time",
        "transaction_datetime",
        "sale_dt",
        "sale_date",
        "fuel_dt",
        "fuel_date",
        "datetime",
        "date_time",
        "date",
        "day",
    ),
    "plate": (
        "plate",
        "normalized_plate",
        "vehicle_plate",
        "license_plate",
        "licenseplate",
        "plate_no",
        "plaka",
        "vehicle_no",
        "vehicle",
    ),
    "liters": (
        "liters",
        "litres",
        "liter",
        "litre",
        "volume",
        "volume_l",
        "quantity",
        "quantity_liters",
        "amount_liters",
        "total_liters",
        "total_liter",
    ),
    "station_code": (
        "station_code",
        "station_id",
        "dealer_code",
        "site_code",
        "location_code",
    ),
    "station_name": (
        "station_name",
        "station",
        "dealer_name",
        "site_name",
        "location_name",
    ),
    "group_name": (
        "group_name",
        "vehicle_group",
        "department_name",
        "department",
        "group",
    ),
}

WIDE_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "turpak": (
        "turpak",
        "turpak_liters",
        "turpak_litres",
        "liters_turpak",
        "turpak_volume",
    ),
    "shell": (
        "shell",
        "shell_liters",
        "shell_litres",
        "liters_shell",
        "shell_volume",
    ),
    "petrol": (
        "petrol",
        "petrol_liters",
        "petrol_litres",
        "liters_petrol",
        "petrol_volume",
    ),
}


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def pick_column(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    lookup = {normalized_name(column): column for column in columns}
    for alias in aliases:
        selected = lookup.get(normalized_name(alias))
        if selected is not None:
            return selected
    return None


def quoted(engine: Engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)


def relation_columns(engine: Engine, relation: str) -> list[str]:
    return [str(column["name"]) for column in inspect(engine).get_columns(relation)]


def inferred_source(relation: str) -> str | None:
    lowered = relation.lower()
    for source in ("turpak", "shell", "petrol"):
        if source in lowered:
            return source
    return None


def empty_relation_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source",
            "event_dt",
            "plate",
            "liters",
            "station_code",
            "station_name",
            "group_name",
        ]
    )


def read_fuel_relation(
    engine: Engine,
    relation: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    columns = relation_columns(engine, relation)
    selected = {
        canonical: pick_column(columns, aliases)
        for canonical, aliases in COLUMN_ALIASES.items()
    }
    wide_selected = {
        source: pick_column(columns, aliases)
        for source, aliases in WIDE_SOURCE_ALIASES.items()
    }

    if selected["event_dt"] is None:
        raise RuntimeError(
            f"В {relation} не найдена дата операции. Колонки: {', '.join(columns)}"
        )
    if selected["plate"] is None:
        raise RuntimeError(
            f"В {relation} не найден госномер. Колонки: {', '.join(columns)}"
        )
    if selected["liters"] is None and not any(wide_selected.values()):
        raise RuntimeError(
            f"В {relation} не найден объём топлива. Колонки: {', '.join(columns)}"
        )

    needed = {
        column
        for column in [*selected.values(), *wide_selected.values()]
        if column is not None
    }
    select_sql = ", ".join(quoted(engine, column) for column in sorted(needed))
    relation_sql = quoted(engine, relation)
    event_sql = quoted(engine, selected["event_dt"])
    query = text(
        f"SELECT {select_sql} FROM {relation_sql} "
        f"WHERE {event_sql} >= :start_dt AND {event_sql} < :end_dt"
    )
    raw = pd.read_sql_query(
        query,
        engine,
        params={"start_dt": start, "end_dt": end},
    )
    if raw.empty:
        return empty_relation_frame()

    base = pd.DataFrame(index=raw.index)
    for canonical in ("event_dt", "plate", "station_code", "station_name", "group_name"):
        column = selected[canonical]
        base[canonical] = raw[column] if column is not None else ""

    source_column = selected["source"]
    liters_column = selected["liters"]
    frames: list[pd.DataFrame] = []

    if liters_column is not None:
        frame = base.copy()
        frame["liters"] = raw[liters_column]
        if source_column is not None:
            frame["source"] = raw[source_column]
        else:
            source = inferred_source(relation)
            if source is None:
                raise RuntimeError(
                    f"В {relation} не найден источник топлива. "
                    f"Колонки: {', '.join(columns)}"
                )
            frame["source"] = source
        frames.append(frame)
    else:
        for source, column in wide_selected.items():
            if column is None:
                continue
            frame = base.copy()
            frame["source"] = source
            frame["liters"] = raw[column]
            frames.append(frame)

    return pd.concat(frames, ignore_index=True) if frames else empty_relation_frame()


def source_mask(series: pd.Series, names: set[str]) -> pd.Series:
    raw = series.fillna("").astype(str).str.lower()
    mask = pd.Series(False, index=series.index)
    for name in names:
        mask |= raw.str.contains(name, na=False)
    return mask


def get_fuel(
    engine: Engine,
    start: date,
    end: date,
    plates: set[str],
) -> tuple[pd.DataFrame, str]:
    start_dt = datetime.combine(start, dt_time.min)
    end_dt = datetime.combine(end + timedelta(days=1), dt_time.min)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    views = set(inspector.get_view_names())
    relations = tables | views

    frames: list[pd.DataFrame] = []
    description = ""

    if "fuel_three_sources_v" in relations:
        try:
            frames = [
                read_fuel_relation(
                    engine,
                    "fuel_three_sources_v",
                    start_dt,
                    end_dt,
                )
            ]
            description = "fuel_three_sources_v"
        except Exception as exc:
            print(f"fuel_three_sources_v пропущено: {exc}")

    if not frames:
        if "fuel_events" not in tables:
            raise RuntimeError(
                "Не удалось прочитать fuel_three_sources_v и отсутствует fuel_events."
            )

        common = read_fuel_relation(engine, "fuel_events", start_dt, end_dt)
        frames.append(common[source_mask(common["source"], {"shell", "petrol"})])

        if "turpak_fuel_events_all" in relations:
            frames.append(
                read_fuel_relation(
                    engine,
                    "turpak_fuel_events_all",
                    start_dt,
                    end_dt,
                )
            )
            description = "fuel_events + turpak_fuel_events_all"
        else:
            frames.append(common[source_mask(common["source"], {"turpak"})])
            description = "fuel_events"

    fuel = pd.concat(frames, ignore_index=True) if frames else empty_relation_frame()
    if fuel.empty:
        return fuel.assign(date=pd.NaT, source_name=""), description

    for column in ("station_code", "station_name", "group_name"):
        if column not in fuel:
            fuel[column] = ""

    fuel["plate"] = fuel["plate"].apply(core.norm_plate)
    fuel["event_dt"] = fuel["event_dt"].apply(core.local_dt)
    fuel["date"] = pd.to_datetime(fuel["event_dt"], errors="coerce").dt.normalize()
    fuel["liters"] = fuel["liters"].apply(core.number)

    raw_source = fuel["source"].fillna("").astype(str).str.lower()
    fuel["source_name"] = ""
    fuel.loc[raw_source.str.contains("turpak", na=False), "source_name"] = "Turpak"
    fuel.loc[raw_source.str.contains("shell", na=False), "source_name"] = "Shell"
    fuel.loc[raw_source.str.contains("petrol", na=False), "source_name"] = "Petrol"

    fuel = fuel[
        fuel["plate"].isin(plates)
        & fuel["date"].notna()
        & fuel["source_name"].isin(core.SOURCES)
        & fuel["liters"].gt(0)
    ].copy()
    fuel = fuel.drop_duplicates(
        [
            "source_name",
            "event_dt",
            "plate",
            "liters",
            "station_code",
            "station_name",
        ]
    )
    return fuel, description
