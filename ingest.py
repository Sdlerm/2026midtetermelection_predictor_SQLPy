import os
import pandas as pd
from init_db import get_connection

_NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "data", "nominees.csv")

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
    # The partisan field can be "1", "DEM", "REP", or NaN.
    # Any non-null value means the poll was sponsored by a partisan actor.
    p = str(partisan).strip() if pd.notna(partisan) else ""
    partisan_lean = p if p not in ("", "nan") else None
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

def _build_state_tokens(nominees_path):
    """Build a dict mapping state abbrev -> set of lowercase name tokens from nominees.csv."""
    ndf = pd.read_csv(nominees_path)
    ndf["state"] = ndf["state"].astype(str).str.strip().str.upper()
    state_tokens = {}
    for state, grp in ndf.groupby("state"):
        tokens = set()
        for name in grp["name"]:
            for tok in str(name).lower().split():
                if len(tok) > 2:  # skip initials like "J." or "R."
                    tokens.add(tok)
        state_tokens[state] = tokens
    return state_tokens


def load_nyt_senate_polls(filepath, year=2026):
    df = pd.read_csv(filepath)

    # Only general election, only major parties, only real candidates
    df = df[
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP"])) &
        (~df["candidate_name"].isin(["Don't know", "Someone else"]))
    ].copy()

    # Warn and skip generic ballot rows — they test hypothetical matchups, not actual nominees
    _GENERIC = {"Generic Democrat", "Generic Republican"}
    _generic_mask = df["candidate_name"].isin(_GENERIC)
    if _generic_mask.any():
        for name, cnt in df.loc[_generic_mask, "candidate_name"].value_counts().items():
            print(f"WARNING: skipping {cnt} row(s) with candidate_name='{name}' (generic ballot test, not an actual nominee)")
    df = df[~_generic_mask].copy()

    # Normalize state early — needed for question-block dedup groupby below
    df["state"] = df["state"].astype(str).str.strip().str.upper()

    # Prefer likely voters; fall back to registered voters, then all adults
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    df["pop_rank"] = df["population"].map(pop_priority).fillna(9)

    # Parse end_date
    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])

    # Drop stale polls: end_date more than ~18 months (548 days) before election_date
    if "election_date" in df.columns:
        df["_election_dt"] = pd.to_datetime(df["election_date"], errors="coerce")
        stale_mask = df["_election_dt"].notna() & (
            (df["_election_dt"] - df["end_date"]).dt.days > 548
        )
        if stale_mask.any():
            print(f"Dropped {stale_mask.sum()} stale poll row(s) (end_date > ~18 months before election_date)")
        df = df[~stale_mask].drop(columns=["_election_dt"])

    # Question-block dedup: for each (poll_id, state), keep the question_id whose
    # candidates best match nominees.csv.
    #
    # Score = (ratio of block candidates that are known nominees, absolute count of matches).
    # A clean H2H [D, R] where both are nominees scores (1.0, 2); a full-field [D, R, L, G]
    # where only D and R are nominees scores (0.5, 2). The cleaner block wins.
    #
    # This must run BEFORE population dedup — the pop dedup collapses across question_ids
    # and makes question grouping impossible afterward.
    if "question_id" in df.columns:
        state_tokens = _build_state_tokens(_NOMINEES_PATH)
        df["question_id"] = df["question_id"].fillna("__default__")

        def _name_matches(candidate_name, tokens):
            return any(tok in tokens for tok in str(candidate_name).lower().split())

        def _pick_best_question(grp):
            state = grp["state"].iloc[0]
            tokens = state_tokens.get(state, set())
            best_qid, best_score = None, (-1.0, -1)
            for qid, qdf in grp.groupby("question_id", sort=False):
                cands = qdf["candidate_name"].tolist()
                n_match = sum(1 for c in cands if _name_matches(c, tokens))
                total = len(cands)
                score = (n_match / total if total else 0.0, n_match)
                if score > best_score:
                    best_score = score
                    best_qid = qid
            return grp[grp["question_id"] == best_qid]

        df = (
            df.groupby(["poll_id", "state"], group_keys=False)
            .apply(_pick_best_question)
            .reset_index(drop=True)
        )

    # Population dedup — after question selection so each candidate appears once per poll_id
    df = df.sort_values("pop_rank")
    df = df.drop_duplicates(subset=["poll_id", "candidate_name"], keep="first")

    # Normalize party to single letter
    df["party"] = df["party"].map({"DEM": "D", "REP": "R"})

    # Format end_date as YYYY-MM-DD string for DB storage
    df["end_date"] = df["end_date"].dt.strftime("%Y-%m-%d")

    con = get_connection()
    cur = con.cursor()

    loaded = 0
    for _, row in df.iterrows():
        state         = row["state"]
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
