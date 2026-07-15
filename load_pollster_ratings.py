import os
import pandas as pd
from init_db import get_connection

# Grades sourced from FiveThirtyEight's archived 2023 pollster ratings
# (github.com/fivethirtyeight/data, pollster-ratings/2023), joined on
# Pollster Rating ID. Letter -> numeric mapping defined at generation time;
# only the RATIOS between values affect the weighted average.
# Unmatched pollsters default to C/D = 1.0 (unknown = unproven).
# F pollsters keep numeric 0.3 but are excluded per-race in senate_model.py
# unless they are the only polling available for that race.
RATINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "pollster_ratings.csv")


def ensure_grade_column(cur):
    """Add the 'grade' TEXT column to pollsters if this DB predates it.

    Uses PRAGMA table_info to inspect existing columns, so it is safe to
    run repeatedly (ALTER TABLE would error on a duplicate column).
    """
    cols = [row[1] for row in cur.execute("PRAGMA table_info(pollsters)")]
    if "grade" not in cols:
        cur.execute("ALTER TABLE pollsters ADD COLUMN grade TEXT")
        print("Added 'grade' column to pollsters table.")


def load_pollster_ratings(filepath=RATINGS_PATH):
    """Update pollsters.credibility and pollsters.grade from the ratings CSV.

    Matches on exact pollster name — safe because pollster_ratings.csv was
    generated FROM senate.csv, so the names are identical by construction.

    IMPORTANT: must run AFTER senate_ingest.py. The ingest's upsert_pollster
    overwrites credibility from senate.csv's (empty) numeric_grade column,
    resetting everything to 1.0. This script is what makes credibility real.
    """
    df = pd.read_csv(filepath)

    con = get_connection()
    cur = con.cursor()
    ensure_grade_column(cur)

    updated, unmatched = 0, []
    for _, row in df.iterrows():
        name = str(row["Pollster"]).strip()
        cur.execute(
            "UPDATE pollsters SET credibility = ?, grade = ? WHERE name = ?",
            (float(row["numeric_grade"]), str(row["grade"]), name),
        )
        if cur.rowcount > 0:
            updated += 1
        else:
            # Pollster in ratings file but not in DB — usually means it had
            # no rows survive ingest filtering (stale, generic-ballot, etc.)
            unmatched.append(name)

    con.commit()
    con.close()

    print(f"Updated {updated} pollsters with grades and credibility.")
    if unmatched:
        print(f"{len(unmatched)} ratings-file pollsters not found in DB "
              f"(no surviving polls after ingest filters):")
        for n in unmatched:
            print(f"  - {n}")


if __name__ == "__main__":
    load_pollster_ratings()
