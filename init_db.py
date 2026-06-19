import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "elections.db")

def get_connection():
    """
    Establishes and returns a connection to the SQLite database.

    This function ensures that the directory structure for the database file
    exists by creating the necessary directories if they are missing. It then
    establishes and returns a connection to the SQLite database located at
    the path specified by `DB_PATH`.

    :return: A connection object to the SQLite database.
    :rtype: sqlite3.Connection
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    """
    Initializes the database with the required schema.

    This function establishes a connection to the database and creates the following
    tables if they do not already exist:
    - pollsters: Stores information about polling organizations, their names, credibility scores,
      and any partisan leanings.
    - races: Represents different election races, including year, state, district, and whether
      the race is marked as competitive.
    - candidates: Holds data about candidates in specific races, including their names,
      political parties, and whether they are incumbents.
    - polls: Contains information about polls conducted for specific races and candidates,
      including pollster details, poll dates, sample sizes, and percentage results.
    - climate_factors: Stores various external factors (e.g., economic indicators) for
      particular years and their associated values.
    - historical_results: Records the results of past races, indicating which candidates won
      and their vote shares.

    This function ensures the schema exists and matches the defined structure.

    :raises: No explicit exceptions are raised in this function, but any database-related
             errors may occur based on the environment or integrity constraints.
    :return: None
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