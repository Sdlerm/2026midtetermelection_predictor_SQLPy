import os
import pandas as pd
from pandas.errors import ParserError
from init_db import get_connection
from senate_ingest import upsert_race, upsert_candidate

DATA = os.path.join(os.path.dirname(__file__), "data")

SUMMARY_COLUMNS = {
    "State",
    "Winner",
    "Winner_Party",
    "Winner_Pct",
    "Runner_Up",
    "Runner_Up_Party",
    "Runner_Up_Pct",
}

RAW_COLUMNS = {
    "state_po",
    "candidate",
    "party_simplified",
    "votes",
    "totalvotes",
    "stage",
    "special",
}

# Full state name -> 2-letter abbreviation. Only needed for the "summary"
# format (2018/2020/2022), which uses full names. The "raw" 2024 MEDSL file
# already uses abbreviations (state_po), so this doesn't apply there.
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


# ---------------------------------------------------------------------------
# Party-name classifiers — kept as TWO separate functions on purpose.
# The 2024 raw file spells parties "DEMOCRAT" / "REPUBLICAN". The 2018-2022
# summary files spell them "Democratic" / "Republican". An exact string
# match against the wrong spelling would silently misclassify everyone as
# "I" instead of raising an error, so merging these into one function that
# tries to handle both spellings would just hide that risk rather than
# remove it. Two small, obviously-named functions is safer than one clever
# one here.
# ---------------------------------------------------------------------------

def classify_party_raw(party_simplified):
    """For the 2024 MEDSL raw-precinct format: 'DEMOCRAT' / 'REPUBLICAN'."""
    ps = str(party_simplified).upper()
    if ps == "DEMOCRAT":
        return "D"
    if ps == "REPUBLICAN":
        return "R"
    return "I"


def classify_party_summary(party_label):
    """For the 2018/2020/2022 winner/runner-up format: 'Democratic' / 'Republican'."""
    p = str(party_label).strip().upper()
    if p == "DEMOCRATIC":
        return "D"
    if p == "REPUBLICAN":
        return "R"
    return "I"


def read_validated_csv(filepath, required_columns, format_name):
    with open(filepath, "rb") as f:
        prefix = f.read(2048).lstrip().lower()

    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise ValueError(
            f"{filepath} is an HTML page, not a CSV. This usually happens when "
            "a GitHub file preview page is saved instead of the raw file. "
            "Replace it with the raw CSV contents, then rerun this script."
        )

    try:
        df = pd.read_csv(filepath)
    except ParserError as exc:
        raise ValueError(
            f"Could not parse {filepath} as {format_name} CSV. Check that the "
            "file is comma-delimited raw data with a single header row."
        ) from exc

    missing = sorted(required_columns - set(df.columns))
    if missing:
        raise ValueError(
            f"{filepath} is not a valid {format_name} CSV. Missing required "
            f"column(s): {', '.join(missing)}"
        )

    return df


# ---------------------------------------------------------------------------
# Loader for the 2024-style "raw" format: one row per candidate per county
# line, requiring the fusion-voting collapse and top-2-by-votes filter.
# ---------------------------------------------------------------------------

def load_historical_senate_raw(filepath, year):
    df = read_validated_csv(filepath, RAW_COLUMNS, "raw senate results")

    general = df["stage"].astype(str).str.upper().eq("GEN")
    regular = df["special"].astype(str).str.upper().eq("FALSE")
    df = df[general & regular].copy()

    agg_rows = []
    for (state_po, candidate), g in df.groupby(["state_po", "candidate"], sort=False):
        biggest_line = g.loc[g["votes"].idxmax()]
        agg_rows.append({
            "state":            state_po,
            "candidate":        str(candidate).strip(),
            "votes":            int(g["votes"].sum()),
            "totalvotes":       int(g["totalvotes"].iloc[0]),
            "party_simplified": biggest_line["party_simplified"],
        })
    agg = pd.DataFrame(agg_rows)

    agg = agg.sort_values("votes", ascending=False)
    agg = agg.groupby("state", sort=False).head(2).reset_index(drop=True)

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "DELETE FROM historical_results "
        "WHERE race_id IN (SELECT id FROM races WHERE year = ?)",
        (year,),
    )

    loaded = 0
    for state, g in agg.groupby("state", sort=False):
        race_id = upsert_race(cur, year, state)
        winner_label = g["votes"].idxmax()
        for idx, r in g.iterrows():
            party = classify_party_raw(r["party_simplified"])
            candidate_id = upsert_candidate(cur, race_id, r["candidate"], party)
            vote_share = round(100 * r["votes"] / r["totalvotes"], 1)
            won = 1 if idx == winner_label else 0
            cur.execute(
                "INSERT INTO historical_results (race_id, candidate_id, vote_share, won) "
                "VALUES (?, ?, ?, ?)",
                (race_id, candidate_id, vote_share, won),
            )
            loaded += 1

    con.commit()
    con.close()
    print(f"Loaded {loaded} historical result rows across "
          f"{agg['state'].nunique()} races for {year}.")


# ---------------------------------------------------------------------------
# Loader for the 2018/2020/2022-style "summary" format: already exactly one
# winner row + one runner-up row per race, so no collapsing/filtering needed.
# ---------------------------------------------------------------------------

def load_historical_senate_summary(filepath, year):
    df = read_validated_csv(filepath, SUMMARY_COLUMNS, "summary senate results")

    special_mask = df["State"].astype(str).str.contains(r"\(Special\)", regex=True)
    if special_mask.any():
        dropped = df.loc[special_mask, "State"].tolist()
        print(f"  Dropping {special_mask.sum()} special election row(s): {dropped}")
    df = df[~special_mask].copy()

    df["state_abbr"] = df["State"].map(STATE_NAME_TO_ABBR)
    unmapped = df[df["state_abbr"].isna()]
    if not unmapped.empty:
        print(f"  WARNING: unmapped state name(s), skipping: {unmapped['State'].tolist()}")
    df = df.dropna(subset=["state_abbr"])

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "DELETE FROM historical_results "
        "WHERE race_id IN (SELECT id FROM races WHERE year = ?)",
        (year,),
    )

    loaded = 0
    for _, row in df.iterrows():
        state = row["state_abbr"]
        race_id = upsert_race(cur, year, state)

        winner_party = classify_party_summary(row["Winner_Party"])
        winner_id = upsert_candidate(cur, race_id, str(row["Winner"]).strip(), winner_party)
        cur.execute(
            "INSERT INTO historical_results (race_id, candidate_id, vote_share, won) "
            "VALUES (?, ?, ?, ?)",
            (race_id, winner_id, float(row["Winner_Pct"]), 1),
        )
        loaded += 1

        runner_party = classify_party_summary(row["Runner_Up_Party"])
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


# ---------------------------------------------------------------------------
# Dispatch table: which loader function reads which year's file, and where
# that file lives. Adding a new year later means adding one line here —
# not writing a new script.
# ---------------------------------------------------------------------------

LOADERS = {
    2018: (load_historical_senate_summary, os.path.join(DATA, "2018-senate-state.csv")),
    2020: (load_historical_senate_summary, os.path.join(DATA, "2020-senate-state.csv")),
    2022: (load_historical_senate_summary, os.path.join(DATA, "2022-senate-state.csv")),
    2024: (load_historical_senate_raw,     os.path.join(DATA, "2024-senate-state.csv")),
}

if __name__ == "__main__":
    for year in sorted(LOADERS):
        loader_fn, path = LOADERS[year]
        if not os.path.exists(path):
            print(f"SKIP {year}: {path} not found")
            continue
        print(f"\n--- Loading {year} ---")
        loader_fn(path, year)
