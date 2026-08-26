import csv
import os
import pandas as pd
from init_db import get_connection

# Grades sourced from FiveThirtyEight's archived 2023 pollster ratings
# (github.com/fivethirtyeight/data, pollster-ratings/2023), joined on that
# file's Pollster Rating ID. The ID itself was dropped from this CSV on
# 2026-08-25: nothing ever read it, and the hand-added rows had started
# carrying invented IDs that collided with real ones.
# Letter -> numeric mapping defined at generation time;
# only the RATIOS between values affect the weighted average.
# Unmatched pollsters default to C/D = 1.0 (unknown = unproven).
# F pollsters keep a small nonzero numeric but are excluded per-race in
# senate_model.py unless they are the only polling available for that race.
RATINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "pollster_ratings.csv")

EXPECTED_COLUMNS = [
    "Pollster",
    "Pollster Rating Name",
    "grade",
    "numeric_grade",
]


def read_ratings(filepath):
    """Read the ratings CSV, failing with an actionable message on bad rows.

    pandas raises a bare "Expected 5 fields in line N, saw 7" tokenizer error
    that says nothing about which pollster is malformed. Hand-edited rows in
    this file are the usual cause (an extra unquoted comma, or a column left
    out), so name the offending line and its content instead.

    Checks the whole file and reports every problem at once — fixing these one
    crash at a time is what let a hand-edited batch of rows accumulate several
    distinct faults. Beyond field counts we also reject:

      * a blank Pollster name, which can never match a DB row; and
      * one letter grade carrying two different numeric_grade values, which
        looks harmless here but silently corrupts load_historical_polls.py's
        grade_ladder(): it keys the ladder by letter and takes whichever row
        it reads last, so historical polls end up on a different scale than
        the live ones.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Ratings file not found: {filepath}")

    problems = []
    grade_values = {}  # letter grade -> (numeric as written, first line seen)
    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != EXPECTED_COLUMNS:
            raise ValueError(
                f"{filepath} header is {header}, expected {EXPECTED_COLUMNS}"
            )
        for row in reader:
            # reader.line_num, not an enumerate() counter: a quoted field may
            # span physical lines, and a line number that points at the wrong
            # row is worse than none at all.
            lineno = reader.line_num
            if len(row) != len(EXPECTED_COLUMNS):
                problems.append(
                    f"  line {lineno}: {len(row)} fields "
                    f"(expected {len(EXPECTED_COLUMNS)}): {','.join(row)}"
                )
                continue

            pollster, _rating_name, grade, numeric = (c.strip() for c in row)
            if not pollster:
                problems.append(f"  line {lineno}: blank Pollster name: {','.join(row)}")
            if grade and numeric:
                seen = grade_values.setdefault(grade, (numeric, lineno))
                if seen[0] != numeric:
                    problems.append(
                        f"  line {lineno}: grade {grade!r} = {numeric}, but "
                        f"line {seen[1]} has {grade!r} = {seen[0]}"
                    )

    if problems:
        raise ValueError(f"Malformed rows in {filepath}:\n" + "\n".join(problems))

    return pd.read_csv(filepath)


def ensure_grade_column(cur):
    """Add the 'grade' TEXT column to pollsters if this DB predates it.

    Uses PRAGMA table_info to inspect existing columns, so it is safe to
    run repeatedly (ALTER TABLE would error on a duplicate column).
    """
    cols = [row[1] for row in cur.execute("PRAGMA table_info(pollsters)")]
    if not cols:
        # PRAGMA on a missing table returns no rows rather than raising, so
        # without this the ALTER below fails with an opaque sqlite error.
        raise RuntimeError(
            "pollsters table does not exist — run init_db.py and an ingest "
            "(senate_ingest.py / house_ingest.py) before loading ratings."
        )
    if "grade" not in cols:
        cur.execute("ALTER TABLE pollsters ADD COLUMN grade TEXT")
        print("Added 'grade' column to pollsters table.")


def load_pollster_ratings(filepath=RATINGS_PATH):
    """Update pollsters.credibility and pollsters.grade from the ratings CSV.

    Matches on exact pollster name — safe because pollster_ratings.csv was
    generated FROM senate.csv, so the names are identical by construction.

    Safe to run before or after senate_ingest.py / house_ingest.py: both
    ingests' upsert_pollster leaves credibility/grade untouched when their
    source CSV has no rating data, instead of resetting it to the unrated
    default. This script is what makes credibility real.
    """
    df = read_ratings(filepath)

    dupes = df["Pollster"].str.strip().duplicated()
    if dupes.any():
        print("Duplicate Pollster names in ratings file (last row wins): "
              + ", ".join(sorted(set(df.loc[dupes, "Pollster"].str.strip()))))

    con = get_connection()
    try:
        cur = con.cursor()
        ensure_grade_column(cur)

        # Sets, not counters: a duplicate row for one pollster is two UPDATEs
        # but one pollster, and reporting it twice overstates the coverage.
        updated, unmatched, skipped = set(), [], []
        for _, row in df.iterrows():
            name = str(row["Pollster"]).strip()
            # A blank grade would otherwise be written as NaN credibility,
            # which silently poisons every weighted average that pollster
            # appears in.
            if pd.isna(row["numeric_grade"]) or pd.isna(row["grade"]):
                skipped.append(name)
                continue
            cur.execute(
                "UPDATE pollsters SET credibility = ?, grade = ? WHERE name = ?",
                (float(row["numeric_grade"]), str(row["grade"]).strip(), name),
            )
            if cur.rowcount > 0:
                updated.add(name)
            else:
                # Pollster in ratings file but not in DB — usually means it had
                # no rows survive ingest filtering (stale, generic-ballot, etc.)
                unmatched.append(name)

        con.commit()
    finally:
        # Without this, a mid-loop failure leaves the DB locked by an open
        # connection holding a write transaction.
        con.close()

    print(f"Updated {len(updated)} pollsters with grades and credibility.")
    if skipped:
        print(f"{len(skipped)} rows skipped for missing grade/numeric_grade: "
              + ", ".join(skipped))
    if unmatched:
        print(f"{len(unmatched)} ratings-file pollsters not found in DB "
              f"(no surviving polls after ingest filters):")
        for n in unmatched:
            print(f"  - {n}")


if __name__ == "__main__":
    load_pollster_ratings()
