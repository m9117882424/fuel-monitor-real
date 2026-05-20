-- PostgreSQL migration for advanced vehicle limits v2
-- Safe to run multiple times where possible.

ALTER TABLE vehicle_limits
    ADD COLUMN IF NOT EXISTS limit_mode VARCHAR(16) NOT NULL DEFAULT 'combined';

ALTER TABLE vehicle_limits
    ADD COLUMN IF NOT EXISTS unlimited BOOLEAN NOT NULL DEFAULT FALSE;

-- Some early deployments created vehicle_limits.unlimited as BIGINT.
-- Normalize it to BOOLEAN so SQLAlchemy Boolean writes true/false correctly.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'vehicle_limits'
          AND column_name = 'unlimited'
          AND table_schema = current_schema()
          AND data_type <> 'boolean'
    ) THEN
        ALTER TABLE vehicle_limits
            ALTER COLUMN unlimited DROP DEFAULT,
            ALTER COLUMN unlimited TYPE BOOLEAN
                USING CASE
                    WHEN unlimited IS NULL THEN FALSE
                    WHEN unlimited::TEXT IN ('1', 't', 'true', 'TRUE', 'yes', 'YES', 'on', 'ON') THEN TRUE
                    ELSE FALSE
                END,
            ALTER COLUMN unlimited SET DEFAULT FALSE,
            ALTER COLUMN unlimited SET NOT NULL;
    END IF;
END $$;

ALTER TABLE vehicle_limits
    ADD COLUMN IF NOT EXISTS combined_limit_liters NUMERIC(12, 2);

ALTER TABLE vehicle_limits
    ADD COLUMN IF NOT EXISTS turpak_limit_liters NUMERIC(12, 2);

ALTER TABLE vehicle_limits
    ADD COLUMN IF NOT EXISTS cards_limit_liters NUMERIC(12, 2);

UPDATE vehicle_limits
SET combined_limit_liters = monthly_limit_liters
WHERE combined_limit_liters IS NULL
  AND monthly_limit_liters IS NOT NULL;

CREATE TABLE IF NOT EXISTS alert_log (
    id SERIAL PRIMARY KEY,
    year_month VARCHAR(7) NOT NULL,
    plate VARCHAR(32) NOT NULL,
    limit_bucket VARCHAR(16) NOT NULL,
    threshold_pct INTEGER NOT NULL,
    usage_pct DOUBLE PRECISION NOT NULL,
    remaining_liters DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL,
    sent_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT ux_alert_month_plate_bucket_threshold UNIQUE (year_month, plate, limit_bucket, threshold_pct)
);

CREATE TABLE IF NOT EXISTS alert_state (
    id SERIAL PRIMARY KEY,
    year_month VARCHAR(7) NOT NULL,
    plate VARCHAR(32) NOT NULL,
    limit_bucket VARCHAR(16) NOT NULL,
    first_threshold_pct INTEGER NOT NULL,
    max_threshold_pct INTEGER NOT NULL,
    usage_pct DOUBLE PRECISION NOT NULL,
    remaining_liters DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL,
    limit_liters DOUBLE PRECISION NOT NULL DEFAULT 0,
    consumed_liters DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_amount_try DOUBLE PRECISION NOT NULL DEFAULT 0,
    mode VARCHAR(16),
    unlimited BOOLEAN NOT NULL DEFAULT FALSE,
    sources VARCHAR(255),
    last_event_dt TIMESTAMP WITHOUT TIME ZONE NULL,
    first_triggered_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT ux_alert_state_month_plate_bucket UNIQUE (year_month, plate, limit_bucket)
);

CREATE INDEX IF NOT EXISTS ix_alert_log_year_month ON alert_log(year_month);
CREATE INDEX IF NOT EXISTS ix_alert_log_plate ON alert_log(plate);
CREATE INDEX IF NOT EXISTS ix_alert_log_bucket ON alert_log(limit_bucket);
CREATE INDEX IF NOT EXISTS ix_alert_state_year_month ON alert_state(year_month);
CREATE INDEX IF NOT EXISTS ix_alert_state_plate ON alert_state(plate);
CREATE INDEX IF NOT EXISTS ix_alert_state_bucket ON alert_state(limit_bucket);
