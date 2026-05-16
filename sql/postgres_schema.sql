CREATE TABLE IF NOT EXISTS fuel_events (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NULL,
    event_key TEXT NOT NULL UNIQUE,
    event_dt TIMESTAMP NOT NULL,
    year_month TEXT NOT NULL,
    plate TEXT NOT NULL,
    fuel_type_raw TEXT NULL,
    fuel_type_norm TEXT NULL,
    liters NUMERIC(12,3) NOT NULL DEFAULT 0,
    unit_price_try NUMERIC(12,4) NOT NULL DEFAULT 0,
    amount_try NUMERIC(14,2) NOT NULL DEFAULT 0,
    discount_try NUMERIC(14,2) NOT NULL DEFAULT 0,
    station_code TEXT NULL,
    station_name TEXT NULL,
    station_city TEXT NULL,
    receipt_no TEXT NULL,
    card_no TEXT NULL,
    card_type TEXT NULL,
    group_name TEXT NULL,
    odometer NUMERIC(14,2) NOT NULL DEFAULT 0,
    sale_type TEXT NULL,
    department_code TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_fuel_events_year_month ON fuel_events(year_month);
CREATE INDEX IF NOT EXISTS ix_fuel_events_plate ON fuel_events(plate);
CREATE INDEX IF NOT EXISTS ix_fuel_events_source ON fuel_events(source);

CREATE TABLE IF NOT EXISTS vehicle_limits (
    id BIGSERIAL PRIMARY KEY,
    plate TEXT NOT NULL UNIQUE,
    monthly_limit_liters NUMERIC(12,2) NOT NULL,
    group_name TEXT NULL,
    note TEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alert_log (
    id BIGSERIAL PRIMARY KEY,
    year_month TEXT NOT NULL,
    plate TEXT NOT NULL,
    threshold_pct INTEGER NOT NULL,
    usage_pct DOUBLE PRECISION NOT NULL,
    remaining_liters DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL,
    sent_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ux_alert_month_plate_threshold UNIQUE (year_month, plate, threshold_pct)
);
