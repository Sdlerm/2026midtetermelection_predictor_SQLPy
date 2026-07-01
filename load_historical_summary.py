import os
import pandas as pd
from init_db import get_connection
from senate_ingest import upsert_race, upsert_candidate

DATA = os.path.join(os.path.dirname(__file__), "data")

# Full state name -> 2-letter abbreviation. These CSVs use full names
# ("Arizona"), but the races table stores the abbreviation ("AZ") to stay
# consistent with the 2024 MEDSL loader (which uses state_po directly).
STATE_NAME_TO_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}


def classify_party(party_label):
    """
    Same job as classify_party() in load_historical.py, but matched against
    this dataset's actual spelling. The 2024 MEDSL file uses "DEMOCRAT" /
    "REPUBLICAN"; these files use "Democratic" / "Republican" — an exact-match
    check against the wrong spelling would silently misclassify everyone as
    "I", so this is deliberately a separate function rather than a shared one.
    """
    p = str(party_label).strip().upper()
    if p == "DEMOCRATIC":
        return "D"
    if p == "REPUBLICAN":
        return "R"
    return "I"


def load_historical_senate_summary(filepath, year):
    """
    Loads a winner/runner-up-style historical Senate results CSV (2018, 2020,
    2022 format) into historical_results. Unlike load_historical_senate() in
    load_historical.py, this format is already aggregated to exactly one
    winner row + one runner-up row per race, so there's no fusion-voting
    collapse or top-2-by-votes filtering to do here — just cleaning and
    reshaping into the schema.
    """
    df = pd.read_csv(filepath)

    # Drop special elections. A regular + special race in the same state
    # would both map to the same (year, state) and collide on races'
    # UNIQUE(year, state) constraint -- same reasoning as load_historical.py.
    special_mask = df["State"].str.contains(r"\(Special\)", regex=True)
    if special_mask.any():
        dropped = df.loc[special_mask, "State"].tolist()
        print(f"  Dropping {special_mask.sum()} special election row(s): {dropped}")
    df = df[~special_mask].copy()

    # Map full state names to abbreviations. Flag anything we don't recognize
    # instead of silently inserting a garbage state code.
    df["state_abbr"] = df["State"].map(STATE_NAME_TO_ABBR)
    unmapped = df[df["state_abbr"].isna()]
    if not unmapped.empty:
        print(f"  WARNING: unmapped state name(s), skipping: {unmapped['State'].tolist()}")
    df = df.dropna(subset=["state_abbr"])

    con = get_connection()
    cur = con.cursor()

    # Scope-wipe this year's historical rows before reloading, same
    # safeguard as load_historical.py -- historical_results has no UNIQUE
    # constraint, so re-running without this would duplicate rows.
    cur.execute(
        "DELETE FROM historical_results "
        "WHERE race_id IN (SELECT id FROM races WHERE year = ?)",
        (year,),
    )

    loaded = 0
    for _, row in df.iterrows():
        state = row["state_abbr"]
        race_id = upsert_race(cur, year, state)

        winner_party = classify_party(row["Winner_Party"])
        winner_id = upsert_candidate(cur, race_id, str(row["Winner"]).strip(), winner_party)
        cur.execute(
            "INSERT INTO historical_results (race_id, candidate_id, vote_share, won) "
            "VALUES (?, ?, ?, ?)",
            (race_id, winner_id, float(row["Winner_Pct"]), 1),
        )
        loaded += 1

        runner_party = classify_party(row["Runner_Up_Party"])
        runner_id = upsert_candidate(cur, race_id, str(row["Runner_Up"]).strip(), runner_party)
        cur.execute(
            "INSERT INTO historical_results (race_id, candidate_id, vote_share, won) "
            "VALUES (?, ?, ?, ?)",
            (race_id, runner_id, float(row["Runner_Up_Pct"]), 0),
        )
        loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} historical result rows across {len(df)} races for {year}.")


if __name__ == "__main__":
    YEARS_AND_FILES = [
        (2018, os.path.join(DATA, "2018-senate-state.csv")),
        (2020, os.path.join(DATA, "2020-senate-state.csv")),
        (2022, os.path.join(DATA, "2022-senate-state.csv")),
    ]

    for year, path in YEARS_AND_FILES:
        if not os.path.exists(path):
            print(f"SKIP {year}: {path} not found")
            continue
        print(f"\n--- Loading {year} ---")
        load_historical_senate_summary(path, year)
