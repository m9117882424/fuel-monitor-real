from __future__ import annotations

from sqlalchemy import text

from app.db import engine


INDEX_STATEMENTS = [
    """
    CREATE INDEX IF NOT EXISTS ix_fuel_events_month_plate
    ON fuel_events(year_month, plate)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fuel_events_month_source
    ON fuel_events(year_month, source)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fuel_events_month_source_plate
    ON fuel_events(year_month, source, plate)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_fuel_events_plate_month_dt
    ON fuel_events(plate, year_month, event_dt)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_import_runs_source_started
    ON import_runs(source, started_at)
    """,
]


def main() -> int:
    with engine.begin() as conn:
        for statement in INDEX_STATEMENTS:
            conn.execute(text(statement))

        dialect = engine.dialect.name.lower()
        if dialect == "sqlite":
            conn.execute(text("ANALYZE"))
        elif dialect == "postgresql":
            conn.execute(text("ANALYZE fuel_events"))
            conn.execute(text("ANALYZE import_runs"))

    print("Dashboard performance indexes are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
