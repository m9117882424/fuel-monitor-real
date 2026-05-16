from pathlib import Path
import sqlite3
from app.config import settings

SQL_PATH = Path(__file__).resolve().parent / 'migrations' / 'limits_v2_sqlite.sql'


def main():
    db_url = settings.database_url
    if not db_url.startswith('sqlite:///'):
        raise RuntimeError('This helper is intended for SQLite only')
    db_path = db_url.replace('sqlite:///', '', 1)
    if db_path.startswith('/'):
        sqlite_path = db_path
    else:
        sqlite_path = str((Path(__file__).resolve().parent / db_path).resolve())
    sql = SQL_PATH.read_text(encoding='utf-8')
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.executescript(sql)
        conn.commit()
        print(f'Migration applied to {sqlite_path}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
