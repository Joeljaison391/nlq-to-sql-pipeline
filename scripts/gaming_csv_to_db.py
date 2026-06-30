import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "gaming_mental_health_10M_40features.csv"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "gaming_mental_health.sqlite"
DEFAULT_TABLE_NAME = "gaming_mental_health"

SQLITE_TYPE_MAP = {
    "int64": "INTEGER",
    "float64": "REAL",
    "bool": "INTEGER",
    "datetime64[ns]": "TEXT",
    "object": "TEXT",
}


def map_pd_dtype_to_sql(dtype) -> str:
    key = str(dtype)
    return SQLITE_TYPE_MAP.get(key, "TEXT")


def create_table_from_df(conn, table_name, df, if_exists="fail"):
    cursor = conn.cursor()

    if if_exists == "replace":
        cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        conn.commit()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    table_exists = cursor.fetchone() is not None

    if table_exists:
        if if_exists == "fail":
            raise ValueError(f"Table '{table_name}' already exists.")
        elif if_exists == "append":
            return

    cols = []
    for col in df.columns:
        coltype = map_pd_dtype_to_sql(df[col].dtype)
        safe_col = col.replace('"', '""')
        cols.append(f'"{safe_col}" {coltype}')

    cursor.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(cols)})')
    conn.commit()


def insert_chunk(conn, table_name, df):
    cursor = conn.cursor()
    cols = ['"{}"'.format(c.replace('"', '""')) for c in df.columns]
    placeholders = ",".join(["?"] * len(df.columns))
    sql = f'INSERT INTO "{table_name}" ({",".join(cols)}) VALUES ({placeholders})'
    rows = [tuple(None if pd.isna(x) else x for x in row) for row in df.itertuples(index=False, name=None)]
    cursor.executemany(sql, rows)
    conn.commit()


def csv_to_sqlite(csv_path, db_path, table_name, if_exists="fail", chunksize=50000):
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    total_rows = 0

    try:
        first = True
        for chunk in pd.read_csv(csv_path, chunksize=chunksize, low_memory=False):
            if first:
                create_table_from_df(conn, table_name, chunk, if_exists=if_exists)
                first = False
            insert_chunk(conn, table_name, chunk)
            total_rows += len(chunk)
            print(f"Loaded {total_rows} rows so far...")

        print(f"Done. {total_rows} rows loaded into '{table_name}'")
    finally:
        conn.close()


def verify_database(db_path, table_name):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    total_rows = cursor.fetchone()[0]
    print(f"\nTotal rows: {total_rows:,}")

    try:
        cursor.execute(f'SELECT gender, COUNT(*) as count FROM "{table_name}" GROUP BY gender ORDER BY count DESC')
        for gender, count in cursor.fetchall():
            print(f"  {gender}: {count:,}")
    except sqlite3.OperationalError:
        pass

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Convert Gaming Mental Health CSV to SQLite.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--table", default=DEFAULT_TABLE_NAME)
    parser.add_argument("--if-exists", choices=["replace", "append", "fail"], default="fail")
    parser.add_argument("--chunksize", type=int, default=50000)
    parser.add_argument("--verify", action="store_true", default=True)

    args = parser.parse_args()

    try:
        csv_to_sqlite(args.csv, args.db, args.table, if_exists=args.if_exists, chunksize=args.chunksize)
        if args.verify:
            verify_database(args.db, args.table)
        print("Conversion completed successfully!")
        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
