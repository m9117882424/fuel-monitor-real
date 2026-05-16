from sqlalchemy import text
from app.db import engine

sql_statements = [
    """
    CREATE TABLE IF NOT EXISTS alert_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year_month TEXT NOT NULL,
        plate TEXT NOT NULL,

        first_threshold_pct INTEGER NOT NULL,
        max_threshold_pct INTEGER NOT NULL,

        usage_pct REAL NOT NULL,
        remaining_liters REAL NOT NULL,
        status TEXT NOT NULL,

        monthly_limit_liters REAL NOT NULL DEFAULT 0,
        total_liters REAL NOT NULL DEFAULT 0,
        total_amount_try REAL NOT NULL DEFAULT 0,

        sources TEXT,
        last_event_dt TEXT NULL,

        first_triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        CONSTRAINT ux_alert_state_month_plate UNIQUE (year_month, plate)
    )
    """,
    'CREATE INDEX IF NOT EXISTS ix_alert_state_year_month ON alert_state(year_month)',
    'CREATE INDEX IF NOT EXISTS ix_alert_state_plate ON alert_state(plate)',
    'CREATE INDEX IF NOT EXISTS ix_alert_state_status ON alert_state(status)',
]

with engine.begin() as conn:
    for sql in sql_statements:
        conn.execute(text(sql))

print('alert_state created')
