from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Body, Cookie, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .models import FuelEvent, ImportRun, VehicleLimit
from .schemas import HealthResponse, LimitUpsert, SyncResult, SyncRunResponse
from .services.alert_service import refresh_alert_state
from .services.driver_registry_service import load_driver_registry
from .services.storage import upsert_limits
from .services.summary_service import build_monthly_vehicle_summary, fetch_events
from .services.sync_service import sync_all
from .utils import current_year_month, normalize_plate, now_local

app = FastAPI(title=settings.app_name)
Base.metadata.create_all(bind=engine)

LIMITS_COOKIE_NAME = "limits_admin_session"

SHELL_UPLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv"}
SHELL_UPLOAD_MAX_SIZE_BYTES = 25 * 1024 * 1024
SHELL_SOURCE_NAME = "shell_excel"


def _setting_value(*names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(settings, name):
            value = getattr(settings, name)
            if value not in (None, ""):
                return value
    return default


def _resolve_shell_upload_dir() -> Path:
    configured = _setting_value(
        "shell_excel_input_path",
        "shell_excel_path",
        "shell_upload_path",
        "shell_input_path",
        "shell_source_path",
        "shell_watch_path",
        "shell_dir",
        "cards_input_path",
        default=None,
    )
    if configured:
        path = Path(str(configured)).expanduser()
    else:
        path = Path("/root/fuel_monitor_real/data/shell")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_search_text(value: Any) -> str:
    text = str(value or "").strip().lower()
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
    })
    text = text.translate(mapping)
    text = text.replace("_", " ")
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def _is_allowed_shell_filename(filename: str | None) -> bool:
    return Path(str(filename or "")).suffix.lower() in SHELL_UPLOAD_EXTENSIONS


def _save_shell_upload(file: UploadFile) -> Path:
    original_name = file.filename or "shell_upload"
    suffix = Path(original_name).suffix.lower()
    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    target = _resolve_shell_upload_dir() / f"shell_{stamp}{suffix}"
    file.file.seek(0)
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return target


def _archive_processed_shell_file(file_path: Path) -> Path | None:
    try:
        archive_dir = file_path.parent.parent / f"{file_path.parent.name}_processed"
        archive_dir.mkdir(parents=True, exist_ok=True)

        target = archive_dir / file_path.name
        if target.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            stamp = now_local().strftime("%Y%m%d_%H%M%S")
            target = archive_dir / f"{stem}_{stamp}{suffix}"

        return file_path.rename(target)
    except Exception:
        return None


def _read_shell_dataframe(file_path: Path) -> pd.DataFrame:
    suffix = file_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path)

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1254", "latin1"):
        try:
            return pd.read_csv(file_path, sep=None, engine="python", encoding=encoding)
        except Exception as exc:  # pragma: no cover - fallback path
            last_error = exc
    raise ValueError(f"Не удалось прочитать CSV: {last_error}")


def _find_first_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized = {_normalize_search_text(col): col for col in columns}
    alias_norms = [_normalize_search_text(alias) for alias in aliases]

    for alias in alias_norms:
        if alias in normalized:
            return normalized[alias]

    for norm, original in normalized.items():
        for alias in alias_norms:
            if alias and alias in norm:
                return original
    return None


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    return text or None


def _normalize_identifier_text(value: Any) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    compact = text.replace("\xa0", "").replace(" ", "")
    if re.fullmatch(r"-?\d+\.0+", compact):
        compact = compact.split(".", 1)[0]
    elif re.fullmatch(r"-?\d+", compact):
        pass
    elif re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        try:
            numeric = float(compact)
            if numeric.is_integer():
                compact = str(int(numeric))
        except Exception:
            pass
    return compact or None


def _canonical_text(value: Any) -> str:
    text = _safe_text(value) or ""
    return re.sub(r"\s+", " ", text).strip().lower()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("\xa0", "").replace(" ", "")
    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        text = text.replace(".", "").replace(",", ".")

    text = re.sub(r"[^0-9\.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None
    try:
        return float(text)
    except Exception:
        return None




def _stable_number_str(value: Any, decimals: int = 3) -> str:
    number = _safe_float(value)
    if number is None:
        return ""
    return f"{number:.{decimals}f}"


def _normalize_fuel_type_name(value: Any) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    normalized = _normalize_search_text(text)
    if "motorin" in normalized or "diesel" in normalized:
        return "motorin"
    if "95" in normalized or "benzin" in normalized or "kursunsuz" in normalized or "kurşunsuz" in str(text).lower():
        return "gasoline_95"
    return normalized.replace(" ", "_")


def _safe_datetime(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    dt = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        return None
    return dt.to_pydatetime()


def _column_name_set(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _dedupe_shell_events(db: Session, year_month: str | None = None) -> int:
    fuel_event_columns = _column_name_set(FuelEvent)
    if "source" not in fuel_event_columns:
        return 0

    query = db.query(FuelEvent).filter(getattr(FuelEvent, "source") == SHELL_SOURCE_NAME)
    if year_month and "year_month" in fuel_event_columns:
        query = query.filter(getattr(FuelEvent, "year_month") == year_month)

    rows = query.all()
    if not rows:
        return 0

    seen: set[str] = set()
    duplicates: list[FuelEvent] = []

    def row_key(row: FuelEvent) -> str:
        plate = normalize_plate(getattr(row, "plate", None) or "")
        event_dt = getattr(row, "event_dt", None)
        event_dt_text = event_dt.strftime("%Y-%m-%d %H:%M:%S") if event_dt else ""
        liters = _stable_number_str(getattr(row, "liters", None), 3)
        amount = _stable_number_str(getattr(row, "amount_try", None), 2)
        fuel_raw = getattr(row, "fuel_type_raw", None)
        fuel_norm = getattr(row, "fuel_type_norm", None) or _normalize_fuel_type_name(fuel_raw) or ""
        station_code = _normalize_identifier_text(getattr(row, "station_code", None)) or ""
        station_name = _canonical_text(getattr(row, "station_name", None))
        card_no = _normalize_identifier_text(getattr(row, "card_no", None)) or ""
        receipt_no = _normalize_identifier_text(getattr(row, "receipt_no", None)) or ""

        return hashlib.sha256(
            "|".join(
                [
                    SHELL_SOURCE_NAME,
                    plate,
                    event_dt_text,
                    liters,
                    amount,
                    str(fuel_norm),
                    station_code,
                    station_name,
                    card_no,
                    receipt_no,
                ]
            ).encode("utf-8")
        ).hexdigest()

    for row in rows:
        key = row_key(row)
        if key in seen:
            duplicates.append(row)
        else:
            seen.add(key)

    if not duplicates:
        return 0

    try:
        for row in duplicates:
            db.delete(row)
        db.commit()
        return len(duplicates)
    except Exception:
        db.rollback()
        return 0


def _model_has_column(model, name: str) -> bool:
    return name in _column_name_set(model)


def _get_import_run_column_names() -> set[str]:
    return _column_name_set(ImportRun)


def _create_import_run(db: Session, status: str, rows_loaded: int, detail: str, started_at, finished_at) -> None:
    cols = _get_import_run_column_names()
    payload: dict[str, Any] = {}

    if "source" in cols:
        payload["source"] = SHELL_SOURCE_NAME
    if "status" in cols:
        payload["status"] = status
    if "rows_loaded" in cols:
        payload["rows_loaded"] = int(rows_loaded or 0)
    if "detail" in cols:
        payload["detail"] = detail
    if "started_at" in cols:
        payload["started_at"] = started_at
    if "finished_at" in cols:
        payload["finished_at"] = finished_at

    if payload:
        db.add(ImportRun(**payload))
        db.commit()


def _build_shell_record_payload(
    row: pd.Series,
    row_index: int,
    file_path: Path,
    columns_map: dict[str, str | None],
    fuel_event_columns: set[str],
) -> tuple[dict[str, Any], str | None]:
    plate_raw = row.get(columns_map["plate"]) if columns_map.get("plate") else None
    plate = normalize_plate(str(plate_raw or ""))

    if not plate:
        return {}, None

    liters = _safe_float(row.get(columns_map["liters"])) if columns_map.get("liters") else None
    if liters is None or liters <= 0:
        return {}, plate

    event_dt = _safe_datetime(row.get(columns_map["event_dt"])) if columns_map.get("event_dt") else None
    event_dt = event_dt or now_local()

    amount = _safe_float(row.get(columns_map["amount"])) if columns_map.get("amount") else None
    product = _safe_text(row.get(columns_map["product"])) if columns_map.get("product") else None
    station = _safe_text(row.get(columns_map["station"])) if columns_map.get("station") else None
    card_no = _normalize_identifier_text(row.get(columns_map["card_no"])) if columns_map.get("card_no") else None
    document_no = _normalize_identifier_text(row.get(columns_map["document_no"])) if columns_map.get("document_no") else None
    station_code = _normalize_identifier_text(row.get(columns_map["station_code"])) if columns_map.get("station_code") else None
    province = _safe_text(row.get(_find_first_column(list(row.index), ["провинция", "province", "il"])))
    unit_type = _safe_text(row.get(_find_first_column(list(row.index), ["тип устройства", "device type"])))
    mileage = _safe_float(row.get(_find_first_column(list(row.index), ["километраж", "mileage", "odometer"])))
    unit_price = _safe_float(row.get(_find_first_column(list(row.index), ["цена", "price", "birim fiyat"])))

    raw_row = {str(key): None if pd.isna(val) else val for key, val in row.to_dict().items()}
    fuel_type_norm = _normalize_fuel_type_name(product)
    event_key = hashlib.sha256(
        "|".join(
            [
                SHELL_SOURCE_NAME,
                plate,
                event_dt.strftime("%Y-%m-%d %H:%M:%S"),
                _stable_number_str(liters, 3),
                _stable_number_str(amount, 2),
                str(fuel_type_norm or ""),
                str(station_code or ""),
                str(station or ""),
                str(card_no or ""),
                str(document_no or ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    external_id = event_key

    payload: dict[str, Any] = {}
    value_candidates = {
        "source": SHELL_SOURCE_NAME,
        "supplier": "Shell",
        "brand": "Shell",
        "plate": plate,
        "event_dt": event_dt,
        "datetime": event_dt,
        "occurred_at": event_dt,
        "fuel_dt": event_dt,
        "liters": liters,
        "volume": liters,
        "qty": liters,
        "quantity": liters,
        "amount": amount,
        "amount_try": amount,
        "total_amount": amount,
        "sum_amount": amount,
        "fuel_type": product,
        "fuel_type_raw": product,
        "fuel_type_norm": fuel_type_norm,
        "product": product,
        "product_name": product,
        "station": station,
        "station_name": station,
        "merchant_name": station,
        "station_code": station_code,
        "province": province,
        "device_type": unit_type,
        "mileage": mileage,
        "odometer": mileage,
        "price": unit_price,
        "unit_price": unit_price,
        "card_no": card_no,
        "card_number": card_no,
        "document_no": document_no,
        "doc_no": document_no,
        "receipt_no": document_no,
        "transaction_no": document_no,
        "invoice_no": document_no,
        "source_file": file_path.name,
        "file_name": file_path.name,
        "external_id": external_id,
        "event_key": event_key,
        "event_hash": event_key,
        "raw_payload": json.dumps(raw_row, ensure_ascii=False, default=str),
        "payload_json": json.dumps(raw_row, ensure_ascii=False, default=str),
        "raw_data": json.dumps(raw_row, ensure_ascii=False, default=str),
        "year_month": event_dt.strftime("%Y-%m"),
        "imported_at": now_local(),
        "created_at": now_local(),
    }

    for key, value in value_candidates.items():
        if key in fuel_event_columns and value is not None:
            payload[key] = value

    return payload, plate


def _import_shell_file_direct(db: Session, file_path: Path) -> dict[str, Any]:
    started_at = now_local()
    try:
        df = _read_shell_dataframe(file_path)
    except Exception as exc:
        detail = f"Файл сохранён, но не прочитан: {exc}"
        _create_import_run(db, "error", 0, detail, started_at, now_local())
        raise HTTPException(status_code=400, detail=detail)

    if df.empty:
        detail = "Файл Shell пустой"
        _create_import_run(db, "warning", 0, detail, started_at, now_local())
        raise HTTPException(status_code=400, detail=detail)

    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]

    columns = list(df.columns)
    columns_map = {
        "event_dt": _find_first_column(
            columns,
            [
                "tarih",
                "transaction date",
                "date",
                "purchase date",
                "islem tarihi",
                "дата",
            ],
        ),
        "plate": _find_first_column(
            columns,
            [
                "plate",
                "plaka",
                "license plate",
                "vehicle plate",
                "arac plakasi",
                "vehicle",
                "номерной знак",
                "госномер",
                "номер машины",
            ],
        ),
        "liters": _find_first_column(
            columns,
            [
                "liters",
                "litres",
                "volume",
                "quantity",
                "miktar",
                "volume l",
                "qty",
                "общий литр",
                "литр",
                "литры",
            ],
        ),
        "amount": _find_first_column(
            columns,
            [
                "amount",
                "total amount",
                "tutar",
                "sales amount",
                "net amount",
                "total",
                "стоимость",
                "сумма",
            ],
        ),
        "product": _find_first_column(
            columns,
            [
                "product",
                "fuel",
                "fuel type",
                "urun",
                "product name",
                "вид топлива",
                "топливо",
            ],
        ),
        "station": _find_first_column(
            columns,
            [
                "station",
                "istasyon",
                "merchant",
                "site",
                "station name",
                "название станции",
                "станция",
            ],
        ),
        "card_no": _find_first_column(
            columns,
            [
                "card no",
                "card number",
                "card",
                "kart no",
                "kart",
                "номер карты",
                "карта",
            ],
        ),
        "station_code": _find_first_column(
            columns,
            [
                "station code",
                "код станции",
                "site code",
            ],
        ),
        "document_no": _find_first_column(
            columns,
            [
                "document no",
                "receipt no",
                "invoice no",
                "transaction id",
                "fis no",
                "belge no",
                "ref no",
            ],
        ),
    }

    if not columns_map["plate"] or not columns_map["liters"]:
        detail = (
            "Не удалось определить обязательные колонки Shell. "
            f"Найдены колонки: {', '.join(columns[:20])}"
        )
        _create_import_run(db, "error", 0, detail, started_at, now_local())
        raise HTTPException(status_code=400, detail=detail)

    fuel_event_columns = _column_name_set(FuelEvent)
    imported_rows = 0
    skipped_rows = 0
    duplicates = 0

    for idx, row in df.iterrows():
        payload, plate = _build_shell_record_payload(row, int(idx), file_path, columns_map, fuel_event_columns)
        if not payload:
            skipped_rows += 1
            continue

        if "event_key" in fuel_event_columns and payload.get("event_key"):
            existing = db.query(FuelEvent).filter(getattr(FuelEvent, "event_key") == payload["event_key"]).first()
            if existing is not None:
                duplicates += 1
                continue
        elif "external_id" in fuel_event_columns and payload.get("external_id"):
            existing = db.query(FuelEvent).filter(getattr(FuelEvent, "external_id") == payload["external_id"]).first()
            if existing is not None:
                duplicates += 1
                continue

        try:
            db.add(FuelEvent(**payload))
            db.flush()
            imported_rows += 1
        except Exception as exc:
            db.rollback()
            detail = f"Ошибка импорта Shell в строке Excel {int(idx) + 2}: {exc}"
            _create_import_run(db, "error", imported_rows, detail, started_at, now_local())
            raise HTTPException(status_code=400, detail=detail)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        detail = f"Ошибка сохранения Shell в БД: {exc}"
        _create_import_run(db, "error", imported_rows, detail, started_at, now_local())
        raise HTTPException(status_code=400, detail=detail)

    deduped_rows = _dedupe_shell_events(db)
    status = "ok" if imported_rows else "warning"
    detail = (
        f"Загружено {imported_rows} строк"
        f"{', пропущено ' + str(skipped_rows) if skipped_rows else ''}"
        f"{', дублей при загрузке ' + str(duplicates) if duplicates else ''}"
        f"{', очищено старых дублей ' + str(deduped_rows) if deduped_rows else ''}"
        f". Файл: {file_path.name}"
    )
    _create_import_run(db, status, imported_rows, detail, started_at, now_local())
    return {
        "ok": True,
        "source": SHELL_SOURCE_NAME,
        "supplier": "Shell",
        "brand": "Shell",
        "strategy": "direct_import",
        "imported_rows": imported_rows,
        "skipped_rows": skipped_rows,
        "duplicates": duplicates,
        "deduped_rows": deduped_rows,
        "detail": detail,
        "file_name": file_path.name,
    }


def _process_uploaded_shell_file(db: Session, file_path: Path) -> dict[str, Any]:
    return _import_shell_file_direct(db, file_path)



def check_api_token(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_token and x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _admin_cookie_value() -> str:
    payload = f"{settings.app_name}|{settings.limits_admin_password or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_limits_admin(session_cookie: str | None) -> bool:
    if not settings.limits_admin_password:
        return True
    return session_cookie == _admin_cookie_value()


def _latest_source_runs(db: Session) -> list[dict[str, Any]]:
    sources = ["turpak", "petrol", SHELL_SOURCE_NAME]
    result: list[dict[str, Any]] = []
    for source in sources:
        run = (
            db.query(ImportRun)
            .filter(ImportRun.source == source)
            .order_by(ImportRun.started_at.desc())
            .first()
        )
        if run is None:
            result.append(
                {
                    "name": source,
                    "status": "no_data",
                    "rows_loaded": 0,
                    "last_sync": None,
                    "detail": "Нет запусков",
                }
            )
        else:
            result.append(
                {
                    "name": source,
                    "status": run.status,
                    "rows_loaded": int(run.rows_loaded or 0),
                    "last_sync": run.finished_at or run.started_at,
                    "detail": run.detail or "",
                }
            )
    return result


def _serialize_record(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if pd.isna(v):
            out[k] = None
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "item"):
            try:
                out[k] = v.item()
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


def _pick_best_roster_match(plate: str, ref_dt, driver_registry: pd.DataFrame) -> dict[str, Any]:
    empty = {
        "vehicle_model": None,
        "grade": None,
        "user_name": None,
        "position": None,
        "directorate": None,
        "roster_date": None,
        "driver_file_name": None,
        "driver_sheet_name": None,
    }
    if driver_registry is None or driver_registry.empty or not plate:
        return empty

    subset = driver_registry[driver_registry["plate"] == plate].copy()
    if subset.empty:
        return empty

    subset["roster_date"] = pd.to_datetime(subset["roster_date"], errors="coerce")

    # PostgreSQL may return tz-aware datetimes, while Excel roster dates are usually tz-naive.
    # Normalize both sides to tz-naive dates before calculating date deltas.
    if getattr(subset["roster_date"].dt, "tz", None) is not None:
        subset["roster_date"] = subset["roster_date"].dt.tz_localize(None)

    subset = subset[subset["roster_date"].notna()].copy()
    if subset.empty:
        return empty

    if ref_dt is None or pd.isna(ref_dt):
        picked = subset.sort_values(["roster_date", "driver_file_name", "driver_sheet_name"]).iloc[-1]
        return picked.to_dict()

    ref_date = pd.to_datetime(ref_dt, errors="coerce")
    if pd.isna(ref_date):
        picked = subset.sort_values(["roster_date", "driver_file_name", "driver_sheet_name"]).iloc[-1]
        return picked.to_dict()

    if getattr(ref_date, "tzinfo", None) is not None:
        ref_date = ref_date.tz_localize(None)

    ref_date = ref_date.normalize()
    subset["delta_days"] = (subset["roster_date"].dt.normalize() - ref_date).dt.days
    subset["abs_delta_days"] = subset["delta_days"].abs()
    subset["is_future"] = subset["delta_days"] > 0
    subset = subset.sort_values(
        ["abs_delta_days", "is_future", "roster_date", "driver_file_name", "driver_sheet_name"],
        ascending=[True, True, False, False, False],
    )
    return subset.iloc[0].to_dict()


def _summary_records_with_driver(db: Session, ym: str) -> list[dict[str, Any]]:
    summary = build_monthly_vehicle_summary(db, ym)
    if summary.empty:
        return []

    driver_registry = load_driver_registry()
    rows = []
    for row in summary.to_dict(orient="records"):
        match = _pick_best_roster_match(str(row.get("plate", "") or ""), row.get("last_event_dt"), driver_registry)
        merged = dict(row)
        merged.update(match)
        rows.append(_serialize_record(merged))
    return rows


def _all_limits_rows(db: Session, ym: str) -> list[dict[str, Any]]:
    summary = build_monthly_vehicle_summary(db, ym)
    summary_map = {normalize_plate(r["plate"]): r for r in summary.to_dict(orient="records")} if not summary.empty else {}

    plates_from_events = {
        normalize_plate(x[0])
        for x in db.query(FuelEvent.plate).distinct().all()
        if x[0]
    }
    plates_from_limits = {
        normalize_plate(x.plate)
        for x in db.query(VehicleLimit).all()
        if x.plate
    }
    all_plates = sorted(plates_from_events | plates_from_limits)

    driver_registry = load_driver_registry()
    limit_map = {normalize_plate(v.plate): v for v in db.query(VehicleLimit).all() if v.plate}

    rows: list[dict[str, Any]] = []
    for plate in all_plates:
        s = summary_map.get(plate, {})
        l = limit_map.get(plate)
        match = _pick_best_roster_match(plate, s.get("last_event_dt"), driver_registry)
        row = {
            "plate": plate,
            "vehicle_model": match.get("vehicle_model"),
            "grade": match.get("grade"),
            "user_name": match.get("user_name"),
            "position": match.get("position"),
            "directorate": match.get("directorate"),
            "roster_date": match.get("roster_date"),
            "driver_file_name": match.get("driver_file_name"),
            "driver_sheet_name": match.get("driver_sheet_name"),
            "limit_mode": (getattr(l, "limit_mode", None) or s.get("limit_mode") or "combined").lower(),
            "unlimited": bool(getattr(l, "unlimited", False)),
            "combined_limit_liters": float(getattr(l, "combined_limit_liters", 0) or s.get("combined_limit_liters") or settings.default_monthly_limit_liters),
            "turpak_limit_liters": float(getattr(l, "turpak_limit_liters", 0) or s.get("turpak_limit_liters") or settings.default_monthly_limit_liters),
            "cards_limit_liters": float(getattr(l, "cards_limit_liters", 0) or s.get("cards_limit_liters") or settings.default_monthly_limit_liters),
            "turpak_liters": float(s.get("turpak_liters", 0) or 0),
            "cards_liters": float(s.get("cards_liters", 0) or 0),
            "shell_liters": float(s.get("shell_liters", 0) or 0),
            "petrol_liters": float(s.get("petrol_liters", 0) or 0),
            "total_liters": float(s.get("total_liters", 0) or 0),
            "combined_usage_pct": float(s.get("combined_usage_pct", 0) or 0),
            "turpak_usage_pct": float(s.get("turpak_usage_pct", 0) or 0),
            "cards_usage_pct": float(s.get("cards_usage_pct", 0) or 0),
            "status": s.get("status", "OK"),
            "worst_bucket": s.get("worst_bucket"),
        }
        rows.append(_serialize_record(row))
    return rows


def _get_attr(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _month_bounds(ym: str):
    try:
        start = pd.Timestamp(f"{ym}-01").to_pydatetime()
    except Exception:
        start = pd.Timestamp(now_local()).replace(day=1).to_pydatetime()
    next_month = (pd.Timestamp(start) + pd.offsets.MonthBegin(1)).to_pydatetime()
    return start, next_month


SOURCE_LABEL_MAP = {
    'shell_excel': 'Shell',
    'shell': 'Shell',
    'petrol': 'Petrol',
    'petrol_ofisi': 'Petrol',
    'po': 'Petrol',
    'turpak': 'Turpak',
}


def _event_source_label(event: Any) -> str:
    raw_source = _safe_text(_get_attr(event, 'source'))
    raw_supplier = _safe_text(_get_attr(event, 'supplier', 'brand'))
    probe = _normalize_search_text(raw_source or raw_supplier or '')
    for key, label in SOURCE_LABEL_MAP.items():
        if key in probe:
            return label
    return raw_supplier or raw_source or '—'


DETAIL_EVENT_DT_FIELDS = ('event_dt', 'datetime', 'occurred_at', 'fuel_dt')
DETAIL_STATION_FIELDS = ('station', 'station_name', 'merchant_name')
DETAIL_CARD_FIELDS = ('card_no', 'card_number')
DETAIL_AMOUNT_FIELDS = ('amount', 'amount_try', 'total_amount', 'sum_amount')
DETAIL_LITERS_FIELDS = ('liters', 'volume', 'qty', 'quantity')
DETAIL_RECEIPT_FIELDS = ('document_no', 'doc_no', 'receipt_no', 'transaction_no', 'invoice_no')


def _fuel_event_detail_record(event: Any) -> dict[str, Any]:
    event_dt = _get_attr(event, *DETAIL_EVENT_DT_FIELDS)
    station = _safe_text(_get_attr(event, *DETAIL_STATION_FIELDS))
    card_no = _normalize_identifier_text(_get_attr(event, *DETAIL_CARD_FIELDS))
    amount = _safe_float(_get_attr(event, *DETAIL_AMOUNT_FIELDS)) or 0.0
    liters = _safe_float(_get_attr(event, *DETAIL_LITERS_FIELDS)) or 0.0
    fuel_type = _safe_text(_get_attr(event, 'fuel_type', 'fuel_type_raw', 'product', 'product_name'))
    receipt_no = _safe_text(_get_attr(event, *DETAIL_RECEIPT_FIELDS))
    return _serialize_record({
        'plate': _safe_text(_get_attr(event, 'plate')),
        'event_dt': event_dt,
        'station': station,
        'source_label': _event_source_label(event),
        'liters': liters,
        'amount': amount,
        'card_no': card_no,
        'fuel_type': fuel_type,
        'receipt_no': receipt_no,
    })


def _vehicle_detail_rows(db: Session, plate: str, ym: str) -> list[dict[str, Any]]:
    plate_norm = normalize_plate(plate)
    if not plate_norm:
        return []

    cols = _column_name_set(FuelEvent)
    query = db.query(FuelEvent)
    if 'plate' in cols:
        query = query.filter(getattr(FuelEvent, 'plate') == plate_norm)

    if 'year_month' in cols:
        query = query.filter(getattr(FuelEvent, 'year_month') == ym)
    else:
        start_dt, next_dt = _month_bounds(ym)
        for dt_field in DETAIL_EVENT_DT_FIELDS:
            if dt_field in cols:
                query = query.filter(getattr(FuelEvent, dt_field) >= start_dt, getattr(FuelEvent, dt_field) < next_dt)
                break

    order_fields = [field for field in DETAIL_EVENT_DT_FIELDS if field in cols]
    if order_fields:
        query = query.order_by(getattr(FuelEvent, order_fields[0]).desc())

    return [_fuel_event_detail_record(event) for event in query.all()]


def _leadership_html(ym: str) -> str:
    html = """
<!DOCTYPE html>
<html lang='ru'>
<head>
  <meta charset='utf-8'/>
  <meta name='viewport' content='width=device-width, initial-scale=1'/>
  <title>__APP_NAME__ · Руководство</title>
  <style>
    :root { --bg:#f8fafc; --card:#fff; --border:#e2e8f0; --text:#0f172a; --muted:#64748b; --shadow:0 10px 30px rgba(15,23,42,.06); }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,Arial,sans-serif; background:var(--bg); color:var(--text); }
    .container { max-width:1700px; margin:0 auto; padding:24px; }
    .header,.card { background:var(--card); border:1px solid var(--border); border-radius:24px; padding:20px; box-shadow:var(--shadow); }
    .header { display:flex; justify-content:space-between; gap:16px; align-items:center; flex-wrap:wrap; }
    h1,h2 { margin:0; }
    .muted { color:var(--muted); }
    .actions,.filters { display:flex; gap:12px; flex-wrap:wrap; }
    button,.btn,input,select { border:1px solid #cbd5e1; background:#fff; color:#0f172a; padding:10px 16px; border-radius:16px; font-size:14px; }
    button,.btn { cursor:pointer; text-decoration:none; }
    .btn.primary { background:#111827; color:#fff; border-color:#111827; }
    input,select { padding:10px 12px; }
    .grid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-top:16px; }
    .grid2 { display:grid; grid-template-columns:2fr 1fr; gap:16px; margin-top:16px; }
    .kpi-label { color:var(--muted); font-size:14px; margin-bottom:8px; }
    .kpi-value { font-size:32px; font-weight:700; }
    .kpi-breakdown { margin-top:10px; color:var(--muted); font-size:13px; line-height:1.6; }
    .kpi-breakdown div { display:flex; justify-content:space-between; gap:8px; }
    .badge { display:inline-flex; align-items:center; justify-content:center; border-radius:999px; padding:4px 10px; font-size:12px; font-weight:700; }
    .badge.ok { background:#d1fae5; color:#065f46; }
    .badge.warning { background:#fef3c7; color:#92400e; }
    .badge.critical { background:#ffedd5; color:#9a3412; }
    .badge.exceeded { background:#fee2e2; color:#991b1b; }
    .badge.unlimited { background:#dbeafe; color:#1d4ed8; }
    .source-row,.alert-row { border:1px solid var(--border); border-radius:18px; padding:12px; margin-bottom:10px; }
    .source-head,.alert-head { display:flex; justify-content:space-between; gap:12px; align-items:center; }
    table { width:100%; border-collapse:collapse; }
    th,td { border-bottom:1px solid var(--border); padding:10px 8px; font-size:14px; text-align:left; vertical-align:top; }
    th { color:var(--muted); }
    .plate { font-weight:700; }
    .limit-box { font-size:12px; line-height:1.45; color:#334155; }
    .empty { color:var(--muted); padding:10px 0; }
    .status-wrap{margin-top:16px;display:none;}
    .status-wrap.show{display:block;}
    .status-card{background:#fff;border:1px solid var(--border);border-radius:18px;padding:14px 16px;box-shadow:var(--shadow);}
    .status-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;}
    .status-title{font-weight:700;}
    .status-text{color:var(--muted);font-size:14px;}
    .status-progress{margin-top:10px;height:10px;background:#e5e7eb;border-radius:999px;overflow:hidden;}
    .status-progress-bar{height:100%;width:0%;background:linear-gradient(90deg,#60a5fa,#2563eb);transition:width .25s ease;}
    .clickable-row{cursor:pointer;}
    .clickable-row:hover{background:#f8fafc;}
    .modal-backdrop{position:fixed;inset:0;background:rgba(15,23,42,.45);display:none;align-items:center;justify-content:center;padding:24px;z-index:1000;}
    .modal-backdrop.show{display:flex;}
    .modal-card{width:min(1100px,100%);max-height:90vh;overflow:auto;background:#fff;border:1px solid var(--border);border-radius:24px;padding:20px;box-shadow:var(--shadow);}
    .modal-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-bottom:16px;}
    .modal-title{font-size:24px;font-weight:700;margin:0;}
    .modal-close{background:#111827;color:#fff;border-color:#111827;}
    .detail-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0 18px 0;}
    .detail-kpi{border:1px solid var(--border);border-radius:18px;padding:14px;}
    .detail-kpi-label{font-size:12px;color:var(--muted);margin-bottom:8px;}
    .detail-kpi-value{font-size:22px;font-weight:700;}
    .detail-table-wrap{overflow:auto;}
    .total-col{background:#eff6ff;}
    th.total-col{color:#1d4ed8;font-weight:700;}
    td.total-cell{background:#eff6ff;font-weight:700;color:#1e3a8a;}
    .top-driver{font-size:12px;color:var(--muted);margin-top:2px;}
    @media (max-width:1200px) { .grid4,.grid2,.detail-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<div class='container'>
  <div class='header'>
    <div>
      <div class='muted'>Оперативная витрина для руководства</div>
      <h1>Топливный мониторинг</h1>
      <div class='muted'>Текущий месяц · Последнее обновление: <span id='last-update'>—</span></div>
    </div>
    <div class='actions'>
      <input id='shell-upload-input' type='file' accept='.xlsx,.xls,.csv' style='display:none'/>
      <button id='shell-upload-btn' type='button'>Загрузить файл Shell</button>
      <button id='sync-btn' type='button'>Синхронизировать источники</button>
      <button id='refresh-btn' type='button'>Обновить витрину</button>
      <a class='btn' href='/limits-admin'>Лимиты</a>
      <a class='btn primary' href='/reports/latest'>Скачать отчёт</a>
    </div>
  </div>

  <div id='status-wrap' class='status-wrap'>
    <div class='status-card'>
      <div class='status-head'>
        <div>
          <div class='status-title' id='status-title'>Обновление данных</div>
          <div class='status-text' id='status-text'>Подготовка...</div>
        </div>
        <div class='small muted' id='status-phase'>—</div>
      </div>
      <div class='status-progress'><div id='status-progress-bar' class='status-progress-bar'></div></div>
    </div>
  </div>

  <div class='grid4'>
    <div class='card'>
      <div class='kpi-label'>Заправлено за месяц</div>
      <div class='kpi-value' id='kpi-liters'>0 л</div>
      <div class='muted'>По всем источникам</div>
      <div class='kpi-breakdown'>
        <div><span>Petrol</span><strong id='kpi-petrol-liters'>0 л</strong></div>
        <div><span>Shell</span><strong id='kpi-shell-liters'>0 л</strong></div>
        <div><span>Turpak</span><strong id='kpi-turpak-liters'>0 л</strong></div>
      </div>
    </div>
    <div class='card'><div class='kpi-label'>Близко к лимиту</div><div class='kpi-value' id='kpi-near-limit'>0</div><div class='muted'>WARNING / CRITICAL / EXCEEDED</div></div>
    <div class='card'><div class='kpi-label'>Критические</div><div class='kpi-value' id='kpi-critical'>0</div><div class='muted'>На уровне 90%+</div></div>
    <div class='card'><div class='kpi-label'>Превышение</div><div class='kpi-value' id='kpi-exceeded'>0</div><div class='muted'>Отдельно по bucket лимита</div></div>
  </div>

  <div class='grid2'>
    <div class='card'><h2>Топ машин по расходу</h2><div id='top-consumption' style='margin-top:16px;'></div></div>
    <div class='card'><h2>Статус источников</h2><div id='source-statuses' style='margin-top:16px;'></div></div>
  </div>

  <div class='grid2'>
    <div class='card'>
      <div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;'>
        <h2>Реестр машин</h2>
        <div class='filters'>
          <input id='vehicle-search' type='text' placeholder='Поиск: госномер, водитель, грейд, дирекция'/>
          <select id='vehicle-status-filter'>
            <option value='all'>Все статусы</option>
            <option value='OK'>OK</option>
            <option value='WARNING'>WARNING</option>
            <option value='CRITICAL'>CRITICAL</option>
            <option value='EXCEEDED'>EXCEEDED</option>
            <option value='UNLIMITED'>UNLIMITED</option>
          </select>
        </div>
      </div>
      <div style='overflow:auto;margin-top:16px;'>
        <table>
          <thead>
            <tr>
              <th>Госномер</th><th>Водитель</th><th>Грейд</th><th>Дирекция</th><th>Режим</th><th>Turpak</th><th>Shell</th><th>Petrol</th><th>Cards</th><th class='total-col'>Total</th><th>Лимиты</th><th>Статус</th>
            </tr>
          </thead>
          <tbody id='vehicle-table'></tbody>
        </table>
      </div>
    </div>
    <div class='card'>
      <div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;'>
        <h2>Активные алерты</h2>
        <input id='alert-search' type='text' placeholder='Поиск по алертам'/>
      </div>
      <div id='active-alerts' style='margin-top:16px;'></div>
    </div>
  </div>
</div>

<div id='vehicle-detail-modal' class='modal-backdrop'>
  <div class='modal-card'>
    <div class='modal-head'>
      <div>
        <div class='muted'>Детализация заправок</div>
        <h2 id='vehicle-detail-title' class='modal-title'>—</h2>
        <div id='vehicle-detail-subtitle' class='muted'>—</div>
      </div>
      <button id='vehicle-detail-close' type='button' class='modal-close'>Закрыть</button>
    </div>
    <div id='vehicle-detail-summary' class='detail-grid'></div>
    <div class='detail-table-wrap'>
      <table>
        <thead>
          <tr><th>Дата</th><th>АЗС</th><th>Источник</th><th>Топливо</th><th>Литры</th><th>Сумма</th><th>Карта</th><th>Документ</th></tr>
        </thead>
        <tbody id='vehicle-detail-table'></tbody>
      </table>
    </div>
  </div>
</div>
<script>
let dashboardData = null;
function formatDate(v) {
  if (!v) return '—';
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleString('ru-RU');
}
function statusBadgeClass(s) {
  const value = String(s || 'OK').toLowerCase();
  return 'badge ' + value;
}
function sourceBadgeClass(status) {
  if (status === 'error') return 'badge exceeded';
  if (status === 'skipped' || status === 'warning' || status === 'no_data') return 'badge warning';
  return 'badge ok';
}
function formatMode(row) {
  if (row.unlimited) return 'Безлимит';
  return row.limit_mode === 'separate' ? 'Раздельный' : 'Общий';
}
function formatLimitCell(row) {
  if (row.unlimited) return '<div class="limit-box">Безлимит</div>';
  if (row.limit_mode === 'separate') {
    return '<div class="limit-box">' +
      'Turpak: ' + Number(row.turpak_liters || 0).toFixed(0) + ' / ' + Number(row.turpak_limit_liters || 0).toFixed(0) + ' л (' + Number(row.turpak_usage_pct || 0).toFixed(1) + '%)<br/>' +
      'Shell+Petrol: ' + Number(row.cards_liters || 0).toFixed(0) + ' / ' + Number(row.cards_limit_liters || 0).toFixed(0) + ' л (' + Number(row.cards_usage_pct || 0).toFixed(1) + '%)' +
      '</div>';
  }
  return '<div class="limit-box">Общий: ' + Number(row.total_liters || 0).toFixed(0) + ' / ' + Number(row.combined_limit_liters || 0).toFixed(0) + ' л (' + Number(row.combined_usage_pct || 0).toFixed(1) + '%)</div>';
}
function renderKpis(data) {
  const rows = data.summary || [];
  const total = rows.reduce((s, x) => s + Number(x.total_liters || 0), 0);
  const petrol = rows.reduce((s, x) => s + Number(x.petrol_liters || 0), 0);
  const shell = rows.reduce((s, x) => s + Number(x.shell_liters || 0), 0);
  const turpak = rows.reduce((s, x) => s + Number(x.turpak_liters || 0), 0);
  const near = rows.filter(x => ['WARNING', 'CRITICAL', 'EXCEEDED'].includes(x.status)).length;
  const critical = rows.filter(x => ['CRITICAL', 'EXCEEDED'].includes(x.status)).length;
  const exceeded = rows.filter(x => x.status === 'EXCEEDED').length;
  document.getElementById('kpi-liters').textContent = total.toFixed(0) + ' л';
  document.getElementById('kpi-petrol-liters').textContent = petrol.toFixed(0) + ' л';
  document.getElementById('kpi-shell-liters').textContent = shell.toFixed(0) + ' л';
  document.getElementById('kpi-turpak-liters').textContent = turpak.toFixed(0) + ' л';
  document.getElementById('kpi-near-limit').textContent = String(near);
  document.getElementById('kpi-critical').textContent = String(critical);
  document.getElementById('kpi-exceeded').textContent = String(exceeded);
  document.getElementById('last-update').textContent = formatDate(data.generated_at);
}
function renderVehicles(data) {
  const tbody = document.getElementById('vehicle-table');
  tbody.innerHTML = '';
  const q = String(document.getElementById('vehicle-search').value || '').trim().toLowerCase();
  const statusFilter = String(document.getElementById('vehicle-status-filter').value || 'all');
  const rows = (data.summary || []).filter(row => {
    const hay = [
      row.plate, row.user_name, row.grade, row.directorate, row.vehicle_model, row.sources, row.status, formatMode(row)
    ].join(' ').toLowerCase();
    const bySearch = !q || hay.includes(q);
    const byStatus = statusFilter === 'all' || String(row.status || '') === statusFilter;
    return bySearch && byStatus;
  });
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty">Нет данных</td></tr>';
    return;
  }
  rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    tr.innerHTML = '<td class="plate">' + row.plate + '</td>' +
      '<td>' + (row.user_name || '—') + '</td>' +
      '<td>' + (row.grade || '—') + '</td>' +
      '<td>' + (row.directorate || '—') + '</td>' +
      '<td>' + formatMode(row) + '</td>' +
      '<td>' + Number(row.turpak_liters || 0).toFixed(0) + '</td>' +
      '<td>' + Number(row.shell_liters || 0).toFixed(0) + '</td>' +
      '<td>' + Number(row.petrol_liters || 0).toFixed(0) + '</td>' +
      '<td>' + Number(row.cards_liters || 0).toFixed(0) + '</td>' +
      '<td class="total-cell">' + Number(row.total_liters || 0).toFixed(0) + '</td>' +
      '<td>' + formatLimitCell(row) + '</td>' +
      '<td><span class="' + statusBadgeClass(row.status) + '">' + row.status + '</span></td>';
    tr.addEventListener('click', function(){ openVehicleDetail(row); });
    tbody.appendChild(tr);
  });
}
function renderAlerts(data) {
  const c = document.getElementById('active-alerts');
  c.innerHTML = '';
  const q = String(document.getElementById('alert-search').value || '').trim().toLowerCase();
  const rows = (data.alerts || []).filter(row => {
    const hay = [row.plate, row.user_name, row.grade, row.directorate, row.limit_bucket_label, row.status].join(' ').toLowerCase();
    return !q || hay.includes(q);
  }).slice(0, 50);
  if (!rows.length) {
    c.innerHTML = '<div class="empty">Активных алертов нет</div>';
    return;
  }
  rows.forEach(row => {
    const item = document.createElement('div');
    item.className = 'alert-row';
    item.innerHTML = '<div class="alert-head"><div><div class="plate">' + row.plate + '</div><div class="muted">' + (row.user_name || '—') + ' · ' + (row.directorate || '—') + '</div></div><span class="' + statusBadgeClass(row.status) + '">' + (row.limit_bucket_label || row.limit_bucket || '') + '</span></div>' +
      '<div class="muted" style="margin-top:10px;">Утилизация: ' + Number(row.usage_pct || 0).toFixed(1) + '%<br/>Остаток: ' + Number(row.remaining_liters || 0).toFixed(0) + ' л<br/>Порог: ' + (row.max_threshold_pct || '') + '%</div>';
    c.appendChild(item);
  });
}
function renderTop(data) {
  const c = document.getElementById('top-consumption');
  c.innerHTML = '';
  const top = [...(data.summary || [])]
    .filter(row => !row.unlimited)
    .sort((a, b) => Number(b.total_liters || 0) - Number(a.total_liters || 0))
    .slice(0, 8);
  if (!top.length) {
    c.innerHTML = '<div class="empty">Нет данных</div>';
    return;
  }
  const max = Math.max(...top.map(x => Number(x.total_liters || 0)), 1);
  top.forEach(row => {
    const pct = Math.max(3, (Number(row.total_liters || 0) / max) * 100);
    const div = document.createElement('div');
    div.style.marginBottom = '10px';
    div.innerHTML = '<div style="display:grid;grid-template-columns:220px 1fr 70px;gap:10px;align-items:center;">' +
      '<div><div class="plate">' + row.plate + '</div><div class="top-driver">' + (row.user_name || 'Водитель не указан') + '</div></div>' +
      '<div style="height:14px;background:#e5e7eb;border-radius:999px;overflow:hidden;"><div style="height:100%;width:' + pct + '%;background:linear-gradient(90deg,#60a5fa,#2563eb);"></div></div>' +
      '<div>' + Number(row.total_liters || 0).toFixed(0) + ' л</div>' +
      '</div>';
    c.appendChild(div);
  });
}

function formatMoney(value) {
  return Number(value || 0).toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ₺';
}
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function closeVehicleDetail() {
  const modal = document.getElementById('vehicle-detail-modal');
  if (modal) modal.classList.remove('show');
}
async function openVehicleDetail(row) {
  const modal = document.getElementById('vehicle-detail-modal');
  const title = document.getElementById('vehicle-detail-title');
  const subtitle = document.getElementById('vehicle-detail-subtitle');
  const summary = document.getElementById('vehicle-detail-summary');
  const tbody = document.getElementById('vehicle-detail-table');
  title.textContent = row.plate || '—';
  subtitle.textContent = 'Загрузка детализации...';
  summary.innerHTML = '';
  tbody.innerHTML = '<tr><td colspan="8" class="empty">Загрузка...</td></tr>';
  modal.classList.add('show');
  try {
    const res = await fetch('/vehicle-detail?plate=' + encodeURIComponent(row.plate) + '&year_month=__YM__', {cache:'no-store'});
    if (!res.ok) throw new Error('detail load failed');
    const payload = await res.json();
    const items = payload.items || [];
    const shellLiters = items.filter(x => x.source_label === 'Shell').reduce((s, x) => s + Number(x.liters || 0), 0);
    const petrolLiters = items.filter(x => x.source_label === 'Petrol').reduce((s, x) => s + Number(x.liters || 0), 0);
    const totalAmount = items.reduce((s, x) => s + Number(x.amount || 0), 0);
    subtitle.textContent = 'Заправки за ' + (payload.year_month || 'период') + ' · ' + items.length + ' операций';
    summary.innerHTML = [
      ['Всего литров', Number(payload.total_liters || 0).toFixed(2) + ' л'],
      ['Всего сумма', formatMoney(totalAmount)],
      ['Shell', shellLiters.toFixed(2) + ' л'],
      ['Petrol', petrolLiters.toFixed(2) + ' л']
    ].map(function(item){ return '<div class="detail-kpi"><div class="detail-kpi-label">' + item[0] + '</div><div class="detail-kpi-value">' + item[1] + '</div></div>'; }).join('');
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">Нет заправок за выбранный период</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(function(item){
      return '<tr>' +
        '<td>' + escapeHtml(formatDate(item.event_dt)) + '</td>' +
        '<td>' + escapeHtml(item.station || '—') + '</td>' +
        '<td>' + escapeHtml(item.source_label || '—') + '</td>' +
        '<td>' + escapeHtml(item.fuel_type || '—') + '</td>' +
        '<td>' + Number(item.liters || 0).toFixed(2) + '</td>' +
        '<td>' + escapeHtml(formatMoney(item.amount || 0)) + '</td>' +
        '<td>' + escapeHtml(item.card_no || '—') + '</td>' +
        '<td>' + escapeHtml(item.receipt_no || '—') + '</td>' +
      '</tr>';
    }).join('');
  } catch (e) {
    console.error(e);
    subtitle.textContent = 'Не удалось загрузить детализацию';
    tbody.innerHTML = '<tr><td colspan="8" class="empty">Ошибка загрузки детализации</td></tr>';
  }
}

function setStatusBar(visible, title, text, phase, percent) {
  const wrap = document.getElementById('status-wrap');
  if (!wrap) return;
  wrap.classList.toggle('show', !!visible);
  document.getElementById('status-title').textContent = title || 'Обновление данных';
  document.getElementById('status-text').textContent = text || '';
  document.getElementById('status-phase').textContent = phase || '—';
  document.getElementById('status-progress-bar').style.width = (percent || 0) + '%';
}

function renderSources(data) {
  const c = document.getElementById('source-statuses');
  c.innerHTML = '';
  (data.sources || []).forEach(s => {
    const item = document.createElement('div');
    item.className = 'source-row';
    item.innerHTML = '<div class="source-head"><div class="plate">' + s.name + '</div><span class="' + sourceBadgeClass(s.status) + '">' + String(s.status || '').toUpperCase() + '</span></div>' +
      '<div class="muted" style="margin-top:10px;">Строк загружено: ' + s.rows_loaded + '<br/>Последний sync: ' + formatDate(s.last_sync) + '<br/>' + (s.detail || '') + '</div>';
    c.appendChild(item);
  });
}

async function uploadShellFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  setStatusBar(true, 'Загрузка файла Shell', 'Файл отправляется на сервер и сразу уходит в импорт.', 'upload', 15);
  const res = await fetch('/shell/upload', { method: 'POST', body: formData });
  const rawText = await res.text();
  let payload = {};
  try {
    payload = rawText ? JSON.parse(rawText) : {};
  } catch (e) {
    payload = { detail: rawText || 'Сервер вернул пустой ответ' };
  }
  if (!res.ok || !payload.ok) {
    const message = payload.detail || 'Не удалось загрузить и обработать файл Shell';
    setStatusBar(true, 'Ошибка загрузки', message, 'error', 100);
    throw new Error(message);
  }
  setStatusBar(true, 'Файл Shell обработан', (payload.detail || 'Импорт завершён') + '. Обновляем витрину.', 'refresh', 70);
  await reloadDashboard(false);
}

async function reloadDashboard(runSync) {
  const btn = document.getElementById('refresh-btn');
  const syncBtn = document.getElementById('sync-btn');
  try {
    btn.disabled = true;
    if (syncBtn) syncBtn.disabled = true;
    btn.textContent = runSync ? 'Витрина заблокирована...' : 'Загрузка...';
    if (syncBtn) syncBtn.textContent = runSync ? 'Идёт sync...' : 'Синхронизировать источники';

    setStatusBar(
      true,
      runSync ? 'Синхронизация источников' : 'Обновление витрины',
      runSync ? 'Запускаем синхронизацию источников и затем перечитываем данные из БД.' : 'Перечитываем актуальные данные из БД без повторного импорта Shell.',
      runSync ? 'sync' : 'load',
      runSync ? 15 : 20
    );

    if (runSync) {
      setStatusBar(true, 'Синхронизация источников', 'Стартовал sync. Ждём ответ сервера.', 'sync', 35);
      const syncRes = await fetch('/dashboard/refresh', { method: 'POST' });
      if (!syncRes.ok) throw new Error('Sync failed');
      setStatusBar(true, 'Синхронизация источников', 'Синхронизация завершена. Перечитываем витрину.', 'load', 70);
    }

    const res = await fetch('/dashboard/leadership-data?year_month=__YM__', { cache: 'no-store' });
    if (!res.ok) throw new Error('Data load failed');
    dashboardData = await res.json();
    setStatusBar(true, runSync ? 'Синхронизация завершена' : 'Витрина обновлена', 'Рисуем дашборд и обновляем таблицы.', 'render', 90);
    renderKpis(dashboardData);
    renderVehicles(dashboardData);
    renderAlerts(dashboardData);
    renderTop(dashboardData);
    renderSources(dashboardData);
    setStatusBar(true, 'Готово', runSync ? 'Источники синхронизированы, витрина обновлена.' : 'Витрина успешно обновлена без повторного импорта.', 'done', 100);
    setTimeout(() => setStatusBar(false, '', '', '', 0), 1200);
  } catch (e) {
    console.error(e);
    setStatusBar(true, runSync ? 'Ошибка синхронизации' : 'Ошибка обновления', 'Не удалось обновить данные. Проверь backend и попробуй ещё раз.', 'error', 100);
    alert(runSync ? 'Не удалось синхронизировать источники' : 'Не удалось обновить дашборд');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Обновить витрину';
    if (syncBtn) {
      syncBtn.disabled = false;
      syncBtn.textContent = 'Синхронизировать источники';
    }
  }
}

document.addEventListener('DOMContentLoaded', function () {
  const uploadBtn = document.getElementById('shell-upload-btn');
  const uploadInput = document.getElementById('shell-upload-input');
  const syncBtn = document.getElementById('sync-btn');
  const detailModal = document.getElementById('vehicle-detail-modal');
  const detailCloseBtn = document.getElementById('vehicle-detail-close');
  document.getElementById('refresh-btn').addEventListener('click', function () { reloadDashboard(false); });
  if (syncBtn) syncBtn.addEventListener('click', function () { reloadDashboard(true); });
  if (detailCloseBtn) {
    detailCloseBtn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      closeVehicleDetail();
    });
  }
  if (detailModal) {
    detailModal.addEventListener('click', function (e) {
      if (e.target === detailModal) closeVehicleDetail();
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeVehicleDetail();
  });
  uploadBtn.addEventListener('click', function () { uploadInput.click(); });
  uploadInput.addEventListener('change', async function () {
    if (!uploadInput.files || !uploadInput.files.length) return;
    const file = uploadInput.files[0];
    try {
      await uploadShellFile(file);
    } catch (e) {
      console.error(e);
      alert(e.message || 'Не удалось импортировать файл Shell');
    } finally {
      uploadInput.value = '';
    }
  });
  document.getElementById('vehicle-search').addEventListener('input', function () { if (dashboardData) renderVehicles(dashboardData); });
  document.getElementById('vehicle-status-filter').addEventListener('change', function () { if (dashboardData) renderVehicles(dashboardData); });
  document.getElementById('alert-search').addEventListener('input', function () { if (dashboardData) renderAlerts(dashboardData); });
  reloadDashboard(false);
});
</script>
</body>
</html>
"""
    return html.replace("__APP_NAME__", settings.app_name).replace("__YM__", ym)


def _limits_login_html(error: str | None = None) -> str:
    err = f"<div style='color:#b91c1c;margin-top:12px;'>{error}</div>" if error else ""
    return (
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/>"
        f"<title>{settings.app_name} · Лимиты</title>"
        "<style>body{font-family:Inter,Arial,sans-serif;background:#f8fafc;margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}"
        ".card{background:#fff;border:1px solid #e2e8f0;border-radius:24px;padding:24px;min-width:360px;box-shadow:0 10px 30px rgba(15,23,42,.06)}"
        "input,button{width:100%;padding:12px 14px;border-radius:14px;border:1px solid #cbd5e1;font-size:14px}"
        "button{margin-top:12px;background:#111827;color:#fff;border-color:#111827;cursor:pointer}</style></head>"
        "<body><div class='card'><h2>Страница лимитов</h2><div style='color:#64748b;margin:8px 0 16px 0;'>Введите пароль для доступа.</div>"
        "<form id='login-form'><input id='password' type='password' placeholder='Пароль'/><button type='submit'>Войти</button></form>"
        f"{err}"
        "<script>document.getElementById('login-form').addEventListener('submit', async function(e){e.preventDefault(); const password=document.getElementById('password').value; const res=await fetch('/limits-admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:password})}); if(res.ok){window.location='/limits-admin'; return;} location.reload();});</script>"
        "</div></body></html>"
    )


def _limits_admin_html() -> str:
    return """
<!DOCTYPE html>
<html lang='ru'>
<head>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>Лимиты</title>
<style>
body{font-family:Inter,Arial,sans-serif;background:#f8fafc;margin:0;color:#0f172a}
.container{max-width:1700px;margin:0 auto;padding:24px}
.header,.card{background:#fff;border:1px solid #e2e8f0;border-radius:24px;padding:20px;box-shadow:0 10px 30px rgba(15,23,42,.06)}
.header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.actions,.filters{display:flex;gap:12px;flex-wrap:wrap}
button,input,select{border:1px solid #cbd5e1;background:#fff;padding:10px 12px;border-radius:16px;font-size:14px}
button{cursor:pointer}
table{width:100%;border-collapse:collapse;margin-top:16px}
th,td{border-bottom:1px solid #e2e8f0;padding:10px 8px;text-align:left;font-size:14px}
th{color:#64748b}
input[type=number]{width:120px;padding:8px;border:1px solid #cbd5e1;border-radius:12px}
select{padding:8px;border:1px solid #cbd5e1;border-radius:12px}
.muted{color:#64748b}.small{font-size:12px;color:#64748b}.row-btn{padding:8px 12px;border-radius:12px}
</style>
</head>
<body>
<div class='container'>
  <div class='header'>
    <div><h1 style='margin:0'>Лимиты по машинам</h1><div class='muted'>Все машины из fuel_events и vehicle_limits. Общий или раздельный лимит. Безлимит отключает контроль.</div></div>
    <div class='actions'>
      <button id='reload-btn'>Обновить все лимиты</button>
      <button id='save-all-btn'>Сохранить все</button>
      <form method='post' action='/limits-admin/logout' style='display:inline'><button type='submit'>Выйти</button></form>
      <a href='/leadership'><button type='button'>Назад</button></a>
    </div>
  </div>
  <div class='card' style='margin-top:16px;overflow:auto'>
    <div class='filters'>
      <input id='limits-search' type='text' placeholder='Поиск: госномер, водитель, грейд, дирекция'/>
      <select id='limits-mode-filter'>
        <option value='all'>Все режимы</option>
        <option value='combined'>Общий</option>
        <option value='separate'>Раздельный</option>
        <option value='unlimited'>Безлимит</option>
      </select>
    </div>
    <table>
      <thead>
        <tr><th>Госномер</th><th>Водитель</th><th>Грейд</th><th>Дирекция</th><th>Режим</th><th>Общий</th><th>Turpak</th><th>Shell+Petrol</th><th>Безлимит</th><th>Сохранить</th></tr>
      </thead>
      <tbody id='limits-table'></tbody>
    </table>
  </div>
</div>

<div id='vehicle-detail-modal' class='modal-backdrop'>
  <div class='modal-card'>
    <div class='modal-head'>
      <div>
        <div class='muted'>Детализация заправок</div>
        <h2 id='vehicle-detail-title' class='modal-title'>—</h2>
        <div id='vehicle-detail-subtitle' class='muted'>—</div>
      </div>
      <button id='vehicle-detail-close' type='button' class='modal-close'>Закрыть</button>
    </div>
    <div id='vehicle-detail-summary' class='detail-grid'></div>
    <div class='detail-table-wrap'>
      <table>
        <thead>
          <tr><th>Дата</th><th>АЗС</th><th>Источник</th><th>Топливо</th><th>Литры</th><th>Сумма</th><th>Карта</th><th>Документ</th></tr>
        </thead>
        <tbody id='vehicle-detail-table'></tbody>
      </table>
    </div>
  </div>
</div>
<script>
let limitsRows = [];
function buildRow(row){
  const tr=document.createElement('tr');
  tr.dataset.plate=row.plate;
  tr.dataset.user=(row.user_name||'');
  tr.dataset.grade=(row.grade||'');
  tr.dataset.directorate=(row.directorate||'');
  tr.innerHTML = '<td><strong>'+row.plate+'</strong></td>'+
    '<td>'+(row.user_name||'—')+'</td>'+
    '<td>'+(row.grade||'—')+'</td>'+
    '<td>'+(row.directorate||'—')+'</td>'+
    '<td><select class="mode"><option value="combined" '+(row.limit_mode==='combined'?'selected':'')+'>Общий</option><option value="separate" '+(row.limit_mode==='separate'?'selected':'')+'>Раздельный</option></select><div class="small">Статус: '+(row.status||'OK')+'</div></td>'+
    '<td><input class="combined" type="number" step="0.01" value="'+Number(row.combined_limit_liters||0)+'"/></td>'+
    '<td><input class="turpak" type="number" step="0.01" value="'+Number(row.turpak_limit_liters||0)+'"/></td>'+
    '<td><input class="cards" type="number" step="0.01" value="'+Number(row.cards_limit_liters||0)+'"/></td>'+
    '<td><input class="unlimited" type="checkbox" '+(row.unlimited?'checked':'')+'/></td>'+
    '<td><button class="row-btn save-one">Сохранить</button></td>';
  function apply(){
    const mode=tr.querySelector('.mode').value;
    const unlimited=tr.querySelector('.unlimited').checked;
    tr.querySelector('.combined').disabled = unlimited || mode!=='combined';
    tr.querySelector('.turpak').disabled = unlimited || mode!=='separate';
    tr.querySelector('.cards').disabled = unlimited || mode!=='separate';
  }
  tr.querySelector('.mode').addEventListener('change', apply);
  tr.querySelector('.unlimited').addEventListener('change', apply);
  apply();
  tr.querySelector('.save-one').addEventListener('click', async function(){ await saveRow(tr); });
  return tr;
}
function rowPayload(tr){
  return {
    plate: tr.dataset.plate,
    limit_mode: tr.querySelector('.mode').value,
    unlimited: tr.querySelector('.unlimited').checked,
    combined_limit_liters: Number(tr.querySelector('.combined').value||0),
    turpak_limit_liters: Number(tr.querySelector('.turpak').value||0),
    cards_limit_liters: Number(tr.querySelector('.cards').value||0)
  };
}
async function saveRow(tr){
  const payload=rowPayload(tr);
  const res=await fetch('/limits-admin/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!res.ok){alert('Не удалось сохранить'); return;}
  alert('Сохранено: '+payload.plate);
}
function renderLimitsTable(){
  const tbody=document.getElementById('limits-table');
  tbody.innerHTML='';
  const q = String(document.getElementById('limits-search').value || '').trim().toLowerCase();
  const modeFilter = String(document.getElementById('limits-mode-filter').value || 'all');
  const rows = limitsRows.filter(row => {
    const hay = [row.plate, row.user_name, row.grade, row.directorate].join(' ').toLowerCase();
    const bySearch = !q || hay.includes(q);
    const rowMode = row.unlimited ? 'unlimited' : (row.limit_mode || 'combined');
    const byMode = modeFilter === 'all' || rowMode === modeFilter;
    return bySearch && byMode;
  });
  if(!rows.length){
    tbody.innerHTML = '<tr><td colspan="10" class="small">Нет данных</td></tr>';
    return;
  }
  rows.forEach(r => tbody.appendChild(buildRow(r)));
}
async function reloadTable(){
  const res=await fetch('/limits-admin/data',{cache:'no-store'});
  if(!res.ok){alert('Не удалось загрузить лимиты'); return;}
  limitsRows=await res.json();
  renderLimitsTable();
}
async function saveAll(){
  const tbody=document.getElementById('limits-table');
  const payload=[...tbody.querySelectorAll('tr[data-plate]')].map(rowPayload);
  const res=await fetch('/limits-admin/save-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!res.ok){alert('Не удалось сохранить все'); return;}
  alert('Все лимиты сохранены');
  await reloadTable();
}
document.addEventListener('DOMContentLoaded', function(){
  document.getElementById('reload-btn').addEventListener('click', reloadTable);
  document.getElementById('save-all-btn').addEventListener('click', saveAll);
  document.getElementById('limits-search').addEventListener('input', renderLimitsTable);
  document.getElementById('limits-mode-filter').addEventListener('change', renderLimitsTable);
  reloadTable();
});
</script>
</body>
</html>
"""


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(ok=True, app=settings.app_name, time=now_local())


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/leadership", status_code=302)


@app.get("/leadership", response_class=HTMLResponse)
def leadership_dashboard() -> str:
    return _leadership_html(current_year_month())


@app.get("/dashboard/leadership-data")
def leadership_data(year_month: str | None = None, db: Session = Depends(get_db)):
    ym = year_month or current_year_month()
    _dedupe_shell_events(db, ym)
    summary_df = build_monthly_vehicle_summary(db, ym)
    summary = _summary_records_with_driver(db, ym)
    alert_registry = refresh_alert_state(db, summary_df, ym)
    alerts = [] if alert_registry.empty else alert_registry.to_dict(orient="records")

    driver_registry = load_driver_registry()
    enriched_alerts = []
    for row in alerts:
        match = _pick_best_roster_match(str(row.get("plate", "") or ""), row.get("last_event_dt"), driver_registry)
        merged = dict(row)
        merged.update(match)
        merged["limit_bucket_label"] = {
            "combined": "Общий",
            "turpak": "Turpak",
            "cards": "Карты Shell и Petrol",
        }.get(merged.get("limit_bucket"), merged.get("limit_bucket"))
        enriched_alerts.append(_serialize_record(merged))

    return {
        "year_month": ym,
        "generated_at": now_local().isoformat(),
        "summary": summary,
        "alerts": enriched_alerts,
        "sources": _latest_source_runs(db),
    }



@app.post("/shell/upload")
async def shell_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    saved_path: Path | None = None
    archived_path: Path | None = None
    try:
        filename = file.filename or ""
        if not filename:
            raise HTTPException(status_code=400, detail="Файл не выбран")

        if not _is_allowed_shell_filename(filename):
            raise HTTPException(status_code=400, detail="Поддерживаются только xlsx, xls и csv")

        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size > SHELL_UPLOAD_MAX_SIZE_BYTES:
            raise HTTPException(status_code=400, detail="Файл слишком большой")

        saved_path = _save_shell_upload(file)
        result = _process_uploaded_shell_file(db, saved_path)
        archived_path = _archive_processed_shell_file(saved_path)

        if archived_path is not None:
            result["archived_file"] = str(archived_path)
            detail = str(result.get("detail") or "").rstrip(". ")
            if detail:
                result["detail"] = f"{detail}. Архив: {archived_path.name}"
            else:
                result["detail"] = f"Файл обработан и перемещён в архив: {archived_path.name}"

        return JSONResponse(result)
    except HTTPException as exc:
        db.rollback()
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "detail": str(exc.detail)})
    except Exception as exc:
        db.rollback()
        return JSONResponse(status_code=500, content={"ok": False, "detail": f"Ошибка загрузки Shell: {exc}"})


@app.post("/dashboard/refresh")
def dashboard_refresh(db: Session = Depends(get_db)):
    results, report_path = sync_all(db, build_report=True, send_report=False)
    _dedupe_shell_events(db, current_year_month())
    return {"ok": True, "results": [r.__dict__ for r in results], "report_path": report_path}


@app.post("/sync/run", response_model=SyncRunResponse, dependencies=[Depends(check_api_token)])
def run_sync(db: Session = Depends(get_db)) -> SyncRunResponse:
    results, report_path = sync_all(db, build_report=True, send_report=False)
    return SyncRunResponse(ok=True, results=[SyncResult(**r.__dict__) for r in results], report_path=report_path)


@app.get("/summary/monthly")
def monthly_summary(year_month: str | None = None, db: Session = Depends(get_db)):
    ym = year_month or current_year_month()
    summary = build_monthly_vehicle_summary(db, ym)
    if summary.empty:
        return []
    return [_serialize_record(row) for row in summary.to_dict(orient="records")]


@app.get("/events")
def get_events(year_month: str | None = None, plate: str | None = None, db: Session = Depends(get_db)):
    ym = year_month or current_year_month()
    df = fetch_events(db, ym, plate=plate)
    if df.empty:
        return []
    return [_serialize_record(row) for row in df.to_dict(orient="records")]


@app.get("/vehicle-detail")
def vehicle_detail(plate: str, year_month: str | None = None, db: Session = Depends(get_db)):
    ym = year_month or current_year_month()
    _dedupe_shell_events(db, ym)
    items = _vehicle_detail_rows(db, plate, ym)
    total_liters = sum(float(item.get('liters') or 0) for item in items)
    return {
        "plate": normalize_plate(plate),
        "year_month": ym,
        "total_liters": total_liters,
        "items": items,
    }


@app.get("/limits-admin", response_class=HTMLResponse)
def limits_admin_page(limits_admin_session: str | None = Cookie(default=None, alias=LIMITS_COOKIE_NAME)):
    if not _is_limits_admin(limits_admin_session):
        return HTMLResponse(_limits_login_html())
    return HTMLResponse(_limits_admin_html())


@app.post("/limits-admin/login")
async def limits_admin_login(request: Request):
    data = await request.json()
    password = str(data.get("password", "") or "")
    if settings.limits_admin_password and password != settings.limits_admin_password:
        return HTMLResponse(_limits_login_html("Неверный пароль"), status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie(LIMITS_COOKIE_NAME, _admin_cookie_value(), httponly=True, samesite="lax")
    return response


@app.post("/limits-admin/logout")
def limits_admin_logout():
    response = RedirectResponse(url="/limits-admin", status_code=302)
    response.delete_cookie(LIMITS_COOKIE_NAME)
    return response


@app.get("/limits-admin/data")
def limits_admin_data(
    limits_admin_session: str | None = Cookie(default=None, alias=LIMITS_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if not _is_limits_admin(limits_admin_session):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _all_limits_rows(db, current_year_month())


@app.post("/limits-admin/save")
def limits_admin_save(
    item: LimitUpsert,
    limits_admin_session: str | None = Cookie(default=None, alias=LIMITS_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if not _is_limits_admin(limits_admin_session):
        raise HTTPException(status_code=401, detail="Unauthorized")
    count = upsert_limits(db, [item.model_dump()])
    return {"ok": True, "count": count}


@app.post("/limits-admin/save-all")
def limits_admin_save_all(
    items: list[LimitUpsert],
    limits_admin_session: str | None = Cookie(default=None, alias=LIMITS_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if not _is_limits_admin(limits_admin_session):
        raise HTTPException(status_code=401, detail="Unauthorized")
    count = upsert_limits(db, [item.model_dump() for item in items])
    return {"ok": True, "count": count}


@app.get("/reports/latest")
def latest_report() -> FileResponse:
    files = sorted(Path(settings.report_output_path).glob("fuel_report_*.xlsx"))
    if not files:
        raise HTTPException(status_code=404, detail="No reports found")
    latest = files[-1]
    return FileResponse(path=latest, filename=latest.name)
