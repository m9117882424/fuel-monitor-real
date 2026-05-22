-- PostgreSQL cleanup for duplicated Shell fuel events.
-- Duplicates can appear when the same Shell transaction is imported through
-- different paths and receives different event_key/card/receipt metadata.
--
-- The dashboard displays datetimes to seconds, and imported rows may differ by
-- milliseconds, fuel_type_norm, card_no, receipt_no, or extra whitespace. For
-- Shell we therefore dedupe by the displayed natural transaction identity:
-- source + plate + second-level datetime + liters + amount + raw fuel + station.

DROP INDEX IF EXISTS ux_fuel_events_shell_natural_tx;

WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY
                source,
                plate,
                DATE_TRUNC('second', event_dt),
                ROUND(liters::numeric, 2),
                ROUND(amount_try::numeric, 2),
                LOWER(BTRIM(COALESCE(fuel_type_raw, ''))),
                LOWER(BTRIM(COALESCE(station_name, '')))
            ORDER BY id
        ) AS rn
    FROM fuel_events
    WHERE source = 'shell_excel'
)
DELETE FROM fuel_events f
USING ranked r
WHERE f.id = r.id
  AND r.rn > 1;

-- Prevent the same Shell natural transaction from being inserted again.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fuel_events_shell_natural_tx
ON fuel_events (
    source,
    plate,
    DATE_TRUNC('second', event_dt),
    ROUND(liters::numeric, 2),
    ROUND(amount_try::numeric, 2),
    LOWER(BTRIM(COALESCE(fuel_type_raw, ''))),
    LOWER(BTRIM(COALESCE(station_name, '')))
)
WHERE source = 'shell_excel';
