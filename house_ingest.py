import os
import pandas as pd
from init_db import get_connection
from senate_ingest import upsert_pollster, upsert_race, upsert_candidate

_HOUSE_NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "house_nominees.csv")
_HOUSE_POLLS_PATH    = os.path.join(os.path.dirname(__file__), "data", "house.csv")


def _pad(district):
    """Normalize any district representation (1, 1.0, '1') to zero-padded text: '01'.
    Zero-padding matters because TEXT sorts character-by-character ('10' < '7' but '07' < '10')."""
    return f"{int(float(district)):02d}"


def load_house_nominees(path=_HOUSE_NOMINEES_PATH):
    """
    Read the wide-format nominees file and reshape to the long dict shape the
    rest of the pipeline expects: {(state, district, party): {"name": ...}}.

    A district enters the roster ONLY if BOTH dem_nominee and rep_nominee are
    filled (Samuel's rule, 2026-07-17). Everything else is returned in
    `skipped` with a reason, for the end-of-run summary. The CSV itself is
    scripture — this function adapts, never edits.
    """
    df = pd.read_csv(path)
    roster, skipped = {}, []

    for _, row in df.iterrows():
        state    = str(row["state"]).strip().upper()
        district = _pad(row["district"])
        key      = f"{state}-{district}"

        dem = str(row["dem_nominee"]).strip() if pd.notna(row["dem_nominee"]) else ""
        rep = str(row["rep_nominee"]).strip() if pd.notna(row["rep_nominee"]) else ""

        if not dem and not rep:
            skipped.append((key, "no nominees yet"))
            continue
        if not dem or not rep:
            skipped.append((key, "one side unsettled"))
            continue

        roster[(state, district, "D")] = {"name": dem}
        roster[(state, district, "R")] = {"name": rep}

        dem_inc = pd.notna(row.get("dem_incumbent")) and str(row["dem_incumbent"]).strip() not in ("", "0")
        rep_inc = pd.notna(row.get("rep_incumbent")) and str(row["rep_incumbent"]).strip() not in ("", "0")
        roster[(state, district, "D")] = {"name": dem, "is_incumbent": dem_inc}
        roster[(state, district, "R")] = {"name": rep, "is_incumbent": rep_inc}

        # Independent nominee is optional; include when present (future NE-style races)
        if pd.notna(row.get("ind_nominee")) and str(row["ind_nominee"]).strip():
            roster[(state, district, "I")] = {"name": str(row["ind_nominee"]).strip(), "is_incumbent": False}

    return roster, skipped


def _build_district_tokens(roster):
    """Per-district name tokens for question-block dedup — same idea as
    senate_ingest._build_state_tokens, keyed one level deeper."""
    tokens = {}
    for (state, district, _party), info in roster.items():
        bucket = tokens.setdefault((state, district), set())
        for tok in info["name"].lower().split():
            if len(tok) > 2:
                bucket.add(tok)
    return tokens


def load_house_polls(filepath=_HOUSE_POLLS_PATH, year=2026):
    """Replace the 2026 House poll data in the DB with the contents of `filepath`.

    DESTRUCTIVE, but only after the input is known good: every read, filter
    and dedup below runs before the wipe, and the wipe shares one transaction
    with the inserts. A missing or malformed house.csv therefore raises with
    the existing rows untouched, rather than emptying the table and then
    failing on the read.
    """
    roster, skipped_nominees = load_house_nominees()
    rostered_districts = {(s, d) for (s, d, _p) in roster}

    df = pd.read_csv(filepath)

    # Same filter battery as Senate ingest, one addition: seat_number must
    # exist. ORDER MATTERS — the notna() filter must precede _pad(), because
    # int(NaN) crashes. Generic-ballot and primary rows are what carry the
    # 2,329 null seat_numbers, so these filters also do that cleanup.
    df = df[
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP"])) &
        (~df["candidate_name"].isin(["Don't know", "Someone else",
                                     "Generic Democrat", "Generic Republican"])) &
        (df["seat_number"].notna())
    ].copy()

    df["state"]    = df["state"].astype(str).str.strip().str.upper()
    df["district"] = df["seat_number"].map(_pad)

    # Restrict to rostered districts. Polled-but-unrostered districts are
    # reported, not ingested — they're either primary noise or a nudge that
    # house_nominees.csv needs a new row.
    df["_key"] = list(zip(df["state"], df["district"]))
    unrostered = sorted({f"{s}-{d}" for (s, d) in df.loc[~df["_key"].isin(rostered_districts), "_key"]})
    df = df[df["_key"].isin(rostered_districts)].copy()

    # Recency hygiene — identical to Senate
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    df["pop_rank"] = df["population"].map(pop_priority).fillna(9)
    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])
    if "election_date" in df.columns:
        df["_election_dt"] = pd.to_datetime(df["election_date"], errors="coerce")
        stale = df["_election_dt"].notna() & ((df["_election_dt"] - df["end_date"]).dt.days > 548)
        if stale.any():
            print(f"Dropped {stale.sum()} stale House poll row(s)")
        df = df[~stale].drop(columns=["_election_dt"])

    # Question-block dedup, keyed one level deeper than Senate:
    # (poll_id, state, district) instead of (poll_id, state).
    if "question_id" in df.columns:
        district_tokens = _build_district_tokens(roster)
        df["question_id"] = df["question_id"].fillna("__default__")

        def _matches(name, toks):
            return any(t in toks for t in str(name).lower().split())

        keep_idx = []
        for (_pid, state, district), grp in df.groupby(["poll_id", "state", "district"], sort=False):
            toks = district_tokens.get((state, district), set())
            best_qid, best_score = None, (-1.0, -1)
            for qid, qdf in grp.groupby("question_id", sort=False):
                cands = qdf["candidate_name"].tolist()
                n = sum(1 for c in cands if _matches(c, toks))
                score = (n / len(cands) if cands else 0.0, n)
                if score > best_score:
                    best_score, best_qid = score, qid
            keep_idx.extend(grp.index[grp["question_id"] == best_qid].tolist())
        df = df.loc[keep_idx].reset_index(drop=True)

    # Population dedup — per (poll_id, district, candidate) so the same name
    # in two districts (it happens) can't collapse across races.
    df = df.sort_values("pop_rank")
    df = df.drop_duplicates(subset=["poll_id", "district", "candidate_name"], keep="first")

    df["party"]    = df["party"].map({"DEM": "D", "REP": "R"})
    df["end_date"] = df["end_date"].dt.strftime("%Y-%m-%d")

    con = get_connection()
    cur = con.cursor()

    # Wipe ONLY 2026 House rows: district != '' is the House half of the
    # partition (the Senate wipe owns district = ''). Plain English: "delete
    # polls and candidates belonging to 2026 races that have a district
    # value, then those races themselves" — a Senate refresh can't touch
    # these, and this can't touch Senate.
    #
    # This sits here, not in __main__ ahead of the call, for two reasons:
    # everything that can reject a bad house.csv has already run by this
    # line, and sqlite3's implicit transaction means the single commit below
    # covers the deletes too — so a crash part-way through the insert loop
    # rolls the wipe back with it instead of leaving the table empty.
    cleared = cur.execute(
        "SELECT COUNT(*) FROM races WHERE year = 2026 AND district != ''"
    ).fetchone()[0]
    cur.execute("DELETE FROM polls WHERE race_id IN (SELECT id FROM races WHERE year = 2026 AND district != '')")
    cur.execute("DELETE FROM candidates WHERE race_id IN (SELECT id FROM races WHERE year = 2026 AND district != '')")
    cur.execute("DELETE FROM races WHERE year = 2026 AND district != ''")
    print(f"Cleared {cleared} 2026 House race(s) and their polls.")

    loaded = 0
    for _, row in df.iterrows():
        pollster_id  = upsert_pollster(cur, str(row["pollster"]).strip(),
                                       row.get("numeric_grade"), row.get("partisan"))
        race_id      = upsert_race(cur, year, row["state"], row["district"])
        candidate_id = upsert_candidate(cur, race_id, str(row["candidate_name"]).strip(), row["party"])
        cur.execute("""
            INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, sample_size, pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (race_id, candidate_id, pollster_id, row["end_date"],
              int(row["sample_size"]) if pd.notna(row["sample_size"]) else None,
              float(row["pct"])))
        loaded += 1
    con.commit()
    con.close()

    # ---- Summary: every skip is loud, none is fatal ----
    polled = {(s, d) for (s, d) in df["_key"]} if "_key" in df else set()
    unpolled_roster = sorted(f"{s}-{d}" for (s, d) in rostered_districts if (s, d) not in polled)

    print(f"\nLoaded {loaded} House poll rows across {len(polled)} districts.")
    if skipped_nominees:
        print(f"{len(skipped_nominees)} nominee rows skipped:")
        for key, why in skipped_nominees:
            print(f"  - {key}: {why}")
    if unrostered:
        print(f"{len(unrostered)} polled district(s) not in house_nominees.csv (polls discarded): {', '.join(unrostered)}")
    if unpolled_roster:
        print(f"{len(unpolled_roster)} rostered district(s) with no polls — your future Tier 2 seed list: {', '.join(unpolled_roster)}")


if __name__ == "__main__":
    # The wipe lives inside load_house_polls, in the same transaction as the
    # inserts. This check only buys a readable message instead of a traceback.
    if not os.path.exists(_HOUSE_POLLS_PATH):
        print(f"ERROR: {_HOUSE_POLLS_PATH} not found. Download it manually and place it in data/")
    else:
        load_house_polls()
