-- PostgreSQL cleanup for duplicated Shell fuel events.
-- Duplicates can appear when the same Shell transaction is imported through
-- different paths and receives different event_key/card/receipt metadata.
-- Natural transaction identity for Shell is: plate + datetime + liters + amount + fuel type + station.

WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY
                source,
                plate,
                event_dt,
                ROUND(liters::numeric, 3),
                ROUND(amount_try::numeric, 2),
                COALESCE(fuel_type_norm, ''),
                COALESCE(NULLIF(TRIM(station_name), ''), '')
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
    event_dt,
    ROUND(liters::numeric, 3),
    ROUND(amount_try::numeric, 2),
    COALESCE(fuel_type_norm, ''),
    COALESCE(NULLIF(TRIM(station_name), ''), '')
)
WHERE source = 'shell_excel';
