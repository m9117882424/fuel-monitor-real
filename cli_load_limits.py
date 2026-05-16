from __future__ import annotations

import argparse
import pandas as pd

from app.db import Base, SessionLocal, engine
from app.services.storage import upsert_limits


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--limits-csv', required=True)
    return p.parse_args()


if __name__ == '__main__':
    args = parse_args()
    Base.metadata.create_all(bind=engine)
    df = pd.read_csv(args.limits_csv)
    db = SessionLocal()
    try:
        count = upsert_limits(db, df.to_dict(orient='records'))
        print(f'Loaded {count} limits')
    finally:
        db.close()
