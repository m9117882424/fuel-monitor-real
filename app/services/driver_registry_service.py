from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import settings
from ..utils import normalize_plate


PRIMARY_SHEET_NAME = "Список легкового автотранспорта"
SECONDARY_SHEET_NAME = "Подменные Yedekler"


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "plate",
        "vehicle_model",
        "grade",
        "user_name",
        "position",
        "directorate",
        "roster_date",
        "driver_file_name",
        "driver_sheet_name",
    ])


def _canon_col(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("Дирекция /  Directorate", "Дирекция / Directorate")
    return s


def _extract_file_date(path: Path) -> datetime:
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", path.name)
    if not m:
        return datetime.min
    return datetime.strptime(m.group(1), "%d.%m.%Y")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_canon_col(c) for c in df.columns]
    return df


def _find_first_column(columns: list[str], aliases: list[str]) -> str | None:
    canon_aliases = {_canon_col(a) for a in aliases}
    for col in columns:
        if _canon_col(col) in canon_aliases:
            return col
    return None


def _extract_columns(raw: pd.DataFrame) -> pd.DataFrame:
    raw = _normalize_columns(raw)
    columns = list(raw.columns)

    alias_map = {
        "vehicle_model": [
            "Марка, модель / Marka, model",
            "Marka, model",
            "Марка, модель",
        ],
        "plate_raw": [
            "Гос рег знак / PLAKA",
            "PLAKA",
            "Plaka",
            "Гос рег знак",
        ],
        "grade": [
            "Грейд / SCALA",
            "SCALA",
            "Scala",
            "Грейд",
        ],
        "user_name": [
            "Пользователь / KULLANICI",
            "KULLANICI",
            "Kullanıcı",
            "Пользователь",
        ],
        "position": [
            "Должность / GÖREVİ",
            "GÖREVİ",
            "Görevi",
            "Должность",
        ],
        "directorate": [
            "Дирекция / Directorate",
            "Directorate",
            "Дирекция",
        ],
    }

    selected: dict[str, str] = {}
    for target, aliases in alias_map.items():
        found = _find_first_column(columns, aliases)
        if found:
            selected[target] = found

    if "plate_raw" not in selected:
        return _empty_df()

    data = pd.DataFrame()
    for target, source in selected.items():
        data[target] = raw[source]

    for col in ["vehicle_model", "grade", "user_name", "position", "directorate"]:
        if col not in data.columns:
            data[col] = ""

    return data


def _read_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    # Разные листы иногда имеют шапку на разных строках. Пробуем несколько вариантов.
    last_error: Exception | None = None
    for header_row in (2, 1, 0):
        try:
            raw = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
            data = _extract_columns(raw)
            if data.empty and "plate_raw" not in data.columns:
                continue

            data["plate"] = data["plate_raw"].apply(normalize_plate)
            data["roster_date"] = _extract_file_date(path)
            data["driver_file_name"] = path.name
            data["driver_sheet_name"] = sheet_name

            data = data[data["plate"] != ""].copy()
            if data.empty:
                continue

            return data[[
                "plate",
                "vehicle_model",
                "grade",
                "user_name",
                "position",
                "directorate",
                "roster_date",
                "driver_file_name",
                "driver_sheet_name",
            ]]
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        return _empty_df()
    return _empty_df()


def _read_one_driver_file(path: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    sheet_candidates = []
    if getattr(settings, "driver_sheet_name", None):
        sheet_candidates.append(settings.driver_sheet_name)
    for sheet_name in (PRIMARY_SHEET_NAME, SECONDARY_SHEET_NAME):
        if sheet_name not in sheet_candidates:
            sheet_candidates.append(sheet_name)

    for sheet_name in sheet_candidates:
        try:
            df = _read_sheet(path, sheet_name)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue

    if not frames:
        return _empty_df()

    return pd.concat(frames, ignore_index=True)


def load_driver_registry() -> pd.DataFrame:
    """
    Возвращает ИСТОРИЮ разнарядок, а не только последнюю запись по машине.
    Дальше report_service подбирает запись по ближайшей дате к заправке.
    """
    if not settings.driver_enabled:
        return _empty_df()

    if not settings.driver_input_dir:
        return _empty_df()

    base_dir = Path(settings.driver_input_dir)
    if not base_dir.exists():
        return _empty_df()

    files = sorted(base_dir.glob(settings.driver_glob))
    if not files:
        return _empty_df()

    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            frames.append(_read_one_driver_file(path))
        except Exception:
            continue

    if not frames:
        return _empty_df()

    full = pd.concat(frames, ignore_index=True)
    if full.empty:
        return full

    full = full.sort_values([
        "plate",
        "roster_date",
        "driver_file_name",
        "driver_sheet_name",
    ]).reset_index(drop=True)

    return full
