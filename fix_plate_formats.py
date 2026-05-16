from app.db import SessionLocal
from app.models import FuelEvent, VehicleLimit, AlertLog, AlertState
from app.utils import normalize_plate


def fix_model_rows(rows, attr_name: str):
    changed = 0
    for row in rows:
        old = getattr(row, attr_name, None)
        new = normalize_plate(old)
        if old != new:
            setattr(row, attr_name, new)
            changed += 1
    return changed


def main():
    db = SessionLocal()
    try:
        total = 0

        total += fix_model_rows(db.query(FuelEvent).all(), "plate")
        total += fix_model_rows(db.query(VehicleLimit).all(), "plate")
        total += fix_model_rows(db.query(AlertLog).all(), "plate")
        total += fix_model_rows(db.query(AlertState).all(), "plate")

        db.commit()
        print(f"updated rows: {total}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()