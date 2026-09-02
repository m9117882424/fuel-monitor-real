from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..config import settings
from ..utils import normalize_plate


PRIMARY_SHEET_NAME = "Список легкового автотранспорта"
SECONDARY_SHEET_NAME = "Подменные Yedekler"
SUPPORTED_EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}


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


def _match_key(value) -> str:
    s = _canon_col(value).lower()
    mapping = str.maketrans({
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
        "&": " ",
    })
    s = s.translate(mapping)
    return re.sub(r"[^0-9a-zа-яё]+", " ", s, flags=re.IGNORECASE).strip()


def _extract_file_date(path: Path) -> datetime:
    matches = re.findall(r"(\d{2}\.\d{2}\.\d{4})", path.name)
    if not matches:
        return datetime.min

    # Uploaded files may be saved with an upload timestamp before the original
    # roster name, for example: "02.09.2026_18-45-00 Разнарядка 01.09.2026.xlsx".
    # The roster business date is normally the last date in the file name.
    return datetime.strptime(matches[-1], "%d.%m.%Y")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_canon_col(c) for c in df.columns]
    return df


def _find_first_column(columns: list[str], aliases: list[str]) -> str | None:
    canon_aliases = {_canon_col(a) for a in aliases}
    alias_keys = [_match_key(a) for a in aliases if _match_key(a)]

    for col in columns:
        if _canon_col(col) in canon_aliases:
            return col

    for col in columns:
        col_key = _match_key(col)
        if not col_key:
            continue
        for alias_key in alias_keys:
            if col_key == alias_key or alias_key in col_key:
                return col
    return None


def _extract_columns(raw: pd.DataFrame) -> pd.DataFrame:
    raw = _normalize_columns(raw)
    columns = list(raw.columns)

    alias_map = {
        "vehicle_model": [
            "Марка, модель / Marka, model",
            "MARKA&MODEL / Марка, модель",
            "MARKA MODEL / Марка, модель",
            "Marka, model",
            "MARKA&MODEL",
            "MARKA MODEL",
            "Марка, модель",
        ],
        "plate_raw": [
            "Гос рег знак / PLAKA",
            "PLAKA / гос рег знак",
            "PLAKA / Гос рег знак",
            "PLAKA",
            "Plaka",
            "Гос рег знак",
        ],
        "grade": [
            "Грейд / SCALA",
            "SCALA / Грейд",
            "SCALA",
            "Scala",
            "Грейд",
        ],
        "user_name": [
            "Пользователь / KULLANICI",
            "KULLANICI / Пользователь",
            "KULLANICI",
            "Kullanıcı",
            "Пользователь",
        ],
        "position": [
            "Должность / GÖREVİ",
            "GÖREVİ / Должность",
            "GÖREVİ",
            "Görevi",
            "Должность",
        ],
        "directorate": [
            "Дирекция / Directorate",
            "Directorate / Дирекция",
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


def _read_sheet_from_workbook(workbook: pd.ExcelFile, path: Path, sheet_name: str) -> pd.DataFrame:
    # Разные листы иногда имеют шапку на разных строках. Пробуем несколько вариантов.
    # workbook уже открыт один раз на файл; это существенно быстрее, чем открывать xlsx заново на каждый лист.
    last_error: Exception | None = None
    for header_row in (2, 1, 0):
        try:
            raw = pd.read_excel(workbook, sheet_name=sheet_name, header=header_row)
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


def _read_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    try:
        with pd.ExcelFile(path) as workbook:
            return _read_sheet_from_workbook(workbook, path, sheet_name)
    except Exception:
        return _empty_df()


def _driver_sheet_candidates(workbook: pd.ExcelFile) -> list[str]:
    """
    Return sheets to inspect.

    Priority sheets are checked first for backward compatibility, then every
    other worksheet in the workbook is checked as a safety net. Sheets without
    a recognizable plate column are ignored by _read_sheet_from_workbook().
    """
    sheet_candidates: list[str] = []

    configured_sheet = str(getattr(settings, "driver_sheet_name", "") or "").strip()
    for sheet_name in (configured_sheet, PRIMARY_SHEET_NAME, SECONDARY_SHEET_NAME):
        if sheet_name and sheet_name in workbook.sheet_names and sheet_name not in sheet_candidates:
            sheet_candidates.append(sheet_name)

    for sheet_name in workbook.sheet_names:
        if sheet_name and sheet_name not in sheet_candidates:
            sheet_candidates.append(sheet_name)

    return sheet_candidates


def _read_one_driver_file(path: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    if path.name.startswith("~$"):
        return _empty_df()

    if path.suffix.lower() not in SUPPORTED_EXCEL_EXTENSIONS:
        return _empty_df()

    try:
        with pd.ExcelFile(path) as workbook:
            for sheet_name in _driver_sheet_candidates(workbook):
                try:
                    df = _read_sheet_from_workbook(workbook, path, sheet_name)
                    if not df.empty:
                        frames.append(df)
                except Exception:
                    continue
    except Exception:
        return _empty_df()

    if not frames:
        return _empty_df()

    return pd.concat(frames, ignore_index=True)


def _driver_files(base_dir: Path, glob_pattern: str) -> list[Path]:
    if not base_dir.exists():
        return []

    files: list[Path] = []
    for path in sorted(base_dir.glob(glob_pattern)):
        if path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in SUPPORTED_EXCEL_EXTENSIONS:
            continue
        if not path.is_file():
            continue
        files.append(path)
    return files


def _file_sort_key(path: Path) -> tuple[datetime, int, str]:
    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        mtime = 0
    return (_extract_file_date(path), mtime, path.name)


def _signature_from_paths(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    signature = []
    for path in sorted(paths, key=_file_sort_key):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), int(stat.st_mtime), int(stat.st_size)))
    return tuple(signature)


def _latest_driver_file(base_dir: Path, glob_pattern: str) -> Path | None:
    files = _driver_files(base_dir, glob_pattern)
    if not files:
        return None
    return max(files, key=_file_sort_key)


def _driver_files_signature(base_dir: Path, glob_pattern: str) -> tuple[tuple[str, int, int], ...]:
    """
    Cache key based on file names, mtimes and sizes.
    If a roster file is replaced or edited, the key changes and cache refreshes automatically.
    """
    return _signature_from_paths(_driver_files(base_dir, glob_pattern))


def _driver_files_signature_latest(base_dir: Path, glob_pattern: str) -> tuple[tuple[str, int, int], ...]:
    latest = _latest_driver_file(base_dir, glob_pattern)
    if latest is None:
        return tuple()
    return _signature_from_paths([latest])


def _driver_files_signature_for_month(
    base_dir: Path,
    glob_pattern: str,
    year_month: str,
) -> tuple[tuple[str, int, int], ...]:
    """
    Return a focused file set for a monthly dashboard.

    Roster files are daily, so loading the full archive is expensive. For a
    requested month we load files dated inside that month plus the latest file
    before the month start, so early-month events can still use the last known
    roster when no same-day file exists.
    """
    files = _driver_files(base_dir, glob_pattern)
    if not files:
        return tuple()

    try:
        month_start = pd.Timestamp(f"{year_month}-01").to_pydatetime()
        month_end = (pd.Timestamp(month_start) + pd.offsets.MonthBegin(1)).to_pydatetime()
    except Exception:
        return _driver_files_signature_latest(base_dir, glob_pattern)

    dated: list[tuple[datetime, Path]] = [
        (_extract_file_date(path), path)
        for path in files
        if _extract_file_date(path) != datetime.min
    ]
    if not dated:
        return _driver_files_signature_latest(base_dir, glob_pattern)

    selected: list[Path] = [
        path for file_date, path in dated
        if month_start <= file_date < month_end
    ]

    before = [item for item in dated if item[0] < month_start]
    if before:
        selected.append(max(before, key=lambda item: _file_sort_key(item[1]))[1])

    if not selected:
        return _driver_files_signature_latest(base_dir, glob_pattern)

    # de-duplicate while preserving deterministic order
    unique: dict[str, Path] = {str(path): path for path in selected}
    return _signature_from_paths(list(unique.values()))


@lru_cache(maxsize=16)
def _load_driver_registry_cached(
    driver_enabled: bool,
    driver_input_dir: str,
    driver_glob: str,
    driver_sheet_name: str,
    files_signature: tuple[tuple[str, int, int], ...],
) -> pd.DataFrame:
    """
    Возвращает историю разнарядок по набору файлов из files_signature.
    Сами наборы файлов формируются отдельно: весь архив, последний файл или месяц.
    """
    if not driver_enabled:
        return _empty_df()

    if not driver_input_dir:
        return _empty_df()

    base_dir = Path(driver_input_dir)
    if not base_dir.exists():
        return _empty_df()

    files = [Path(item[0]) for item in files_signature]
    if not files:
        return _empty_df()

    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            df = _read_one_driver_file(path)
            if not df.empty:
                frames.append(df)
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


def read_driver_file(path: str | Path) -> pd.DataFrame:
    """Read one roster Excel file and all usable sheets inside it."""
    return _read_one_driver_file(Path(path))


def load_driver_registry() -> pd.DataFrame:
    base_dir = Path(settings.driver_input_dir) if settings.driver_input_dir else Path("")
    files_signature = _driver_files_signature(base_dir, settings.driver_glob) if settings.driver_input_dir else tuple()

    # Return a copy so downstream code can mutate columns without polluting cached data.
    return _load_driver_registry_cached(
        bool(settings.driver_enabled),
        str(settings.driver_input_dir or ""),
        str(settings.driver_glob or "*.xlsx"),
        str(settings.driver_sheet_name or PRIMARY_SHEET_NAME),
        files_signature,
    ).copy()


def load_latest_driver_registry() -> pd.DataFrame:
    base_dir = Path(settings.driver_input_dir) if settings.driver_input_dir else Path("")
    files_signature = _driver_files_signature_latest(base_dir, settings.driver_glob) if settings.driver_input_dir else tuple()

    return _load_driver_registry_cached(
        bool(settings.driver_enabled),
        str(settings.driver_input_dir or ""),
        str(settings.driver_glob or "*.xlsx"),
        str(settings.driver_sheet_name or PRIMARY_SHEET_NAME),
        files_signature,
    ).copy()


def load_driver_registry_for_month(year_month: str) -> pd.DataFrame:
    base_dir = Path(settings.driver_input_dir) if settings.driver_input_dir else Path("")
    files_signature = (
        _driver_files_signature_for_month(base_dir, settings.driver_glob, year_month)
        if settings.driver_input_dir
        else tuple()
    )

    return _load_driver_registry_cached(
        bool(settings.driver_enabled),
        str(settings.driver_input_dir or ""),
        str(settings.driver_glob or "*.xlsx"),
        str(settings.driver_sheet_name or PRIMARY_SHEET_NAME),
        files_signature,
    ).copy()


def clear_driver_registry_cache() -> None:
    _load_driver_registry_cached.cache_clear()
