"""
Load verified fictional score data into the queryable table
"""

import pandas as pd
import sqlite3


CSV_PATH = "data/scores_extended.csv"
DB_PATH = "data/scores.db"

SCHEMA = """
DROP TABLE IF EXISTS scores;

CREATE TABLE scores (
    account_id  INTEGER NOT NULL,
    period      TEXT    NOT NULL,   -- ISO date 'YYYY-MM-DD'
    score       INTEGER NOT NULL,
    bin         INTEGER NOT NULL,
    market      INTEGER NOT NULL,
    PRIMARY KEY (account_id, period)
);

CREATE INDEX idx_scores_market ON scores(market);
"""



def load_csv(csv_path: str = CSV_PATH) -> pd.DataFrame:
    """Reads the ';'-delimited score CSV into a DataFrame."""
    df = pd.read_csv(csv_path, sep=";")
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Parses the DD.MM.YYYY period strings into real dates and sorts the data."""
    df = df.copy()
    # print(df.columns.tolist())
    # print(df.head(2))

    # Read the 'period' dates. They arrive as text such as '01.12.1991'
    df["period"] = pd.to_datetime(df["period"], format="%d.%m.%Y")
    # The output dates will be "%Y-%m-%d", so they're ready to be sorted properly.

    # Sort so each customer's history reads oldest -> newest
    df = df.sort_values(["account_id", "period"]).reset_index(drop=True)
    return df



def load_to_db(df: pd.DataFrame,db_path: str = DB_PATH) -> None:
    """Rebuilds the 'scores' table and fills it with the DataFrame's rows."""
    df = df.copy()
    # SQLite has no date type, so store the date as ISO text
    df["period"] = df["period"].dt.strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA) # drops and recreates the table
        df.to_sql("scores", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from src.synthetic_dataset import DatasetConfig, generate_dataset, OUTPUT_NAMES
    from src.synthetic_database import build_database
    import os
    import shutil

    data = Path("data")
    data.mkdir(exist_ok=True)
    existing = [(data / name).exists() for name in OUTPUT_NAMES]
    if any(existing) and not all(existing):
        raise ValueError("Incomplete synthetic input bundle in data/; restore all three verified inputs")
    with TemporaryDirectory() as temporary:
        if not any(existing):
            inputs = Path(temporary) / "inputs"
            generate_dataset(DatasetConfig(), inputs)
        else:
            inputs = data
        output = Path(temporary) / "verified"
        receipt = build_database(inputs, output)
        if inputs != data:
            for name in OUTPUT_NAMES:
                shutil.copyfile(inputs / name, data / name)
        # Stage in the same directory and close file handles before replacement.
        with TemporaryDirectory(prefix=".build-", dir=data) as publication:
            for name in ("scores.db", "database_receipt.json"):
                staged = Path(publication) / name
                shutil.copyfile(output / name, staged)
                os.replace(staged, data / name)
    print(f"Verified synthetic database: {receipt['accounts']} accounts, {receipt['rows']} rows")
