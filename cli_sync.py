from __future__ import annotations

from app.db import Base, SessionLocal, engine
from app.services.sync_service import sync_all


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        results, report_path = sync_all(db, build_report=True, send_report=True)
        print('Results:')
        for r in results:
            print(r)
        print('Report:', report_path)
        return 1 if any(result.status == 'error' for result in results) else 0
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())
