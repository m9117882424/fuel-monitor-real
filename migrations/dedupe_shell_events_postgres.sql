-- PostgreSQL cleanup for duplicated Shell fuel events.
-- Duplicates can appear when the same Shell transaction is imported through
-- different paths and receives different event_key/card/receipt/station metadata.
--
-- The UI displays all Shell-like sources as "Shell". Therefore source must not
-- be part of the duplicate key. Also, the same Shell transaction can arrive once
-- with station_name and once with an empty station_name shown as "—" in the UI.
-- Station is kept only as a preference when deciding which duplicate row remains.
--
-- Natural Shell transaction identity:
-- plate + second-level datetime + liters + amount + raw fuel.

DROP INDEX IF EXISTS ux_fuel_events_shell_natural_tx;

WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY
                plate,
                DATE_TRUNC('second', event_dt),
                ROUND(liters::numeric, 2),
                ROUND(amount_try::numeric, 2),
                LOWER(BTRIM(COALESCE(fuel_type_raw, '')))
            ORDER BY
                CASE WHEN NULLIF(BTRIM(COALESCE(station_name, '')), '') IS NOT NULL THEN 0 ELSE 1 END,
                CASE WHEN source = 'shell_excel' THEN 0 ELSE 1 END,
                id
        ) AS rn
    FROM fuel_events
    WHERE LOWER(COALESCE(source, '')) LIKE '%shell%'
)
DELETE FROM fuel_events f
USING ranked r
WHERE f.id = r.id
  AND r.rn > 1;

-- Prevent the same Shell natural transaction from being inserted again, even if
-- it arrives as a different Shell source variant or with missing station_name.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fuel_events_shell_natural_tx
ON fuel_events (
    plate,
    DATE_TRUNC('second', event_dt),
    ROUND(liters::numeric, 2),
    ROUND(amount_try::numeric, 2),
    LOWER(BTRIM(COALESCE(fuel_type_raw, '')))
)
WHERE LOWER(COALESCE(source, '')) LIKE '%shell%';
