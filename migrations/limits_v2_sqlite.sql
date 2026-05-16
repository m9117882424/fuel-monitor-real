-- SQLite migration for advanced vehicle limits v2
PRAGMA foreign_keys = OFF;

ALTER TABLE vehicle_limits ADD COLUMN limit_mode TEXT NOT NULL DEFAULT 'combined';
ALTER TABLE vehicle_limits ADD COLUMN unlimited INTEGER NOT NULL DEFAULT 0;
ALTER TABLE vehicle_limits ADD COLUMN combined_limit_liters REAL;
ALTER TABLE vehicle_limits ADD COLUMN turpak_limit_liters REAL;
ALTER TABLE vehicle_limits ADD COLUMN cards_limit_liters REAL;

UPDATE vehicle_limits
SET combined_limit_liters = monthly_limit_liters
WHERE combined_limit_liters IS NULL;

DROP TABLE IF EXISTS alert_log;
DROP TABLE IF EXISTS alert_state;

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month TEXT NOT NULL,
    plate TEXT NOT NULL,
    limit_bucket TEXT NOT NULL,
    threshold_pct INTEGER NOT NULL,
    usage_pct REAL NOT NULL,
    remaining_liters REAL NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_alert_month_plate_bucket_threshold UNIQUE (year_month, plate, limit_bucket, threshold_pct)
);

CREATE TABLE IF NOT EXISTS alert_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month TEXT NOT NULL,
    plate TEXT NOT NULL,
    limit_bucket TEXT NOT NULL,
    first_threshold_pct INTEGER NOT NULL,
    max_threshold_pct INTEGER NOT NULL,
    usage_pct REAL NOT NULL,
    remaining_liters REAL NOT NULL,
    status TEXT NOT NULL,
    limit_liters REAL NOT NULL DEFAULT 0,
    consumed_liters REAL NOT NULL DEFAULT 0,
    total_amount_try REAL NOT NULL DEFAULT 0,
    mode TEXT,
    unlimited INTEGER NOT NULL DEFAULT 0,
    sources TEXT,
    last_event_dt TEXT NULL,
    first_triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ux_alert_state_month_plate_bucket UNIQUE (year_month, plate, limit_bucket)
);

CREATE INDEX IF NOT EXISTS ix_alert_log_year_month ON alert_log(year_month);
CREATE INDEX IF NOT EXISTS ix_alert_log_plate ON alert_log(plate);
CREATE INDEX IF NOT EXISTS ix_alert_log_bucket ON alert_log(limit_bucket);
CREATE INDEX IF NOT EXISTS ix_alert_state_year_month ON alert_state(year_month);
CREATE INDEX IF NOT EXISTS ix_alert_state_plate ON alert_state(plate);
CREATE INDEX IF NOT EXISTS ix_alert_state_bucket ON alert_state(limit_bucket);

PRAGMA foreign_keys = ON;
