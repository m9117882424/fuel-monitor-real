from app.db import SessionLocal
from app.models import AlertLog, AlertState
from app.utils import current_year_month

db = SessionLocal()
try:
    ym = current_year_month()

    deleted_state = db.query(AlertState).filter(AlertState.year_month == ym).delete()
    deleted_log = db.query(AlertLog).filter(AlertLog.year_month == ym).delete()

    db.commit()
    print(f"year_month={ym}")
    print(f"deleted alert_state: {deleted_state}")
    print(f"deleted alert_log: {deleted_log}")
finally:
    db.close()