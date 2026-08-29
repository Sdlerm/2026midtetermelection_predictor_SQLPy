"""
load_historical_polls.py — load 2018/2020 Senate polls so the model can be
backtested against elections whose outcomes are already known.

WHY ONLY 2018 AND 2020
----------------------
BACKTEST_SCOPE.md asked for 2018-2024. Only 2018 and 2020 are recoverable.
FiveThirtyEight shut down in 2025 and its polls-page CSVs now serve the ABC News
HTML shell (verify: curl the URL in the README — it returns 314 KB of markup with
a 200 status, which is why a naive downloader would happily save it as a .csv).
The surviving copy is a git-scraped mirror, simonw/fivethirtyeight-polls, whose
last commit is 2021-04-05. That snapshot holds the complete 2018 and 2020 general
polling — the scrape ran past the January 2021 Georgia runoffs — and 34 rows of
the 2022 cycle, which had barely begun. 2022 and 2024 Senate polling is not in
any mirror found; see the note at the bottom of this file before re-searching.

So: two cycles, one midterm and one presidential year. That is a real sample for
a per-race quantity like the poll/lean blend weight. It is NOT a sample for a
per-cycle quantity like SIGMA_NATIONAL_MARGIN, which needs one observation per
election — BACKTEST_SCOPE.md §5 already made that argument and it still holds.

WHAT THIS SCRIPT WRITES
-----------------------
Poll rows attached to the 2018/2020 races that load_historical.py already
created, and to the candidates it already loaded from the actual results. It
never creates races or candidates: a poll for somebody who did not appear on the
general-election ballot is primary-era noise and is dropped, which is the same
rule senate_nominees.csv enforces for 2026.

Year-scoped like every other ingest here (senate_ingest.py owns year=2026,
district=''), so the three loaders cannot touch each other's rows.
"""

import csv
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime

from init_db import get_connection

POLLS_PATH = os.path.join(os.path.dirname(__file__), "data",
                          "senate_polls_538_historical.csv")

CYCLES = (2018, 2020)

# Races excluded with a reason, rather than silently producing nonsense.
#
# The races table is keyed UNIQUE(year, state, district) and district is '' for
# every Senate seat, so a state holding TWO Senate races in one year can only be
# represented once. load_historical.py stored one of them; polls for the other
# would land on the wrong race. Rather than special-case a seat identifier
# through the whole schema for three races, they are dropped and named here.
#
# The two 2020 exclusions are a different problem: Louisiana's jungle primary
# and Georgia's runoffs mean the stored result is not the outcome of a two-way
# November contest, so a two-way poll margin has nothing to be scored against.
EXCLUDED = {
    (2018, "MS"): "two Senate races (Wicker regular + Hyde-Smith special); "
                  "stored result is the special, which went to a runoff",
    (2018, "MN"): "two Senate races (Klobuchar regular + Smith special)",
    (2020, "GA"): "two Senate races, both decided in January 2021 runoffs",
    (2020, "LA"): "jungle primary; stored result is the first round, not a "
                  "two-way general",
}

STATE_ABBREV = {
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

# 538 party codes -> this schema's codes. Everything that is not one of the two
# major parties becomes 'I', matching how load_historical.py recorded Al Gross
# (AK-2020) and Ricky Harrington (AR-2020).
PARTY = {"DEM": "D", "REP": "R"}

NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# A poll more than this far before its election is cycle-stale. Mirrors the
# 18-month filter senate_ingest.py applies to the live feed.
STALE_DAYS = 548


# Fallback credibility per letter grade, on the SAME scale as
# pollster_ratings.csv: a uniform 0.2 step from A+ = 3.0 down to F = 0.2,
# with each compound grade sitting in the slot between its two letters
# (A/B between A- and B+, and so on).
#
# Two exceptions to the step, both inherited from the CSV rather than invented
# here:
#   C/D = 1.0, not the 0.9 its slot implies. 1.0 is the unrated default in
#     load_pollster_ratings.py and in the pollsters table, and C/D means
#     exactly that — unproven, not bad — so it is pinned to the default.
#   D/F = 0.3, midway between D- and F, because no CSV row carries it.
#
# This table only decides grades the CSV happens not to contain; the loop below
# overrides every grade the CSV does list. That is what let it drift out of
# sync once already (it read A/B = 2.3 against the CSV's 2.4) without anything
# noticing, because every grade in the historical file happened to be
# CSV-covered. Four grades now rest on a single CSV row each, so one hand edit
# to that file is all it takes to make these numbers load-bearing. Keep the two
# scales identical.
GRADE_LADDER = {
    "A+": 3.0, "A": 2.8, "A-": 2.6,
    "A/B": 2.4,
    "B+": 2.2, "B": 2.0, "B-": 1.8,
    "B/C": 1.6,
    "C+": 1.4, "C": 1.2, "C-": 1.0,
    "C/D": 1.0,
    "D+": 0.8, "D": 0.6, "D-": 0.4,
    "D/F": 0.3,
    "F": 0.2,
}


def grade_to_credibility():
    """
    {letter grade: numeric credibility}, taken from pollster_ratings.csv so
    the historical rows sit on exactly the same scale as the live ones, with
    GRADE_LADDER filling grades that file happens not to contain.

    Only the RATIOS matter to the weighted average (load_pollster_ratings.py
    says so), which is why filling gaps from a ladder built on the CSV's own
    0.2 spacing is safe rather than a second, competing scale.

    A grade the CSV rates differently from GRADE_LADDER is reported rather than
    silently accepted: the CSV still wins, but any disagreement means the
    fallback has gone stale and the gap grades around it are no longer on one
    scale with it.
    """
    ladder = dict(GRADE_LADDER)

    drifted = []
    ratings = os.path.join(os.path.dirname(__file__), "pollster_ratings.csv")
    if os.path.exists(ratings):
        with open(ratings, newline="") as f:
            for row in csv.DictReader(f):
                grade, numeric = row.get("grade"), row.get("numeric_grade")
                if grade and numeric:
                    grade, numeric = grade.strip(), float(numeric)
                    if grade in GRADE_LADDER and numeric != GRADE_LADDER[grade]:
                        drifted.append(f"{grade}: CSV {numeric} vs ladder "
                                       f"{GRADE_LADDER[grade]}")
                    ladder[grade] = numeric

    if drifted:
        print("WARNING: pollster_ratings.csv disagrees with GRADE_LADDER in "
              "load_historical_polls.py; the CSV wins, but the grades it does "
              "not list are left on a different scale: "
              + ", ".join(sorted(set(drifted))))
    return ladder


def register_pollsters(cur, path):
    """
    Add the 538 file's pollsters to the pollsters table, with their fte_grade.

    INSERT ONLY — never UPDATE. An existing row's grade and credibility come
    from pollster_ratings.csv and drive the LIVE 2026 forecast; overwriting
    them with the mirror's 2021-vintage grade would quietly re-weight every
    current race as a side effect of setting up a backtest. A name that is
    already present is therefore left exactly as it is.

    Without this step 72% of historical poll rows fall through
    weighted_average_and_stderr's COALESCE to credibility 1.0, and the backtest
    would be scoring an unweighted average while appearing to score the model's.

    LOOKAHEAD: these grades are from the April 2021 scrape, so they encode 2018
    and 2020 pollster performance — the very cycles being predicted. That is
    smaller than the 2026 snapshot's leak but not zero, and it is why
    backtest_senate.py also reports an equal-credibility run.
    """
    ladder = grade_to_credibility()

    cur.execute("SELECT name FROM pollsters")
    known = {name for (name,) in cur.fetchall()}

    seen = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            name, grade = row["pollster"].strip(), (row["fte_grade"] or "").strip()
            if name and name not in known:
                seen.setdefault(name, grade or None)

    added = 0
    for name, grade in seen.items():
        cur.execute(
            "INSERT INTO pollsters (name, credibility, grade) VALUES (?,?,?)",
            (name, ladder.get(grade, 1.0), grade),
        )
        added += 1
    return added, len(seen) - added


def parse_538_date(value):
    """
    '11/2/20' -> date(2020, 11, 2).

    538 writes M/D/YY, the polls table stores ISO YYYY-MM-DD, and everything
    downstream calls date.fromisoformat on it. Converting at the boundary is what
    keeps that contract: loading the raw string would make every historical poll
    raise the moment recency_weight touched it. Two-digit years resolve through
    %y (00-68 -> 2000s), which covers every cycle this file will ever hold.
    """
    return datetime.strptime(value.strip(), "%m/%d/%y").date()


def _norm(name):
    """Lowercase, de-accent, strip punctuation. 'Kevin de León' -> 'kevin de leon'."""
    ascii_name = (unicodedata.normalize("NFKD", name or "")
                  .encode("ascii", "ignore").decode())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", ascii_name.lower())).strip()


def surname(name):
    """
    Match key: the last name token, suffixes removed.

    Surname beats full-name matching here because the two sources disagree on
    given names constantly and agree on surnames almost always — the results
    files say "Bob Menendez", "Bob Casey Jr.", "Jack Reed" where 538 says
    "Robert Menendez", "Robert P. Casey Jr.", "John F. Reed". Dropping the
    suffix matters for its own reason: without it the last token of
    "Angus S. King Jr." is "jr", which matches nothing.

    Collisions are checked for at load time rather than assumed away — see
    build_candidate_index.
    """
    parts = [p for p in _norm(name).split() if p not in NAME_SUFFIXES]
    return parts[-1] if parts else ""


def build_candidate_index():
    """
    {(year, state): {(surname, party): candidate_id}} over candidates who
    actually appeared on a general-election ballot, i.e. those with a row in
    historical_results.

    Raises on a surname+party collision inside one race. A collision would mean
    two same-party candidates with the same surname in the same state-year, and
    silently keeping whichever row came last would attach polls to the wrong
    person — a class of error the rest of this file is built to avoid.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT r.year, r.state, c.id, c.name, c.party
        FROM races r
        JOIN candidates c ON c.race_id = r.id
        JOIN historical_results h ON h.candidate_id = c.id
        WHERE r.year IN ({}) AND r.district = ''
        """.format(",".join("?" * len(CYCLES))),
        CYCLES,
    )
    rows = cur.fetchall()
    con.close()

    index, seen = defaultdict(dict), {}
    for year, state, cand_id, name, party in rows:
        key = (surname(name), party)
        if key in index[(year, state)]:
            raise ValueError(
                f"Ambiguous candidate key {key} in {year} {state}: "
                f"'{name}' collides with '{seen[(year, state, key)]}'. "
                f"Surname+party is not unique here — this race needs an "
                f"explicit alias before its polls can be loaded."
            )
        index[(year, state)][key] = cand_id
        seen[(year, state, key)] = name
    return index


def _poll_party(code, race_index):
    """
    538 party code -> schema code, with one documented fallback.

    Independents are coded inconsistently across sources: Al Gross ran in 2020
    as an independent with Democratic backing and appears under more than one
    label, and Ricky Harrington was the Libertarian. Both are stored as 'I' by
    load_historical.py. So when a race has an 'I' candidate and no candidate of
    the poll's own party, the poll is read as being about the independent.
    """
    mapped = PARTY.get(code)
    if mapped and any(k[1] == mapped for k in race_index):
        return mapped
    if any(k[1] == "I" for k in race_index):
        return "I"
    return mapped


def pick_question_blocks(rows):
    """
    One question block per poll, chosen by nominee coverage.

    A single 538 poll_id often carries several question_ids: a head-to-head, a
    full field with minor parties, and — before a primary — one block per
    hypothetical nominee. Loading all of them counts the same poll several times
    and, worse, counts matchups that never happened. senate_ingest.py solves
    this for the live feed by keeping the block that best matches the nominee
    list; the nominee list here is "who actually appeared in the results", which
    is strictly better information.

    Keeps the block with the most matched general-election candidates, breaking
    ties toward the SMALLER field — between a clean head-to-head and the same
    two candidates buried in a six-way, the head-to-head is the cleaner read of
    the two-party margin.
    """
    by_question = defaultdict(list)
    for row in rows:
        by_question[(row["poll_id"], row["question_id"])].append(row)

    best_per_poll = {}
    for (poll_id, question_id), block in by_question.items():
        matched = sum(1 for r in block if r["candidate_id"] is not None)
        score = (matched, -len(block))
        if poll_id not in best_per_poll or score > best_per_poll[poll_id][0]:
            best_per_poll[poll_id] = (score, question_id, block)

    kept = []
    for _score, _question_id, block in best_per_poll.values():
        kept.extend(r for r in block if r["candidate_id"] is not None)
    return kept, len(by_question), len(best_per_poll)


def load_historical_polls(path=POLLS_PATH):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. It is a trimmed copy of 538's senate_polls.csv "
            f"from the simonw/fivethirtyeight-polls mirror (2018+2020 general "
            f"rows only) — see this file's docstring for provenance."
        )

    candidate_index = build_candidate_index()

    con = get_connection()
    cur = con.cursor()

    cur.execute(
        "SELECT id, year, state FROM races WHERE year IN ({}) AND district = ''"
        .format(",".join("?" * len(CYCLES))), CYCLES,
    )
    race_ids = {(y, s): rid for rid, y, s in cur.fetchall()}

    pollsters_added, _existing = register_pollsters(cur, path)

    cur.execute("SELECT id, name FROM pollsters")
    pollster_ids = {name: pid for pid, name in cur.fetchall()}

    staged, skipped = [], defaultdict(int)
    excluded_races = set()

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            year = int(row["cycle"])
            state = STATE_ABBREV.get(row["state"])
            if state is None:
                skipped["unrecognized state"] += 1
                continue
            if (year, state) in EXCLUDED:
                excluded_races.add((year, state))
                skipped["excluded race"] += 1
                continue
            race_id = race_ids.get((year, state))
            if race_id is None:
                skipped["no race row"] += 1
                continue

            race_index = candidate_index.get((year, state), {})
            party = _poll_party(row["candidate_party"], race_index)
            cand_id = race_index.get((surname(row["candidate_name"]), party))
            # cand_id None is expected and common: minor-party candidates and
            # pre-primary hypotheticals. Kept in the staging list so
            # pick_question_blocks can still measure a block's field size.

            try:
                end_date = parse_538_date(row["end_date"])
                lag = (parse_538_date(row["election_date"]) - end_date).days
            except ValueError:
                skipped["unparseable date"] += 1
                continue
            if lag > STALE_DAYS:
                skipped[f"older than {STALE_DAYS}d before election"] += 1
                continue
            if lag < 0:
                skipped["dated after election day"] += 1
                continue

            try:
                pct = float(row["pct"])
            except (TypeError, ValueError):
                skipped["unparseable pct"] += 1
                continue

            sample = row["sample_size"]
            staged.append({
                "poll_id": row["poll_id"],
                "question_id": row["question_id"],
                "race_id": race_id,
                "candidate_id": cand_id,
                "pollster_id": pollster_ids.get(row["pollster"]),
                "poll_date": end_date.isoformat(),
                "sample_size": int(float(sample)) if sample else None,
                "pct": pct,
            })

    kept, n_questions, n_polls = pick_question_blocks(staged)

    cur.execute(
        "DELETE FROM polls WHERE race_id IN (SELECT id FROM races WHERE "
        "year IN ({}) AND district = '')".format(",".join("?" * len(CYCLES))),
        CYCLES,
    )
    cur.executemany(
        "INSERT INTO polls (race_id, candidate_id, pollster_id, poll_date, "
        "sample_size, pct) VALUES (?,?,?,?,?,?)",
        [(r["race_id"], r["candidate_id"], r["pollster_id"], r["poll_date"],
          r["sample_size"], r["pct"]) for r in kept],
    )
    con.commit()

    cur.execute(
        """
        SELECT r.year, COUNT(DISTINCT r.state), COUNT(p.id)
        FROM races r JOIN polls p ON p.race_id = r.id
        WHERE r.year IN ({}) AND r.district = '' GROUP BY r.year
        """.format(",".join("?" * len(CYCLES))), CYCLES,
    )
    per_year = cur.fetchall()
    unrated = sum(1 for r in kept if r["pollster_id"] is None)
    con.close()

    return {
        "loaded": len(kept),
        "question_blocks_seen": n_questions,
        "polls_deduped_to": n_polls,
        "per_year": per_year,
        "unrated_pollster_rows": unrated,
        "pollsters_added": pollsters_added,
        "skipped": dict(skipped),
        "excluded_races": sorted(excluded_races),
    }


if __name__ == "__main__":
    summary = load_historical_polls()

    print(f"\n{'='*70}\nHISTORICAL SENATE POLLS — 2018 + 2020\n{'='*70}")
    print(f"  Poll rows loaded:        {summary['loaded']:,}")
    print(f"  Question blocks seen:    {summary['question_blocks_seen']:,} "
          f"-> {summary['polls_deduped_to']:,} polls after block selection")
    for year, states, rows in summary["per_year"]:
        print(f"  {year}: {rows:,} rows across {states} races")

    print("\n  Skipped rows (by reason):")
    for reason, count in sorted(summary["skipped"].items(), key=lambda x: -x[1]):
        print(f"    {count:>5}  {reason}")

    print(f"\n  {summary['pollsters_added']} pollsters newly registered with their "
          f"538 grade (insert-only — existing rows, which drive the live 2026 "
          f"forecast, were left untouched).")
    print(f"  {summary['unrated_pollster_rows']:,} loaded rows still have no "
          f"pollster row and fall back to credibility 1.0.")

    if summary["excluded_races"]:
        print("\n  Races deliberately excluded:")
        for year, state in summary["excluded_races"]:
            print(f"    {year} {state} — {EXCLUDED[(year, state)]}")

    print(f"\n  Next: python backtest_senate.py")
    print(f"{'='*70}")
