import csv
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

EXPECTED_COLUMNS = [
    "Pollster",
    "Pollster Rating Name",
    "Pollster Rating ID",
    "grade",
    "numeric_grade",
]


def read_ratings(filepath):
    """Read the ratings CSV, failing with an actionable message on bad rows.

    pandas raises a bare "Expected 5 fields in line N, saw 7" tokenizer error
    that says nothing about which pollster is malformed. Hand-edited rows in
    this file are the usual cause (an extra unquoted comma, or a column left
    out), so name the offending line and its content instead.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Ratings file not found: {filepath}")

    bad_lines = []
    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != EXPECTED_COLUMNS:
            raise ValueError(
                f"{filepath} header is {header}, expected {EXPECTED_COLUMNS}"
            )
        for lineno, row in enumerate(reader, start=2):
            if len(row) != len(EXPECTED_COLUMNS):
                bad_lines.append((lineno, len(row), ",".join(row)))

    if bad_lines:
        detail = "\n".join(
            f"  line {n}: {count} fields (expected {len(EXPECTED_COLUMNS)}): {text}"
            for n, count, text in bad_lines
        )
        raise ValueError(f"Malformed rows in {filepath}:\n{detail}")

    return pd.read_csv(filepath)


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
    cur = con.cursor()
    ensure_grade_column(cur)

    updated, unmatched, skipped = 0, [], []
    for _, row in df.iterrows():
        name = str(row["Pollster"]).strip()
        # A blank grade would otherwise be written as NaN credibility, which
        # silently poisons every weighted average that pollster appears in.
        if pd.isna(row["numeric_grade"]) or pd.isna(row["grade"]):
            skipped.append(name)
            continue
        cur.execute(
            "UPDATE pollsters SET credibility = ?, grade = ? WHERE name = ?",
            (float(row["numeric_grade"]), str(row["grade"]).strip(), name),
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
