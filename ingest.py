import os
import pandas as pd
from init_db import get_connection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Upsert functions return the relevant ID for use in foreign keys.
# Inserts or updates pollster record in a SQLite database, returning the pollster ID.
def upsert_pollster(cur, name, numeric_grade=None, partisan=None):
    try:
        credibility = float(numeric_grade) if pd.notna(numeric_grade) else 1.0
    except (ValueError, TypeError):
        credibility = 1.0
    partisan_lean = "U" if str(partisan).strip() == "1" else None
    cur.execute("""
        INSERT INTO pollsters (name, credibility, partisan_lean)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            credibility   = excluded.credibility,
            partisan_lean = excluded.partisan_lean
    """, (name, credibility, partisan_lean))
    cur.execute("SELECT id FROM pollsters WHERE name = ?", (name,))
    return cur.fetchone()[0]

def upsert_race(cur, year, state):
    cur.execute("""
        INSERT INTO races (year, state)
        VALUES (?, ?)
        ON CONFLICT(year, state) DO NOTHING
    """, (year, state))
    cur.execute("SELECT id FROM races WHERE year = ? AND state = ?", (year, state))
    return cur.fetchone()[0]

def upsert_candidate(cur, race_id, name, party):
    cur.execute("""
        INSERT INTO candidates (race_id, name, party)
        VALUES (?, ?, ?)
        ON CONFLICT(race_id, name) DO UPDATE SET party = excluded.party
    """, (race_id, name, party))
    cur.execute("SELECT id FROM candidates WHERE race_id = ? AND name = ?", (race_id, name))
    return cur.fetchone()[0]

# ---------------------------------------------------------------------------
# FiveThirtyEight / NYT senate polls CSV
# NOTE: 538 shut down March 2025. Update senate.csv manually from:
# https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv
# ---------------------------------------------------------------------------

def load_nyt_senate_polls(filepath, year=2026):
    df = pd.read_csv(filepath)

    # Only general election, only major parties, only real candidates
    df = df[
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP"])) &
        (df["candidate_name"] != "Don't know") &
        (df["candidate_name"] != "Someone else")
    ].copy()

    # Prefer likely voters; fall back to registered voters, then all adults
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    df["pop_rank"] = df["population"].map(pop_priority).fillna(9)

    # For each poll_id + candidate, keep only the best population group
    df = df.sort_values("pop_rank")
    df = df.drop_duplicates(subset=["poll_id", "candidate_name"], keep="first")

    # Normalize party to single letter
    df["party"] = df["party"].map({"DEM": "D", "REP": "R"})

    # Parse end_date to YYYY-MM-DD
    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])
    df["end_date"] = df["end_date"].dt.strftime("%Y-%m-%d")

    con = get_connection()
    cur = con.cursor()

    loaded = 0
    for _, row in df.iterrows():
        state         = str(row["state"]).strip().upper()
        candidate     = str(row["candidate_name"]).strip()
        party         = row["party"]
        pct           = float(row["pct"])
        poll_date     = row["end_date"]
        sample_size   = int(row["sample_size"]) if pd.notna(row["sample_size"]) else None
        pollster_name = str(row["pollster"]).strip()
        numeric_grade = row.get("numeric_grade")
        partisan      = row.get("partisan")

        pollster_id  = upsert_pollster(cur, pollster_name, numeric_grade, partisan)
        race_id      = upsert_race(cur, year, state)
        candidate_id = upsert_candidate(cur, race_id, candidate, party)

        cur.execute("""
            INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, sample_size, pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (race_id, candidate_id, pollster_id, poll_date, sample_size, pct))
        loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} poll entries from {filepath}")


def load_climate_factors(filepath):
    """
    Hand-authored CSV with columns: year, factor_name, value
    e.g. 2026, PRES_APPROVAL, 44.5
    """
    df = pd.read_csv(filepath)
    df.columns = df.columns.str.strip()

    con = get_connection()
    cur = con.cursor()

    for _, row in df.iterrows():
        cur.execute("""
            INSERT INTO climate_factors (year, factor_name, value)
            VALUES (?, ?, ?)
            ON CONFLICT(year, factor_name) DO UPDATE SET value = excluded.value
        """, (int(row["year"]), str(row["factor_name"]).strip(), float(row["value"])))

    con.commit()
    con.close()
    print(f"Loaded climate factors: {len(df)} rows")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DATA = os.path.join(os.path.dirname(__file__), "data")

    senate_path = os.path.join(DATA, "senate.csv")

    if not os.path.exists(senate_path):
        print(f"ERROR: {senate_path} not found. Download it manually and place it in data/")
    else:
        # Wipe old data so we don't double-count on re-runs
        con = get_connection()
        cur = con.cursor()
        cur.execute("DELETE FROM polls")
        cur.execute("DELETE FROM candidates")
        cur.execute("DELETE FROM races")
        con.commit()
        con.close()
        print("Cleared old poll data.")

        load_nyt_senate_polls(senate_path)

        climate_path = os.path.join(DATA, "climate.csv")
        if os.path.exists(climate_path):
            load_climate_factors(climate_path)