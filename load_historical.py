import os
import pandas as pd
from init_db import get_connection
from senate_ingest import upsert_race, upsert_candidate

DATA = os.path.join(os.path.dirname(__file__), "data")
HISTORICAL_PATH = os.path.join(DATA, "2024-senate-state.csv")
YEAR = 2024


def classify_party(party_simplified):
    """
    Classify a political party designation into a standardized single-letter code.

    Takes a party affiliation string and converts it to a standardized single-letter
    representation. Democrat affiliations are converted to "D", Republican
    affiliations to "R", and all other values (including independent, third-party,
    or unrecognized affiliations) are converted to "I".

    Parameters
    ----------
    party_simplified : str
        The party affiliation to classify. The input is converted to uppercase
        string for case-insensitive comparison.

    Returns
    -------
    str
        A single-letter party code: "D" for Democrat, "R" for Republican, or "I"
        for all other cases (Independent or other).
    """
    ps = str(party_simplified).upper()
    if ps == "DEMOCRAT":
        return "D"
    if ps == "REPUBLICAN":
        return "R"
    return "I"


def load_historical_senate(filepath=HISTORICAL_PATH, year=YEAR):
    df = pd.read_csv(filepath)

    # 1. General election only; drop special elections. A regular + special race in
    #    the same state would collide on races' UNIQUE(year, state) constraint.
    df = df[(df["stage"] == "GEN") & (df["special"] == False)].copy()

    # 2. Collapse fusion-voting lines: one row per (state, candidate), with votes
    #    summed across party lines and party taken from the candidate's LARGEST line.
    #    e.g. CT Murphy = DEMOCRATIC 953,646 + WORKING FAMILIES 47,049 -> one D row.
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

    # 3. Relevance filter: keep the top 2 finishers per race by votes. This is the
    #    actual contest. It captures the real I-vs-R races (VT, ME, NE) and discards
    #    minor/write-in/noise candidates automatically — no hardcoded name list.
    agg = agg.sort_values("votes", ascending=False)
    agg = agg.groupby("state", sort=False).head(2).reset_index(drop=True)

    con = get_connection()
    cur = con.cursor()

    # Scope-wipe THIS year's historical rows before reloading. historical_results
    # has no UNIQUE constraint, so without this a re-run silently duplicates rows.
    cur.execute(
        "DELETE FROM historical_results "
        "WHERE race_id IN (SELECT id FROM races WHERE year = ?)",
        (year,),
    )

    loaded = 0
    for state, g in agg.groupby("state", sort=False):
        race_id = upsert_race(cur, year, state)
        winner_label = g["votes"].idxmax()  # row-index of the top vote-getter in this race
        for idx, r in g.iterrows():
            party = classify_party(r["party_simplified"])
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


if __name__ == "__main__":
    load_historical_senate()
