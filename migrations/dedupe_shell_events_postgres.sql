-- PostgreSQL cleanup and guard for duplicated Shell fuel events.
--
-- Root cause:
-- The same Shell transaction can be imported more than once through different
-- paths and metadata quality. One row may have station_name, another may have an
-- empty station_name displayed as "—". Event keys can differ, so the legacy
-- unique event_key does not always stop duplicates.
--
-- Shell natural transaction identity:
-- plate + second-level datetime + liters + amount + raw fuel.
-- station_name is intentionally NOT part of the key.

DROP TRIGGER IF EXISTS trg_fuel_events_shell_dedupe ON fuel_events;
DROP FUNCTION IF EXISTS fuel_events_shell_dedupe_fn();
DROP INDEX IF EXISTS ux_fuel_events_shell_natural_tx;
DROP INDEX IF EXISTS ix_fuel_events_shell_natural_tx;

-- One-time cleanup of existing duplicates. Keep the best row:
-- 1) row with station_name filled,
-- 2) source='shell_excel',
-- 3) lowest id.
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

-- Non-unique helper index for cleanup/trigger performance.
-- We use a trigger instead of a unique index because web uploads should remain
-- idempotent and not fail with IntegrityError when the same report is uploaded again.
CREATE INDEX IF NOT EXISTS ix_fuel_events_shell_natural_tx
ON fuel_events (
    plate,
    DATE_TRUNC('second', event_dt),
    ROUND(liters::numeric, 2),
    ROUND(amount_try::numeric, 2),
    LOWER(BTRIM(COALESCE(fuel_type_raw, '')))
)
WHERE LOWER(COALESCE(source, '')) LIKE '%shell%';

CREATE OR REPLACE FUNCTION fuel_events_shell_dedupe_fn()
RETURNS trigger AS $$
DECLARE
    preferred_id integer;
BEGIN
    IF LOWER(COALESCE(NEW.source, '')) NOT LIKE '%shell%' THEN
        RETURN NEW;
    END IF;

    SELECT id
    INTO preferred_id
    FROM fuel_events
    WHERE LOWER(COALESCE(source, '')) LIKE '%shell%'
      AND plate = NEW.plate
      AND DATE_TRUNC('second', event_dt) = DATE_TRUNC('second', NEW.event_dt)
      AND ROUND(liters::numeric, 2) = ROUND(NEW.liters::numeric, 2)
      AND ROUND(amount_try::numeric, 2) = ROUND(NEW.amount_try::numeric, 2)
      AND LOWER(BTRIM(COALESCE(fuel_type_raw, ''))) = LOWER(BTRIM(COALESCE(NEW.fuel_type_raw, '')))
    ORDER BY
      CASE WHEN NULLIF(BTRIM(COALESCE(station_name, '')), '') IS NOT NULL THEN 0 ELSE 1 END,
      CASE WHEN source = 'shell_excel' THEN 0 ELSE 1 END,
      id
    LIMIT 1;

    IF preferred_id IS NOT NULL THEN
        DELETE FROM fuel_events f
        WHERE LOWER(COALESCE(f.source, '')) LIKE '%shell%'
          AND f.plate = NEW.plate
          AND DATE_TRUNC('second', f.event_dt) = DATE_TRUNC('second', NEW.event_dt)
          AND ROUND(f.liters::numeric, 2) = ROUND(NEW.liters::numeric, 2)
          AND ROUND(f.amount_try::numeric, 2) = ROUND(NEW.amount_try::numeric, 2)
          AND LOWER(BTRIM(COALESCE(f.fuel_type_raw, ''))) = LOWER(BTRIM(COALESCE(NEW.fuel_type_raw, '')))
          AND f.id <> preferred_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_fuel_events_shell_dedupe
AFTER INSERT ON fuel_events
FOR EACH ROW
EXECUTE FUNCTION fuel_events_shell_dedupe_fn();
