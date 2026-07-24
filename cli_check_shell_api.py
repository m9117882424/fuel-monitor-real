from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from sqlalchemy import func

from app.config import settings
from app.db import SessionLocal
from app.models import FuelEvent
from app.normalizers import normalize_shell_df
from app.sources.shell_tts import ShellTtsClient, shell_transactions_to_legacy_df
from app.utils import month_start_local, now_local


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Shell GetCustomerSalesTransaction_with_rid and GetOnlineTransaction "
            "without changing the database."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="Request the last N days instead of the current month",
    )
    return parser.parse_args()


def summarize(name: str, rows: list[dict], process_result: str) -> None:
    raw = shell_transactions_to_legacy_df(rows)
    events = normalize_shell_df(raw)

    print(f"\n=== {name} ===")
    print(f"PROCESSRESULT: {process_result!r}")
    print(f"Raw rows: {len(rows)}")
    print(f"Raw columns: {sorted(rows[0].keys()) if rows else []}")
    print(f"Valid normalized rows: {len(events.index)}")

    if events.empty:
        return

    print(f"Liters: {float(events['liters'].sum()):.2f}")
    print(f"First operation: {events['event_dt'].min()}")
    print(f"Last operation: {events['event_dt'].max()}")
    print(f"Rows with external ID: {int(events['external_id'].astype(str).str.strip().ne('').sum())}")
    print("Latest operations:")
    print(
        events[
            ['event_dt', 'plate', 'liters', 'amount_try', 'station_name', 'external_id']
        ]
        .sort_values('event_dt')
        .tail(15)
        .to_string(index=False)
    )


def main() -> int:
    args = parse_args()
    end_dt = now_local().replace(microsecond=0)
    start_dt = (
        end_dt - timedelta(days=max(1, args.days))
        if args.days
        else month_start_local(end_dt)
    )

    client = ShellTtsClient(
        base_url=settings.shell_base_url,
        customer_code=settings.shell_customer_code,
        user_id=settings.shell_user_id,
        password=settings.shell_password,
        branch_code=settings.shell_branch_code,
        timeout=settings.shell_timeout_seconds,
    )

    print(f"Requested interval: {start_dt.isoformat()} -> {end_dt.isoformat()}")

    sales_rows, sales_result = client.get_customer_sales_transactions(
        start_dt=start_dt,
        end_dt=end_dt,
        with_rid=True,
    )
    summarize('GetCustomerSalesTransaction_with_rid', sales_rows, sales_result)

    try:
        online_rows, online_result = client.get_online_transactions(
            start_dt=start_dt,
            end_dt=end_dt,
        )
        summarize('GetOnlineTransaction', online_rows, online_result)
    except Exception as exc:
        print(f"\n=== GetOnlineTransaction ===")
        print(f"ERROR: {exc}")

    db = SessionLocal()
    try:
        operations, liters, first_event, last_event = (
            db.query(
                func.count(FuelEvent.id),
                func.coalesce(func.sum(FuelEvent.liters), 0),
                func.min(FuelEvent.event_dt),
                func.max(FuelEvent.event_dt),
            )
            .filter(
                FuelEvent.source == 'shell_excel',
                FuelEvent.event_dt >= start_dt.replace(tzinfo=None),
                FuelEvent.event_dt <= end_dt.replace(tzinfo=None),
            )
            .one()
        )
        print("\n=== DATABASE ===")
        print(f"Operations: {int(operations or 0)}")
        print(f"Liters: {float(liters or 0):.2f}")
        print(f"First operation: {first_event}")
        print(f"Last operation: {last_event}")
    finally:
        db.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
