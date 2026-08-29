import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "elections.db")

def get_connection():
    """
    Establishes a connection to the SQLite database specified by DB_PATH.

    Summary:
    This function ensures the directory for the database file exists by creating
    it if necessary. It then establishes and returns a connection to the SQLite
    database located at the path defined by the DB_PATH constant.

    Raises:
    OSError: If the directory creation fails due to an underlying OS error.
    sqlite3.Error: If the connection to the SQLite database cannot be established.

    Returns:
    sqlite3.Connection: An object representing the connection to the SQLite
    database.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def ensure_column(cur, table, column, coltype):
    """Add `column` to `table` if this DB predates it.

    Uses PRAGMA table_info to inspect existing columns, so it is safe to
    call on every init_db() run (ALTER TABLE would error on a duplicate
    column on a DB that already has it).
    """
    cols = [row[1] for row in cur.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"Added '{column}' column to {table} table.")


def init_db():
    """
    Initializes the database by creating necessary tables if they do not already exist.

    This function connects to the database, uses an SQL script to create tables, ensuring
    the database structure accommodates pollsters, races, candidates, polls, climate factors,
    and historical election results. Primary and foreign key constraints are defined to enforce
    proper relationships between tables.

    Attributes Initialized in the Database:
    - pollsters: Stores pollster information including name, credibility, and partisan lean.
    - races: Represents electoral races with details such as year, state, and competitiveness.
    - candidates: Holds candidate information, linked to races and includes data on party
      affiliation and incumbency.
    - polls: Captures poll data including pollster, associated race, candidate details,
      and polling statistics.
    - climate_factors: Contains general election climate information such as presidential
      approval or generic party ballot trends.
    - historical_results: Stores outcomes of races, linking candidates to vote shares and
      win status.

    Raises:
    - Any errors encountered during the execution of the SQL script or database connection
      management will propagate and must be handled by the caller.

    Important:
    - Only tables listed in the SQL script are created or checked for existence.
    - Assumes the database file path is correctly configured in the calling environment
      (e.g., via DB_PATH constant).
    """
    con = get_connection()
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS pollsters (
        id               INTEGER PRIMARY KEY,
        name             TEXT NOT NULL UNIQUE,
        credibility      REAL NOT NULL DEFAULT 1.0,  -- 0.0 to 3.0, from 538 letter-grade mapping
        grade            TEXT,                        -- 538 letter grade ('A+'..'F'); NULL = ungraded
        partisan_lean    TEXT                         -- 'D', 'R', or NULL
        );

        CREATE TABLE IF NOT EXISTS races (
        id               INTEGER PRIMARY KEY,
        year             INTEGER NOT NULL,
        state            TEXT NOT NULL,               -- 2-letter abbreviation
        district         TEXT NOT NULL DEFAULT '',    -- '' = Senate; '01'-'53' zero-padded = House
        is_competitive   INTEGER NOT NULL DEFAULT 0,
        UNIQUE(year, state, district)
    );

        CREATE TABLE IF NOT EXISTS candidates (
            id               INTEGER PRIMARY KEY,
            race_id          INTEGER NOT NULL REFERENCES races(id),
            name             TEXT NOT NULL,
            party            TEXT NOT NULL,               -- 'D', 'R', 'I' etc.
            is_incumbent     INTEGER NOT NULL DEFAULT 0,
            UNIQUE(race_id, name)
        );

        CREATE TABLE IF NOT EXISTS polls (
            id               INTEGER PRIMARY KEY,
            race_id          INTEGER NOT NULL REFERENCES races(id),
            candidate_id     INTEGER NOT NULL REFERENCES candidates(id),
            pollster_id      INTEGER REFERENCES pollsters(id),
            poll_date        TEXT NOT NULL,               -- YYYY-MM-DD
            sample_size      INTEGER,
            pct              REAL NOT NULL                -- e.g. 47.2
        );

        CREATE TABLE IF NOT EXISTS climate_factors (
            id               INTEGER PRIMARY KEY,
            year             INTEGER NOT NULL,
            factor_name      TEXT NOT NULL,               -- e.g. 'PRES_APPROVAL', 'GENERIC_BALLOT_D'
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

    # These columns were added after some databases were already created via
    # CREATE TABLE IF NOT EXISTS above (a no-op on an existing table), so a
    # pre-existing DB needs them backfilled explicitly.
    ensure_column(cur, "races", "district", "TEXT NOT NULL DEFAULT ''")
    ensure_column(cur, "pollsters", "grade", "TEXT")

    con.commit()
    con.close()
    print("Database initialized at:", DB_PATH)

if __name__ == "__main__":
    init_db()