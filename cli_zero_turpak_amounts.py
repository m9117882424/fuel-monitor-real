from app.db import SessionLocal
from app.models import FuelEvent
from sqlalchemy import update


def main():
    db = SessionLocal()
    try:
        result = db.execute(
            update(FuelEvent)
            .where(FuelEvent.source == 'turpak')
            .values(unit_price_try=0, amount_try=0, discount_try=0)
        )
        db.commit()
        print(f'Updated rows: {int(result.rowcount or 0)}')
    finally:
        db.close()


if __name__ == '__main__':
    main()
