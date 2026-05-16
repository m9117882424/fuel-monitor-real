from __future__ import annotations

from app.db import Base, SessionLocal, engine
from app.services.sync_service import sync_all


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        results, report_path = sync_all(db, build_report=True, send_report=True)
        print('Results:')
        for r in results:
            print(r)
        print('Report:', report_path)
    finally:
        db.close()
