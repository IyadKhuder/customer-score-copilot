"""
Loadin the behavioural score CSV into queryable table
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

    # Read the 'period' dates. They arrive as text such as '27.01.2025'
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
    df = clean_data(load_csv())
    load_to_db(df)

    # Quick verification: read back from the database
    conn = sqlite3.connect(DB_PATH)
    try:
        num_rows = conn.execute("SELECT count(*) FROM scores").fetchone()[0]
        num_accounts = conn.execute("SELECT count(DISTINCT account_id) FROM scores").fetchone()[0]
        first_period, last_period = conn.execute("SELECT MIN(period), MAX(period) FROM scores").fetchone()
        print(f"num rows: {num_rows} | num accounts: {num_accounts} | Periods: {first_period} -> {last_period}")

        print("\nSample rows for account 103480:")
        for row in conn.execute(
                "SELECT * FROM scores WHERE account_id = ? ORDER BY period LIMIT 5", (103480,)
        ):
            print(row)
    finally:
        conn.close()
