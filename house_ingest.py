"""
house_ingest.py — Loads House polling data into elections.db.

Two things get ingested:
  1. District polls: rows from house.csv where state != 'US', filtered to
     confirmed general-election nominees from house_nominees.csv.
  2. Generic ballot: rows from house.csv where state == 'US', stored as a
     special race (state='US', district='') for use in House control projection.

Run after init_db.py and before house_model.py.
Safe to re-run — wipes House races first, then reloads.
Senate data is untouched (different district values).
"""

import os
import csv
import pandas as pd
from datetime import datetime, date
from init_db import get_connection
from senate_ingest import upsert_pollster, upsert_race, upsert_candidate

DATA_DIR           = os.path.join(os.path.dirname(__file__), "data")
HOUSE_CSV          = os.path.join(DATA_DIR, "house.csv")
HOUSE_NOMINEES_CSV = os.path.join(DATA_DIR, "house_nominees.csv")

# ---------------------------------------------------------------------------
# Load confirmed nominees from house_nominees.csv
# Returns: dict (state, district) -> { party -> name }
# Skips primaries and rows missing both nominees.
# ---------------------------------------------------------------------------

def _split_names(raw):
    """
    Splits a nominees.csv field that may contain one name, or multiple
    names joined by a comma (e.g. CA's top-two jungle primary advances
    two candidates from the same party: "Connie Chan, Scott Wiener").
    Strips stray whitespace/tabs from each name. Returns a list (possibly
    empty, possibly length 1, possibly length 2+).
    """
    raw = raw.strip()
    if not raw:
        return []
    return [name.strip() for name in raw.split(",") if name.strip()]


def _load_confirmed_nominees():
    """
    Returns a dict keyed by (state, district) whose values are
    { 'D': [names], 'R': [names], 'I': [names] } for whichever parties
    are confirmed. A party's list normally holds one name, but can hold
    two or more when multiple same-party candidates advance to the
    general (e.g. CA-style top-two primaries: CA-11, CA-14 for Democrats,
    CA-40 for Republicans).
    Primary rows and fully-blank rows are skipped.
    """
    confirmed = {}
    with open(HOUSE_NOMINEES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "primary" in row.get("notes", "").lower():
                continue
            state    = row["state"].strip()
            district = row["district"].strip()
            dem      = _split_names(row.get("dem_nominee", ""))
            rep      = _split_names(row.get("rep_nominee", ""))
            ind      = _split_names(row.get("ind_nominee", ""))
            if not dem and not rep and not ind:
                continue
            entry = {}
            if dem:
                entry["D"] = dem
            if rep:
                entry["R"] = rep
            if ind:
                entry["I"] = ind
            confirmed[(state, district)] = entry
    return confirmed


# ---------------------------------------------------------------------------
# Build a name -> (state, district, party) lookup for fast poll row matching
# ---------------------------------------------------------------------------

def _build_nominee_lookup(confirmed):
    """
    Inverts confirmed nominees dict into:
      candidate_name -> (state, district, party)
    Used to decide whether a poll row belongs to a confirmed general-election race.
    Each party slot may hold multiple names (e.g. CA top-two primaries send
    two same-party candidates to the general) — every name maps individually.
    """
    lookup = {}
    for (state, district), parties in confirmed.items():
        for party, names in parties.items():
            for name in names:
                lookup[name] = (state, district, party)
    return lookup


# ---------------------------------------------------------------------------
# Generic ballot ingestion
# Stored as race (year=2026, state='US', district='')
# Candidates: 'Generic Democrat' (D) and 'Generic Republican' (R)
# ---------------------------------------------------------------------------

def load_generic_ballot(df, year=2026):
    """
    Ingests national generic House ballot polls (state='US') into the DB.
    Filters to 'Generic Democrat' and 'Generic Republican' only.
    Uses same recency/credibility pattern as district polls.
    """
    generic = df[
        (df["state"] == "US") &
        (df["stage"] == "general") &
        (df["candidate_name"].isin(["Generic Democrat", "Generic Republican"]))
        ].copy()

    if generic.empty:
        print("WARNING: no generic ballot rows found in house.csv")
        return

    # Population priority: lv > rv > a
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    generic["pop_rank"] = generic["population"].map(pop_priority).fillna(9)
    generic = generic.sort_values("pop_rank")
    generic = generic.drop_duplicates(subset=["poll_id", "candidate_name"], keep="first")

    party_map = {"Generic Democrat": "D", "Generic Republican": "R"}

    con = get_connection()
    cur = con.cursor()

    race_id = upsert_race(cur, year, "US", district="")

    # Ensure generic candidates exist
    dem_id = upsert_candidate(cur, race_id, "Generic Democrat", "D")
    rep_id = upsert_candidate(cur, race_id, "Generic Republican", "R")
    cand_ids = {"Generic Democrat": dem_id, "Generic Republican": rep_id}

    loaded = 0
    for _, row in generic.iterrows():
        try:
            pct = float(row["pct"])
        except (ValueError, TypeError):
            continue

        poll_date = row["end_date"]
        if pd.isna(poll_date):
            continue

        try:
            sample_size = int(row["sample_size"]) if pd.notna(row["sample_size"]) else None
        except (ValueError, TypeError):
            sample_size = None

        pollster_id  = upsert_pollster(cur, str(row["pollster"]).strip(),
                                       row.get("numeric_grade"), row.get("partisan"))
        candidate_id = cand_ids[row["candidate_name"]]

        cur.execute("""
            INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, sample_size, pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (race_id, candidate_id, pollster_id, poll_date, sample_size, pct))
        loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} generic ballot poll entries")


# ---------------------------------------------------------------------------
# District poll ingestion
# ---------------------------------------------------------------------------

def load_house_district_polls(df, confirmed, nominee_lookup, year=2026):
    """
    Ingests district-level House polls into the DB.

    Filtering logic:
      - stage == 'general'
      - state != 'US'
      - candidate_name must match a confirmed nominee in house_nominees.csv
        (this is the House equivalent of ingest.py's question-block dedup —
         it ensures we only store polls for actual general-election nominees,
         not primary candidates or hypothetical matchups)
      - party must be DEM, REP, or IND (mapped to D/R/I)

    Population dedup: per (poll_id, candidate_name), keep best population tier.
    """
    district_df = df[
        (df["state"] != "US") &
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP", "IND"]))
        ].copy()

    party_map = {"DEM": "D", "REP": "R", "IND": "I"}
    district_df["party"] = district_df["party"].map(party_map)

    # Population priority dedup
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    district_df["pop_rank"] = district_df["population"].map(pop_priority).fillna(9)
    district_df = district_df.sort_values("pop_rank")
    district_df = district_df.drop_duplicates(subset=["poll_id", "candidate_name"], keep="first")

    con = get_connection()
    cur = con.cursor()

    loaded   = 0
    skipped  = 0

    for _, row in district_df.iterrows():
        candidate = str(row["candidate_name"]).strip()

        # Core filter: only confirmed general-election nominees
        if candidate not in nominee_lookup:
            skipped += 1
            continue

        state, district, party = nominee_lookup[candidate]

        try:
            pct = float(row["pct"])
        except (ValueError, TypeError):
            skipped += 1
            continue

        poll_date = row["end_date"]
        if pd.isna(poll_date):
            skipped += 1
            continue

        try:
            sample_size = int(row["sample_size"]) if pd.notna(row["sample_size"]) else None
        except (ValueError, TypeError):
            sample_size = None

        pollster_id  = upsert_pollster(cur, str(row["pollster"]).strip(),
                                       row.get("numeric_grade"), row.get("partisan"))
        race_id      = upsert_race(cur, year, state, district=district)
        candidate_id = upsert_candidate(cur, race_id, candidate, party)

        cur.execute("""
            INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, sample_size, pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (race_id, candidate_id, pollster_id, poll_date, sample_size, pct))
        loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} House district poll entries ({skipped} rows skipped — unconfirmed nominees or bad data)")


# ---------------------------------------------------------------------------
# Wipe existing House data only
# Senate rows have district='', House rows have district != '' or state='US'.
# We delete races where district != '' OR state = 'US', cascading via race_id.
# ---------------------------------------------------------------------------

def _wipe_house_data():
    """
    Removes all House-related rows from polls, candidates, and races.
    Senate rows (district='', state != 'US') are untouched.
    """
    con = get_connection()
    cur = con.cursor()

    # Get race_ids to delete
    cur.execute("""
        SELECT id FROM races
        WHERE district != '' OR state = 'US'
    """)
    house_race_ids = [r[0] for r in cur.fetchall()]

    if house_race_ids:
        placeholders = ",".join("?" * len(house_race_ids))
        cur.execute(f"DELETE FROM polls      WHERE race_id IN ({placeholders})", house_race_ids)
        cur.execute(f"DELETE FROM candidates WHERE race_id IN ({placeholders})", house_race_ids)
        cur.execute(f"DELETE FROM races      WHERE id      IN ({placeholders})", house_race_ids)

    con.commit()
    con.close()
    print(f"Cleared {len(house_race_ids)} House race(s) from DB.")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not os.path.exists(HOUSE_CSV):
        print(f"ERROR: {HOUSE_CSV} not found.")
        raise SystemExit(1)
    if not os.path.exists(HOUSE_NOMINEES_CSV):
        print(f"ERROR: {HOUSE_NOMINEES_CSV} not found.")
        raise SystemExit(1)

    # Load and normalize dates up front — shared by both ingestion functions
    df = pd.read_csv(HOUSE_CSV)
    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])
    df["end_date"] = df["end_date"].dt.strftime("%Y-%m-%d")
    df["state"]    = df["state"].astype(str).str.strip().str.upper()

    confirmed      = _load_confirmed_nominees()
    nominee_lookup = _build_nominee_lookup(confirmed)

    print(f"Confirmed nominees: {len(nominee_lookup)} candidates across {len(confirmed)} races")

    _wipe_house_data()
    load_generic_ballot(df, year=2026)
    load_house_district_polls(df, confirmed, nominee_lookup, year=2026)

    print("House ingest complete.")