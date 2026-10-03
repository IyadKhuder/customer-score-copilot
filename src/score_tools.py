"""
score_tools.py
The "Score Query Tool": fixed, safe functions that return exact numbers
from the scores database. Every function returns plain Python data
(dicts / lists) so results can later be sent to the LLM as JSON.
"""

import sqlite3
import statistics
from typing import Optional

DB_PATH = "data/scores.db"


def _connect() -> sqlite3.Connection:
    """Opens a connection whose rows can be accessed by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_accounts(market: Optional[int] = None) -> list[int]:
    """All account_ids in the database, optionally filtered to one market."""
    conn = _connect()
    try:
        if market is None:
            rows = conn.execute(
                "SELECT DISTINCT account_id FROM scores ORDER BY account_id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT account_id FROM scores WHERE market = ? ORDER BY account_id",
                (market,),
            ).fetchall()
    finally:
        conn.close()
    return [r["account_id"] for r in rows]


def get_latest_score(account_id: int) -> Optional[dict]:
    """Most recent score record for one customer, or None if unknown."""
    history = get_history(account_id)
    return history[-1] if history else None


def get_migrations(account_id: int) -> list[dict]:
    """
    Every month-over-month bin change for one customer, e.g.
    {'from_period': '2024-03-01', 'to_period': '2024-04-01',
     'from_bin': 7, 'to_bin': 6, 'from_score': 625, 'to_score': 609,
     'direction': 'down'}
    """
    history = get_history(account_id)
    migrations = []
    for prev, curr in zip(history, history[1:]):
        if prev["bin"] != curr["bin"]:
            migrations.append({
                "from_period": prev["period"],
                "to_period": curr["period"],
                "from_bin": prev["bin"],
                "to_bin": curr["bin"],
                "from_score": prev["score"],
                "to_score": curr["score"],
                "direction": "up" if curr["bin"] > prev["bin"] else "down",
            })
    return migrations


def compute_trend(account_id: int) -> Optional[dict]:
    """Summary stats for one customer's whole trajectory, or None if unknown."""
    history = get_history(account_id)
    if not history:
        return None

    scores = [row["score"] for row in history]
    migrations = get_migrations(account_id)

    # Sum of |bin change| across every migration: distinguishes a big single
    # jump (e.g. 6->9) from several small back-and-forth ones (e.g. 6->7->6),
    # even when both have the same migration *count*.
    bin_migration_magnitude = sum(abs(m["to_bin"] - m["from_bin"]) for m in migrations)

    # Where the account ended up relative to where it started, in bins.
    # Distinguishes churn (ends back where it started) from a real trend.
    net_bin_change = history[-1]["bin"] - history[0]["bin"]

    return {
        "account_id": account_id,
        "n_periods": len(history),
        "start_period": history[0]["period"],
        "end_period": history[-1]["period"],
        "start_score": scores[0],
        "end_score": scores[-1],
        "net_change": scores[-1] - scores[0],
        "min_score": min(scores),
        "max_score": max(scores),
        "volatility_stdev": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
        "n_bin_migrations": len(migrations),
        "bin_migration_magnitude": bin_migration_magnitude,
        "net_bin_change": net_bin_change,
    }


def print_trend(trend: Optional[dict]) -> None:
    """Pretty-prints a compute_trend() result, one field per line."""
    if trend is None:
        print("No data for this account.")
        return

    labels = [
        ("account_id",             "Account"),
        ("n_periods",              "Number of periods on record"),
        ("start_period",           "First period"),
        ("end_period",             "Last period"),
        ("start_score",            "Score at first period"),
        ("end_score",              "Score at last period"),
        ("net_change",             "Net score change (end - start)"),
        ("min_score",              "Minimum score"),
        ("max_score",              "Maximum score"),
        ("volatility_stdev",       "Score volatility (stdev)"),
        ("n_bin_migrations",       "Number of bin migrations"),
        ("bin_migration_magnitude","Sum of |bin change| across all migrations"),
        ("net_bin_change",         "Net bin change (end bin - start bin)"),
    ]
    width = max(len(label) for _, label in labels)
    for key, label in labels:
        print(f"  {label:<{width}} : {trend[key]}")


def get_market_average(market: int, period_start: Optional[str] = None,
                        period_end: Optional[str] = None) -> list[dict]:
    """
    Average score per period for a whole market, optionally bounded by
    period_start/period_end ('YYYY-MM-DD' strings, inclusive).
    """
    conn = _connect()
    try:
        query = """
            SELECT period, AVG(score) AS avg_score, COUNT(*) AS n_accounts
            FROM scores
            WHERE market = ?
        """
        params = [market]

        if period_start:
            query += " AND period >= ?"
            params.append(period_start)
        if period_end:
            query += " AND period <= ?"
            params.append(period_end)

        query += " GROUP BY period ORDER BY period"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [
        {"period": r["period"], "avg_score": round(r["avg_score"], 1), "n_accounts": r["n_accounts"]}
        for r in rows
    ]


def get_history(account_id: int) -> list[dict]:
    """Full monthly score/bin history for one customer, oldest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT period, score, bin, market
            FROM scores
            WHERE account_id = ?
            ORDER BY period
            """,
            (account_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]



# ---------------------------------------------------------------------------
# Tool schemas for the Anthropic Messages API (tool-use / function calling)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "list_accounts",
        "description": "List all customer account_ids, optionally filtered to one market.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {"type": "integer", "description": "Optional market id filter"},
            },
        },
    },
    {
        "name": "get_history",
        "description": "Get a customer's full monthly score and bin history, oldest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_latest_score",
        "description": "Get a customer's most recent score record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_migrations",
        "description": "Get every month-over-month bin change for a customer, with direction (up/down).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "compute_trend",
        "description": (
            "Get summary trend stats for a customer: net score change, volatility, "
            "min/max score, number of bin migrations, total bin-migration magnitude, "
            "and net bin change from start to end."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_market_average",
        "description": "Get the average score per period for a whole market, optionally bounded by a period range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market": {"type": "integer"},
                "period_start": {"type": "string", "description": "'YYYY-MM-DD' format"},
                "period_end": {"type": "string", "description": "'YYYY-MM-DD' format"},
            },
            "required": ["market"],
        },
    },
]

# Maps tool name -> callable, so an agent loop can dispatch on tool_use blocks
TOOL_DISPATCH = {
    "list_accounts": list_accounts,
    "get_history": get_history,
    "get_latest_score": get_latest_score,
    "get_migrations": get_migrations,
    "compute_trend": compute_trend,
    "get_market_average": get_market_average,
}



if __name__ == "__main__":
    # print("All accounts:", list_accounts())

    # history = get_history(103480)
    # print(f"\nAccount 103480: {len(history)} periods")
    # for row in history:
    #     print(row)

    # print("\nUnknown account:", get_history(999999))
    #
    # print("\nLatest score for 103480:", get_latest_score(103480))
    # print("Latest score for unknown account:", get_latest_score(999999))
    #
    # print("\nMigrations for 103480:")
    # for m in get_migrations(103480):
    #     print(m)

    # print("\nMigrations for 400000:")
    # for m in get_migrations(400000):
    #     print(m)

    # print("\nMigrations for unknown account:", get_migrations(999999))

    # print("\nTrend for 400003:", compute_trend(400003)) # 400000 #103480 #400003
    # print("Trend for unknown account:", compute_trend(999999))
    #
    # print("\nTrend for 103480:")
    # print_trend(compute_trend(103480))
    #
    # print("\nTrend for unknown account:")
    # print_trend(compute_trend(999999))
    # print("\nMarket 1 average (last 3 periods):")
    # for row in get_market_average(1, period_start="2025-02-01"):
    #     print(row)
    #
    # print("\nMarket 3 average (full range):")
    # for row in get_market_average(3):
    #     print(row)
    print("All accounts:", list_accounts())

    history = get_history(103480)
    print(f"\nAccount 103480: {len(history)} periods")
    for row in history:
        print(row)

    print("\nUnknown account:", get_history(999999))

    print("\nTrend for 103480:")
    print_trend(compute_trend(103480))

    print("\nTrend for unknown account:")
    print_trend(compute_trend(999999))

    # --- Step 2.7 check: simulate how an agent would call a tool ---
    print("\n--- Simulated tool_use dispatch ---")
    simulated_call = {"name": "get_migrations", "input": {"account_id": 103480}}
    fn = TOOL_DISPATCH[simulated_call["name"]]
    result = fn(**simulated_call["input"])
    print(f"Called {simulated_call['name']}({simulated_call['input']}) -> {result}")

    print(f"\n{len(TOOL_SCHEMAS)} tool schemas defined:")
    for schema in TOOL_SCHEMAS:
        print(f"  - {schema['name']}")


