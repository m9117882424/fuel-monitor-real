from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import settings
from ..io_utils import newest_matching_file, read_shell_excel, read_tabular_file
from ..models import FuelEvent, ImportRun
from ..normalizers import normalize_petrol_api_payload, normalize_petrol_df, normalize_shell_df, normalize_turpak_sales
from ..sources.petrol import PetrolAutomaticClient
from ..sources.shell_tts import ShellTtsClient, shell_transactions_to_legacy_df
from ..sources.turpak import TurpakClient
from ..utils import current_year_month, format_petrol_dt, month_start_local, now_local
from .alert_service import build_alert_candidates, dispatch_alerts, filter_unsent_alerts, refresh_alert_state
from .driver_registry_service import load_driver_registry
from .report_service import export_report
from .storage import save_events
from .summary_service import build_monthly_vehicle_summary, fetch_events
from .telegram_service import send_telegram_document


PETROL_CHUNK_DAYS = 1


@dataclass
class SourceSyncResult:
    source: str
    rows_loaded: int
    detail: str | None = None


def _track_run_start(db: Session, source: str) -> ImportRun:
    run = ImportRun(source=source, status='running')
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _track_run_finish(db: Session, run: ImportRun, rows_loaded: int, status: str = 'ok', detail: str | None = None) -> None:
    run.rows_loaded = rows_loaded
    run.status = status
    run.detail = detail
    run.finished_at = datetime.utcnow()
    db.add(run)
    db.commit()


def _source_has_month_data(db: Session, source: str, year_month: str) -> bool:
    existing = db.query(FuelEvent.id).filter(FuelEvent.source == source, FuelEvent.year_month == year_month).first()
    return existing is not None


def _build_source_window(db: Session, source: str) -> tuple[datetime, datetime]:
    end_dt = now_local().replace(microsecond=0)
    year_month = end_dt.strftime('%Y-%m')

    if _source_has_month_data(db, source, year_month):
        start_dt = (end_dt - timedelta(days=settings.regular_sync_days_back)).replace(microsecond=0)
    else:
        start_dt = month_start_local(end_dt)

    return start_dt, end_dt


def _format_turpak_window(db: Session) -> tuple[str, str]:
    start_dt, end_dt = _build_source_window(db, 'turpak')
    return start_dt.isoformat(), end_dt.isoformat()


def _format_petrol_window(db: Session) -> tuple[str, str]:
    start_dt, end_dt = _build_source_window(db, 'petrol')
    return format_petrol_dt(start_dt), format_petrol_dt(end_dt)


def _parse_petrol_dt(value: str) -> datetime:
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S')


def _iter_petrol_chunks(start_dt_str: str, end_dt_str: str, chunk_days: int = PETROL_CHUNK_DAYS):
    current = _parse_petrol_dt(start_dt_str)
    end_dt = _parse_petrol_dt(end_dt_str)
    while current < end_dt:
        chunk_end = min(current + timedelta(days=chunk_days), end_dt)
        yield current.strftime('%Y-%m-%dT%H:%M:%S'), chunk_end.strftime('%Y-%m-%dT%H:%M:%S')
        current = chunk_end


def _petrol_payload_to_events(payload) -> pd.DataFrame:
    rows: list[dict] = []

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ('data', 'rows', 'items', 'salesList'):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break

    raw = pd.DataFrame(rows)
    events = normalize_petrol_df(raw)
    if events.empty and isinstance(payload, dict):
        fallback_events = normalize_petrol_api_payload(payload)
        if not fallback_events.empty:
            events = fallback_events
    return events


def _zero_turpak_amounts(db: Session) -> int:
    stmt = update(FuelEvent).where(FuelEvent.source == 'turpak').values(unit_price_try=0, amount_try=0, discount_try=0)
    result = db.execute(stmt)
    db.commit()
    return int(result.rowcount or 0)


def _sync_shell_file_fallback(db: Session, run: ImportRun, api_error: str | None = None) -> SourceSyncResult:
    path = None
    if settings.shell_input_path:
        path = Path(settings.shell_input_path)
    elif settings.shell_input_dir:
        path = newest_matching_file(settings.shell_input_dir, settings.shell_glob)

    if not path or not path.exists():
        detail = 'Shell API is not configured and Shell file not found'
        if api_error:
            detail = f'Shell API error: {api_error}; fallback file not found'
        _track_run_finish(db, run, 0, status='error' if api_error else 'skipped', detail=detail)
        return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=detail)

    raw = read_shell_excel(path)
    events = normalize_shell_df(raw)
    rows_loaded = save_events(db, events)
    detail = f'file fallback {path}'
    if api_error:
        detail += f'; API error: {api_error}'
    _track_run_finish(db, run, rows_loaded, detail=detail)
    return SourceSyncResult(source='shell_excel', rows_loaded=rows_loaded, detail=detail)


def sync_shell(db: Session) -> SourceSyncResult:
    run = _track_run_start(db, 'shell_excel')
    try:
        api_configured = all((
            settings.shell_customer_code,
            settings.shell_user_id,
            settings.shell_password,
            settings.shell_branch_code,
        ))

        if settings.shell_use_api and api_configured:
            start_dt, end_dt = _build_source_window(db, 'shell_excel')
            client = ShellTtsClient(
                base_url=settings.shell_base_url,
                customer_code=settings.shell_customer_code,
                user_id=settings.shell_user_id,
                password=settings.shell_password,
                branch_code=settings.shell_branch_code,
                timeout=settings.shell_timeout_seconds,
            )
            rows, process_result = client.get_customer_sales_transactions(start_dt=start_dt, end_dt=end_dt)
            raw = shell_transactions_to_legacy_df(rows)
            events = normalize_shell_df(raw)
            rows_loaded = save_events(db, events)
            detail = f'api GetCustomerSalesTransaction {start_dt.isoformat()} -> {end_dt.isoformat()}, received={len(rows)}'
            if process_result:
                detail += f', result={process_result}'
            _track_run_finish(db, run, rows_loaded, detail=detail)
            return SourceSyncResult(source='shell_excel', rows_loaded=rows_loaded, detail=detail)

        if settings.shell_file_fallback_enabled:
            return _sync_shell_file_fallback(db, run)

        detail = 'Shell SOAP credentials are not configured'
        _track_run_finish(db, run, 0, status='skipped', detail=detail)
        return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=detail)

    except requests.exceptions.RequestException as exc:
        db.rollback()
        if settings.shell_file_fallback_enabled:
            try:
                return _sync_shell_file_fallback(db, run, api_error=str(exc))
            except Exception as fallback_exc:
                db.rollback()
                detail = f'Shell API error: {exc}; fallback error: {fallback_exc}'
                _track_run_finish(db, run, 0, status='error', detail=detail)
                return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=detail)
        detail = f'Shell request error: {exc}'
        _track_run_finish(db, run, 0, status='error', detail=detail)
        return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=detail)
    except Exception as exc:
        db.rollback()
        if settings.shell_file_fallback_enabled:
            try:
                return _sync_shell_file_fallback(db, run, api_error=str(exc))
            except Exception as fallback_exc:
                db.rollback()
                detail = f'Shell API error: {exc}; fallback error: {fallback_exc}'
                _track_run_finish(db, run, 0, status='error', detail=detail)
                return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=detail)
        _track_run_finish(db, run, 0, status='error', detail=str(exc))
        return SourceSyncResult(source='shell_excel', rows_loaded=0, detail=f'error: {exc}')


def sync_petrol(db: Session) -> SourceSyncResult:
    run = _track_run_start(db, 'petrol')
    try:
        start_dt, end_dt = _format_petrol_window(db)

        if settings.petrol_use_api and settings.petrol_user_name and settings.petrol_user_password:
            client = PetrolAutomaticClient(
                base_url=settings.petrol_base_url,
                user_id=settings.petrol_user_id,
                client_role_id=settings.petrol_client_role_id,
                user_name=settings.petrol_user_name,
                user_password=settings.petrol_user_password,
                timeout=180,
                proxy_url=settings.petrol_proxy_url or None,
            )

            frames: list[pd.DataFrame] = []
            chunk_count = 0
            for chunk_start, chunk_end in _iter_petrol_chunks(start_dt, end_dt, chunk_days=PETROL_CHUNK_DAYS):
                payload = client.get_sales_with_invoice_infos(
                    start_date=chunk_start,
                    end_date=chunk_end,
                    fleet_id=settings.petrol_fleet_id or settings.petrol_fleet_list or None,
                    holding_id=settings.petrol_holding_id,
                )
                events = _petrol_payload_to_events(payload)
                if not events.empty:
                    frames.append(events)
                chunk_count += 1

            all_events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            rows_loaded = save_events(db, all_events)
            detail = f'api GET_SALES_WITH_INVOICE_INFOS {start_dt} -> {end_dt}, chunks={chunk_count}'
            if settings.petrol_proxy_url:
                detail += ', proxy=enabled'
            _track_run_finish(db, run, rows_loaded, detail=detail)
            return SourceSyncResult(source='petrol', rows_loaded=rows_loaded, detail=detail)

        path = None
        if settings.petrol_input_path:
            path = Path(settings.petrol_input_path)
        elif settings.petrol_input_dir:
            path = newest_matching_file(settings.petrol_input_dir, settings.petrol_glob)

        if not path or not path.exists():
            detail = 'Petrol API credentials not configured and Petrol file not found'
            _track_run_finish(db, run, 0, status='skipped', detail=detail)
            return SourceSyncResult(source='petrol', rows_loaded=0, detail=detail)

        raw = read_tabular_file(path)
        events = normalize_petrol_df(raw)
        rows_loaded = save_events(db, events)

        detail = f'file {path}'
        _track_run_finish(db, run, rows_loaded, detail=detail)
        return SourceSyncResult(source='petrol', rows_loaded=rows_loaded, detail=detail)

    except requests.exceptions.ReadTimeout as exc:
        db.rollback()
        detail = f'Petrol API timeout: {exc}'
        _track_run_finish(db, run, 0, status='error', detail=detail)
        return SourceSyncResult(source='petrol', rows_loaded=0, detail=detail)
    except requests.exceptions.RequestException as exc:
        db.rollback()
        detail = f'Petrol request error: {exc}'
        _track_run_finish(db, run, 0, status='error', detail=detail)
        return SourceSyncResult(source='petrol', rows_loaded=0, detail=detail)
    except Exception as exc:
        db.rollback()
        _track_run_finish(db, run, 0, status='error', detail=str(exc))
        return SourceSyncResult(source='petrol', rows_loaded=0, detail=f'error: {exc}')


def sync_turpak(db: Session) -> SourceSyncResult:
    run = _track_run_start(db, 'turpak')
    try:
        if not settings.turpak_company_name or not settings.turpak_password:
            detail = 'Turpak credentials not configured'
            _track_run_finish(db, run, 0, status='skipped', detail=detail)
            return SourceSyncResult(source='turpak', rows_loaded=0, detail=detail)

        start_dt, end_dt = _format_turpak_window(db)
        client = TurpakClient(
            base_url=settings.turpak_base_url,
            company_name=settings.turpak_company_name,
            password=settings.turpak_password,
        )
        sales = client.get_sales(start_dt=start_dt, end_dt=end_dt, group_name=settings.turpak_group_name)
        events = normalize_turpak_sales(sales)
        rows_loaded = save_events(db, events)

        detail = f'{start_dt} -> {end_dt}'
        _track_run_finish(db, run, rows_loaded, detail=detail)
        return SourceSyncResult(source='turpak', rows_loaded=rows_loaded, detail=detail)

    except Exception as exc:
        db.rollback()
        _track_run_finish(db, run, 0, status='error', detail=str(exc))
        return SourceSyncResult(source='turpak', rows_loaded=0, detail=f'error: {exc}')


def sync_all(db: Session, build_report: bool = True, send_report: bool = False) -> tuple[list[SourceSyncResult], str | None]:
    results: list[SourceSyncResult] = []

    zeroed = _zero_turpak_amounts(db)

    if settings.shell_enabled:
        results.append(sync_shell(db))
    if settings.petrol_enabled:
        results.append(sync_petrol(db))
    if settings.turpak_enabled:
        results.append(sync_turpak(db))

    if zeroed:
        _zero_turpak_amounts(db)

    report_path: str | None = None
    if build_report:
        year_month = current_year_month()
        summary = build_monthly_vehicle_summary(db, year_month=year_month)
        details = fetch_events(db, year_month=year_month, limit=5000)

        alert_candidates = build_alert_candidates(summary, year_month=year_month)
        unsent = filter_unsent_alerts(db, alert_candidates)
        dispatch_alerts(db, unsent)
        alert_registry = refresh_alert_state(db, summary, year_month)

        driver_registry = load_driver_registry()

        output = settings.report_output_path / f'fuel_report_{year_month}.xlsx'
        export_report(output, summary, details, alert_registry, driver_registry=driver_registry)
        report_path = str(output)

        if send_report:
            send_telegram_document(output, caption=f'Fuel monitor report {year_month}')

    return results, report_path
