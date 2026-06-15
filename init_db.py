import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "elections.db")

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    """
    Initialize the database by creating necessary tables if they do not already exist.
    This function establishes a connection to the database, defines the schema for core
    entities such as pollsters, races, candidates, polls, climate factors, and historical
    results, and ensures the essential data structures are in place.

    :raises sqlite3.Error: If any database operation fails.
    :return: None

Schema structure:
- pollsters: Stores information about polling organizations, including their credibility and partisan lean.
    -PK id: Unique identifier for each pollster (becomes FK pollster_id in polls)
    -name: Name of the pollster. (text will not be null and must be unique)
    -credibility: A numeric score representing the pollster's reliability (0.0 to 3.0, from 538 numeric grade).
    -partisan_lean: Indicates if the pollster has a partisan tilt ('D' for Democratic, 'R' for Republican, or NULL for neutral).
- races: Contains details about each election race, such as the year, state, and whether it's competitive.
    -PK id: Unique identifier for each (become FK race_id in candidates and polls)
    -year, state
    -is_competitive: A boolean flag indicating if the race is considered competitive (1 for true, if the margin is less than 5%, otherwise 0).
    -unique constraint on (year, state) to prevent duplicate entries; there can only be one race per state and year.
-candidates: lists the candidates participating in each race, along with their party affiliation and incumbency status.
    -PK id: Unique identifier for each candidate (becomes FK candidate_id in polls and historical_results)
    -race_id: FK to races.id, indicating which race the candidate is participating in.
    -name, party, is_incumbent
    -unique constraint on (race_id, name) to prevent duplicates; there can only be one candidate with the same name in a given race (id)

-polls: Records individual poll results, linking them to specific races, candidates, and pollsters, along with details about the poll date, sample size, and percentage support.
    -PK id: Unique identifier for each poll entry.
    -race_id: FK to races.id, indicating which race the poll is associated with.
    -candidate_id: FK to candidates.id, indicating which candidate the poll is about.
    -pollster_id: FK to pollsters.id, indicating which pollster conducted the poll.
    -poll_date, sample_size, pct

-climate_factors: Stores various factors that can influence election outcomes, such as presidential approval ratings or generic ballot polling, indexed by year and factor name.
    -PK id: Unique identifier for each climate factor entry.
    -year, factor_name, value
    -unique constraint on (year, factor_name) to prevent duplicates; there can only be one entry for a given factor in a specific year.

-historical_results: Contains the actual election results for each candidate in each race, including their vote share and whether they won.
    -PK id: Unique identifier for each historical result entry
    -race_id: FK to races.id, indicating which race the result is associated with.
    -candidate_id: FK to candidates.id, indicating which candidate the result is about.
    -vote_share: The percentage of the vote that the candidate received.
    -won: A boolean flag indicating if the candidate won the election in the projection (1 for true, 0 for false).
    """
    con = get_connection()
    cur = con.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS pollsters (
            id               INTEGER PRIMARY KEY,
            name             TEXT NOT NULL UNIQUE,
            credibility      REAL NOT NULL DEFAULT 1.0,  -- 0.0 to 3.0, from 538 numeric grade
            partisan_lean    TEXT                         -- 'D', 'R', or NULL
        );

        CREATE TABLE IF NOT EXISTS races (
            id               INTEGER PRIMARY KEY,
            year             INTEGER NOT NULL,
            state            TEXT NOT NULL,               -- 2-letter abbreviation
            is_competitive   INTEGER NOT NULL DEFAULT 0,
            UNIQUE(year, state)
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

    con.commit()
    con.close()
    print("Database initialized at:", DB_PATH)

if __name__ == "__main__":
    init_db()