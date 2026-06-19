import os
import pandas as pd
from init_db import get_connection

_NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "data", "senate_nominees.csv")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def upsert_pollster(cur, name, numeric_grade=None, partisan=None):
    try:
        credibility = float(numeric_grade) if pd.notna(numeric_grade) else 1.0
    except (ValueError, TypeError):
        credibility = 1.0
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

def upsert_race(cur, year, state, district=''):
    """
    Insert or ignore a race row. district='' for Senate, '1'/'2'/... for House.
    The unique key is (year, state, district).
    """
    cur.execute("""
        INSERT INTO races (year, state, district)
        VALUES (?, ?, ?)
        ON CONFLICT(year, state, district) DO NOTHING
    """, (year, state, district))
    cur.execute(
        "SELECT id FROM races WHERE year = ? AND state = ? AND district = ?",
        (year, state, district)
    )
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
                if len(tok) > 2:
                    tokens.add(tok)
        state_tokens[state] = tokens
    return state_tokens


def load_nyt_senate_polls(filepath, year=2026):
    df = pd.read_csv(filepath)

    df = df[
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP"])) &
        (~df["candidate_name"].isin(["Don't know", "Someone else"]))
        ].copy()

    _GENERIC = {"Generic Democrat", "Generic Republican"}
    _generic_mask = df["candidate_name"].isin(_GENERIC)
    if _generic_mask.any():
        for name, cnt in df.loc[_generic_mask, "candidate_name"].value_counts().items():
            print(f"WARNING: skipping {cnt} row(s) with candidate_name='{name}' (generic ballot)")
    df = df[~_generic_mask].copy()

    df["state"] = df["state"].astype(str).str.strip().str.upper()

    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    df["pop_rank"] = df["population"].map(pop_priority).fillna(9)

    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])

    if "election_date" in df.columns:
        df["_election_dt"] = pd.to_datetime(df["election_date"], errors="coerce")
        stale_mask = df["_election_dt"].notna() & (
                (df["_election_dt"] - df["end_date"]).dt.days > 548
        )
        if stale_mask.any():
            print(f"Dropped {stale_mask.sum()} stale poll row(s)")
        df = df[~stale_mask].drop(columns=["_election_dt"])

    if "question_id" in df.columns:
        state_tokens = _build_state_tokens(_NOMINEES_PATH)
        df["question_id"] = df["question_id"].fillna("__default__")

        def _name_matches(candidate_name, tokens):
            return any(tok in tokens for tok in str(candidate_name).lower().split())

        keep_idx = []
        for (_, state), grp in df.groupby(["poll_id", "state"], sort=False):
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
            keep_idx.extend(grp.index[grp["question_id"] == best_qid].tolist())
        df = df.loc[keep_idx].reset_index(drop=True)

    df = df.sort_values("pop_rank")
    df = df.drop_duplicates(subset=["poll_id", "candidate_name"], keep="first")
    df["party"] = df["party"].map({"DEM": "D", "REP": "R"})
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
        race_id      = upsert_race(cur, year, state)          # district='' implicit
        candidate_id = upsert_candidate(cur, race_id, candidate, party)

        cur.execute("""
            INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, sample_size, pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (race_id, candidate_id, pollster_id, poll_date, sample_size, pct))
        loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} Senate poll entries from {filepath}")


def _wipe_senate_data():
    """
    Removes all Senate rows from polls, candidates, and races.
    Senate races are identified by district='' AND state != 'US' — the same
    discriminator house_ingest.py's _wipe_house_data() uses, just inverted.

    NOTE: this is currently an unconditional wipe of ALL races matching that
    filter, which today means "all Senate races" since House always sets a
    non-empty district or state='US'. If a future data source ever needs a
    third category that also satisfies district='' and state != 'US', this
    will need a more specific marker (e.g. a chamber column) to stay scoped.
    """
    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        SELECT id FROM races
        WHERE district = '' AND state != 'US'
    """)
    senate_race_ids = [r[0] for r in cur.fetchall()]

    if senate_race_ids:
        placeholders = ",".join("?" * len(senate_race_ids))
        cur.execute(f"DELETE FROM polls      WHERE race_id IN ({placeholders})", senate_race_ids)
        cur.execute(f"DELETE FROM candidates WHERE race_id IN ({placeholders})", senate_race_ids)
        cur.execute(f"DELETE FROM races      WHERE id      IN ({placeholders})", senate_race_ids)

    con.commit()
    con.close()
    print(f"Cleared {len(senate_race_ids)} Senate race(s) from DB.")


def load_climate_factors(filepath):
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
        print(f"ERROR: {senate_path} not found.")
    else:
        _wipe_senate_data()

        load_nyt_senate_polls(senate_path)

        climate_path = os.path.join(DATA, "climate.csv")
        if os.path.exists(climate_path):
            load_climate_factors(climate_path)