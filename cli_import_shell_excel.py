from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import func

from app.db import Base, SessionLocal, engine
from app.io_utils import read_shell_excel
from app.models import FuelEvent
from app.normalizers import normalize_shell_df
from app.services.storage import save_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Shell transactions from an Excel export. Existing events are skipped by event_key."
    )
    parser.add_argument("file", type=Path, help="Path to the Shell Excel file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.file.expanduser().resolve()

    if not path.exists() or not path.is_file():
        raise SystemExit(f"File not found: {path}")
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        raise SystemExit("Only .xlsx and .xls files are supported")

    raw = read_shell_excel(path)
    events = normalize_shell_df(raw)
    if events.empty:
        raise SystemExit(
            "No valid Shell transactions found. Expected sheet 'items' and standard Shell export columns."
        )

    file_rows = int(len(events.index))
    file_liters = float(events["liters"].sum())
    months = sorted({str(value) for value in events["year_month"].dropna().tolist() if str(value)})

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        inserted = save_events(db, events)

        print(f"File: {path}")
        print(f"Valid Shell rows in file: {file_rows}")
        print(f"Liters in file: {file_liters:.2f}")
        print(f"Inserted new rows: {inserted}")
        print(f"Skipped existing rows: {file_rows - inserted}")

        for year_month in months:
            operations, liters, last_event = (
                db.query(
                    func.count(FuelEvent.id),
                    func.coalesce(func.sum(FuelEvent.liters), 0),
                    func.max(FuelEvent.event_dt),
                )
                .filter(
                    FuelEvent.source == "shell_excel",
                    FuelEvent.year_month == year_month,
                )
                .one()
            )
            print(
                f"Database Shell {year_month}: operations={int(operations or 0)}, "
                f"liters={float(liters or 0):.2f}, last_event={last_event}"
            )
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
