import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "elections.db")

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    """
    Initialize the database by creating necessary tables if they do not already exist.

    Schema structure:
    - pollsters: Polling organizations with credibility score and partisan lean.
    - races: One row per (year, state, district).
        district = '' for Senate races (one per state).
        district = '1', '2', ... for House races.
        UNIQUE(year, state, district) enforces no duplicates.
    - candidates: Candidates per race, linked by race_id.
    - polls: Individual poll entries, linked to race/candidate/pollster.
    - climate_factors: Economic/approval indicators by year.
    - historical_results: Actual election outcomes for backtesting.
    """
    con = get_connection()
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS pollsters (
            id               INTEGER PRIMARY KEY,
            name             TEXT NOT NULL UNIQUE,
            credibility      REAL NOT NULL DEFAULT 1.0,
            partisan_lean    TEXT
        );

        CREATE TABLE IF NOT EXISTS races (
            id               INTEGER PRIMARY KEY,
            year             INTEGER NOT NULL,
            state            TEXT NOT NULL,
            district         TEXT NOT NULL DEFAULT '',
            is_competitive   INTEGER NOT NULL DEFAULT 0,
            UNIQUE(year, state, district)
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id               INTEGER PRIMARY KEY,
            race_id          INTEGER NOT NULL REFERENCES races(id),
            name             TEXT NOT NULL,
            party            TEXT NOT NULL,
            is_incumbent     INTEGER NOT NULL DEFAULT 0,
            UNIQUE(race_id, name)
        );

        CREATE TABLE IF NOT EXISTS polls (
            id               INTEGER PRIMARY KEY,
            race_id          INTEGER NOT NULL REFERENCES races(id),
            candidate_id     INTEGER NOT NULL REFERENCES candidates(id),
            pollster_id      INTEGER REFERENCES pollsters(id),
            poll_date        TEXT NOT NULL,
            sample_size      INTEGER,
            pct              REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS climate_factors (
            id               INTEGER PRIMARY KEY,
            year             INTEGER NOT NULL,
            factor_name      TEXT NOT NULL,
            value            REAL NOT NULL,
            UNIQUE(year, factor_name)
        );

        CREATE TABLE IF NOT EXISTS historical_results (
            id               INTEGER PRIMARY KEY,
            race_id          INTEGER NOT NULL REFERENCES races(id),
            candidate_id     INTEGER NOT NULL REFERENCES candidates(id),
            vote_share       REAL NOT NULL,
            won              INTEGER NOT NULL DEFAULT 0
        );
    """)

    con.commit()
    con.close()
    print("Database initialized at:", DB_PATH)

if __name__ == "__main__":
    init_db()