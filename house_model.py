"""
house_model.py — Tier 2 projections for all 435 House districts.

WHAT CHANGED FROM TIER 1
------------------------
Tier 1 covered only polled districts and refused to say anything about the
other ~396. Tier 2 gives every district a projection by falling back to a
structural lean, exactly as senate_model.py already does for unpolled states.
Two mechanisms, one formula:

    polled district:    LEAN_ALPHA_HOUSE * poll_avg + (1-alpha) * (lean + env/2)
    unpolled district:                                            lean + env/2

The second is the first with the poll term unavailable. Rows carry has_polls
so display can tell them apart instead of dressing structural guesses up as
polled projections.

`env` is the national House environment as a D-minus-R margin (see
national_environment_margin), which is why it enters each candidate's SHARE at
half weight. It shifts the lean BASELINE rather than the finished projection:
polls already contain the 2026 environment, so a polled district must not be
shifted twice, and it feels the term only through the (1-alpha) lean weight.

WHAT TIER 2 STILL DOES NOT DO
-----------------------------
  * NO chamber-control probability IN THIS FILE. 435 point estimates are not a
    control call — that needs a Monte Carlo over correlated district errors.
    This file reports seat COUNTS only; monte_carlo_house.py turns them into
    probabilities and owns the correlation structure and the per-district error
    sigmas (which differ for polled vs lean-only districts).
  * NO MEASURED generic ballot. 538's generic-ballot feed is dead (its polls-
    page CSVs all return HTML now), so the national environment is INFERRED
    from presidential approval via the midterm regression in calibration.py
    rather than measured. national_environment_margin() prefers a stored
    GENERIC_BALLOT_D factor and falls back to the regression, so ingesting real
    generic-ballot polling upgrades this automatically. Until then the
    environment term carries a ~2.6pp regression residual on top of everything
    else, which is why SIGMA_NATIONAL_MARGIN_HOUSE is not small.
  * NO INCUMBENCY. house_nominees.csv names an incumbent in only ~83 districts,
    so uniform swing is applied to a presidential lean with no incumbency
    correction. This matters most where it is least visible: a well-entrenched
    incumbent in a district the environment says should flip. The lean-only
    sigma absorbs it as noise; it does not correct for it.
  * NO incumbency for unrostered districts. house_nominees.csv covers ~83
    districts; the other ~350 have no known incumbent, so their flip status is
    unknowable and is reported as False rather than guessed. Seat-count deltas
    against the current chamber are therefore not available here.

NO TOSS-UP LABEL
----------------
Every district is projected for whichever candidate holds the greater projected
vote share. A 0.2pt gap is a (very soft) call, not an abstention, so nothing is
withheld into a neutral bucket. Closeness is carried by the margin itself, by
the "within Npt" count in the summary below, and on the dashboard map by the
pale Tilt D / Tilt R color bands. The is_tossup flag that _finalize_race
attaches for the Senate still rides along on these rows; House output ignores
it.

BIAS LEDGER
-----------
The lean under ~396 of these districts is 538's 2022-vintage partisan lean —
2016/2020 presidential results on 2022 maps. It contains no 2024, and for the
95 districts in redrawn states it describes boundaries that no longer exist
(see fetch_district_lean.py, which prints them on every run). Unpolled
projections are therefore materially weaker than polled ones, and weakest of
all in TX/NC/OH/FL. The honest read: this file now covers the chamber, but
coverage is not accuracy.
"""

import csv
import os

from calibration import (
    TILT_MARGIN_THRESHOLD,
    MIDTERM_SLOPE, MIDTERM_INTERCEPT, MIDTERM_RESIDUAL_SD,
    PRESIDENT_PARTY,
)
from init_db import get_connection
from senate_model import (
    weighted_average_and_stderr,
    get_climate_score, climate_adjustment,
    get_approval_score, approval_adjustment,
    _finalize_race, two_way_poll_sum_ok, matched_poll_blocks,
)
from house_ingest import load_house_nominees, _pad

LEAN_ALPHA_HOUSE = 0.80   # (1-alpha) = 0.20 is the lean weight
# Was documented as "matches senate_model.LEAN_ALPHA" — it does not: that value
# is 0.82. Left at 0.80 because it is UNBACKTESTED and copying the Senate's
# figure would import a number measured on statewide polls into district polls,
# which are sparser and more often partisan-sponsored. See backtest_senate.py
# for the Senate result and why a House equivalent needs historical district
# lean vintages before it can be run at all.

DISTRICT_LEAN_PATH = os.path.join(os.path.dirname(__file__), "data", "district_lean.csv")

GENERIC_NAME = {"D": "Democratic candidate", "R": "Republican candidate"}


def load_district_lean(path=DISTRICT_LEAN_PATH):
    """
    Read data/district_lean.csv into {(state, district): dem_margin}.

    Generated by fetch_district_lean.py — a missing file is a setup error, not
    a data gap, so it raises rather than silently degrading all 435 districts
    to neutral (which would look like a working model producing 50-50 ties).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — run fetch_district_lean.py first."
        )

    lean = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("district") or "").strip().upper()
            margin = (row.get("dem_margin") or "").strip()
            if not key or not margin:
                continue
            state, _, district = key.partition("-")
            lean[(state, _pad(district))] = float(margin)
    return lean


def national_environment_margin(year=2026):
    """
    The 2026 national House environment, as a D-minus-R MARGIN in points.
    Returns (margin, source).

    Two sources, preferred order:

    1. A stored GENERIC_BALLOT_D climate factor, read as the D-minus-R generic
       ballot margin. This is the real measurement, and init_db.py has always
       named the slot for it; nothing populates it yet because 538's generic-
       ballot feed went dead. Ingest one and it takes over automatically.

    2. Otherwise, presidential approval run through the midterm regression in
       calibration.py. The relationship is thin (n=8) but it is the right SHAPE:
       it carries the midterm penalty, which is the single largest term in any
       midterm House forecast and the one the econ/approval share nudges could
       not express at all — they topped out at D+1.3 on the margin scale with
       the president at 39.7% approval, where the historical relationship says
       D+7.2.

    Sign: positive = Democrats lead the national House vote. The regression is
    fit in the president's-party direction, so it is negated when Republicans
    hold the White House.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT value FROM climate_factors WHERE year = ? AND factor_name = 'GENERIC_BALLOT_D'",
        (year,),
    )
    row = cur.fetchone()
    con.close()
    if row is not None:
        return round(float(row[0]), 2), "generic ballot (measured)"

    approval = _stored_approval(year)
    if approval is None:
        # No approval either: return a flat environment rather than inventing
        # one. This is the "no data, no adjustment" fallback the climate and
        # approval getters already use.
        return 0.0, "none available — flat environment"

    pres_party_margin = MIDTERM_SLOPE * (approval - 50.0) + MIDTERM_INTERCEPT
    margin = pres_party_margin if PRESIDENT_PARTY == "D" else -pres_party_margin
    return round(margin, 2), (
        f"approval {approval:.1f}% via midterm regression (±{MIDTERM_RESIDUAL_SD:.1f})"
    )


def _stored_approval(year):
    """Raw PRES_APPROVAL percentage, or None. get_approval_score() normalizes to
    a -1..+1 score, which cannot be inverted back to points — the regression
    needs the actual approval number, so it reads the factor directly."""
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT value FROM climate_factors WHERE year = ? AND factor_name = 'PRES_APPROVAL'",
        (year,),
    )
    row = cur.fetchone()
    con.close()
    return None if row is None else float(row[0])


def district_lean_baseline(state, district, party, district_lean, env_margin=0.0):
    """
    Structural baseline as a vote-share %, from the stored signed margin,
    shifted to the current national environment.
    Same derivation as senate_model.lean_baseline:
        margin = dem_share - rep_share = 2*dem_share - 100  ->  dem_share = margin/2 + 50

    env_margin is a national D-minus-R MARGIN, so it enters each share at half
    weight — one point of margin is half a point of vote share. This is uniform
    swing: 538's partisan lean measures a district against a NATIONALLY TIED
    vote, so it is a baseline, not a forecast, and it has to be shifted to the
    environment you are actually forecasting before it can be one. Adding the
    environment here rather than to the finished projection is what keeps polled
    districts from being double-shifted: their polls already contain the 2026
    environment, and the shift reaches them only through the (1-alpha) lean
    weight.

    An unknown district returns 50.0 plus the environment rather than raising —
    house.csv occasionally polls a district that district_lean.csv has never
    heard of, and a neutral-but-shifted baseline is the right answer there.
    """
    margin = district_lean.get((state, district))
    if margin is None:
        margin = 0.0

    shifted = margin + env_margin
    dem_share = 50.00 + shifted / 2.00
    rep_share = 50.00 - shifted / 2.00
    if party == "D":
        return dem_share
    if party == "R":
        return rep_share
    return 50.0   # independents: no structural lean data exists at district level


def predict_house_races(year=2026):
    """
    Project all 435 districts. Returns (results, climate, approval).

    climate and approval are returned for display but are NO LONGER ADDED to
    House projections. The national environment now enters exactly once, through
    national_environment_margin() shifting the lean baseline. Keeping the old
    per-candidate econ/approval nudges alongside it would count the same
    national signal twice — approval literally drives both — and the nudges were
    the weaker measurement of the two: ±0.65 points of share against a
    regression that puts the 2026 environment at D+7.2 on the margin scale.
    The Senate still uses them, because there they are a small correction on top
    of polls rather than the only 2026 information in the model.
    """
    roster, _skipped = load_house_nominees()
    district_lean = load_district_lean()
    climate  = get_climate_score(year)
    approval = get_approval_score(year)
    env, env_source = national_environment_margin(year)

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT id, state, district FROM races WHERE year = ? AND district != ''",
        (year,),
    )
    races = cur.fetchall()

    results = []
    for race_id, state, district in races:
        finalists = []
        parties = [p for (s, d, p) in roster if s == state and d == district]
        incumbent_party = next(
            (p for p in parties if roster[(state, district, p)].get("is_incumbent")), ""
        )

        # Resolve every nominee to a candidate row FIRST — the matched-block set
        # is a property of the finalist field as a whole, so it cannot be
        # computed inside the per-candidate loop that consumes it.
        contenders = []
        for party in parties:
            info = roster[(state, district, party)]
            cur.execute(
                "SELECT id FROM candidates WHERE race_id = ? AND name = ?",
                (race_id, info["name"]),
            )
            row = cur.fetchone()
            if not row:
                continue                     # nominee never appeared in polls — skip (scripture rule)
            contenders.append((party, info, row[0]))

        blocks = matched_poll_blocks(race_id, [c[2] for c in contenders])

        for party, info, candidate_id in contenders:
            poll_avg, poll_stderr = weighted_average_and_stderr(
                race_id, candidate_id, blocks=blocks)
            if poll_avg is None:
                continue

            lean = district_lean_baseline(state, district, party, district_lean, env)
            # The environment is inside `lean`, so it reaches a polled district
            # only at (1-alpha) weight — the polls already carry 2026.
            projected = round(LEAN_ALPHA_HOUSE * poll_avg
                              + (1 - LEAN_ALPHA_HOUSE) * lean, 2)

            finalists.append({
                "state": state, "district": district,
                "race": f"{state}-{district}",
                "name": info["name"], "party": party,
                "poll_avg": poll_avg, "env": env, "env_source": env_source,
                "lean": round(lean, 2),
                "projected": projected,
                "poll_stderr": poll_stderr,
                "has_polls": True,
                "is_incumbent": info.get("is_incumbent", False),
                "incumbent_party": incumbent_party,
                "incumbent_known": bool(incumbent_party),
            })

        # A one-sided polled race is a data gap, not a contest — discard it and
        # let the lean-only pass below rebuild the district coherently. The same
        # goes for a pair whose shares cannot be a two-way general election; the
        # guard is shared with the Senate, see senate_model.two_way_poll_sum_ok.
        if len(finalists) < 2 or not two_way_poll_sum_ok(finalists, f"{state}-{district}"):
            continue

        results.extend(_finalize_race(finalists))

    con.close()

    # ---- Lean-only pass: every district without a poll-based projection -----
    # Runs over district_lean.csv's key set, which IS the 435-district universe.
    # Names come from the roster when it has them (a rostered-but-unpolled
    # district has real nominees) and are generic otherwise.
    projected = {(r["state"], r["district"]) for r in results}

    for (state, district) in sorted(district_lean):
        if (state, district) in projected:
            continue

        incumbent_party = next(
            (p for p in ("D", "R")
             if roster.get((state, district, p), {}).get("is_incumbent")), ""
        )

        finalists = []
        for party in ("D", "R"):
            info = roster.get((state, district, party), {})
            lean = district_lean_baseline(state, district, party, district_lean, env)
            finalists.append({
                "state": state, "district": district,
                "race": f"{state}-{district}",
                "name": info.get("name") or GENERIC_NAME[party],
                "party": party,
                "poll_avg": None, "env": env, "env_source": env_source,
                "lean": round(lean, 2),
                # Environment-shifted lean IS the projection here — there is
                # nothing else known about this district in 2026.
                "projected": round(lean, 2),
                "poll_stderr": None,
                "has_polls": False,
                "is_incumbent": info.get("is_incumbent", False),
                "incumbent_party": incumbent_party,
                # Unrostered districts have no known incumbent, so _finalize_race
                # will report is_flip=False. That is "unknown", not "hold".
                "incumbent_known": bool(incumbent_party),
            })

        results.extend(_finalize_race(finalists))

    return results, climate, approval


if __name__ == "__main__":
    results, climate, approval = predict_house_races()

    _env, _env_source = national_environment_margin()
    print(f"Climate score: {climate:+.3f} · Approval score: {approval:+.2f}")
    print(f"National House environment: D{_env:+.2f} margin  [{_env_source}]")
    print(f"  Applied to every district's lean baseline; polled districts feel it "
          f"at {1 - LEAN_ALPHA_HOUSE:.0%} weight (their polls already carry 2026).")
    print(f"Tier 2 — all districts projected; polled and lean-only marked separately\n")

    polled_races = {r["race"] for r in results if r["has_polls"]}

    def print_section(rows, heading):
        """
        Print one basis section: districts grouped by race, alphabetical.

        Sorting on the "XX-NN" race key orders by state abbreviation first and
        district second, and house_ingest._pad already zero-pads the district
        so '02' sorts before '10' — the reason districts are stored as padded
        text in the first place.

        Poll-backed and lean-only rows print different detail fields because
        they carry different information: a lean-only row has no poll average
        and no stderr, and padding it with placeholders would make a structural
        guess look like a thin poll.
        """
        print(f"\n{'═'*70}\n{heading}\n{'═'*70}")
        if not rows:
            print("  (none)")
            return

        current = None
        for r in sorted(rows, key=lambda x: x["race"]):
            if r["race"] != current:
                current = r["race"]
                print(f"\n── {current} ──────────────")
            marker = "★" if r.get("winner") else " "
            inc    = " [incumbent]" if r["is_incumbent"] else ""
            flip   = " ⚡FLIP" if r.get("winner") and r.get("is_flip") else ""
            if r["has_polls"]:
                # stderr is None when a lone poll recorded no sample size —
                # unknown, not zero. Say so rather than crashing on the format.
                err = f"±{r['poll_stderr']:.1f}%" if r["poll_stderr"] is not None else "±?"
                detail = f"poll: {r['poll_avg']:.1f}% {err}  lean+env: {r['lean']:.1f}%"
            else:
                detail = f"lean+env only: {r['lean']:.1f}%"
            print(f"  {marker} {r['party']}  {r['name']:<28} {detail}  "
                  f"env: D{r['env']:+.1f} margin  → {r['projected']:.1f}%{inc}{flip}")

    polled    = [r for r in results if r["has_polls"]]
    lean_only = [r for r in results if not r["has_polls"]]

    print_section(polled, f"POLLED DISTRICTS — {len(polled_races)} districts")

    # Lean-only districts are computed and COUNTED, just not itemized: 399
    # per-candidate breakdowns would restate district_lean.csv at length and
    # bury the ~36 districts that carry actual poll information. They still
    # flow into the seat totals below — which is the whole point of Tier 2, so
    # the summary reports the polled/lean-only split on every line rather than
    # letting the suppressed rows disappear from view.
    lean_only_races = {r["race"] for r in lean_only}
    if lean_only_races:
        print(f"\n{len(lean_only_races)} lean-only district(s) not itemized above, "
              f"included in the seat counts below.")

    winners = [r for r in results if r.get("winner")]
    d_leads = sum(1 for r in winners if r["party"] == "D")
    r_leads = sum(1 for r in winners if r["party"] == "R")
    d_polled = sum(1 for r in winners if r["party"] == "D" and r["has_polls"])
    r_polled = sum(1 for r in winners if r["party"] == "R" and r["has_polls"])
    # Districts inside the closest rating band. NOT reported as toss-ups: each
    # one is already counted above for whichever candidate leads it, however
    # thinly. This line says how soft those leads are, not that they are
    # unresolved — the dashboard colors them the same way, Tilt D / Tilt R.
    tilts = sum(1 for r in winners if abs(r.get("margin", 0.0)) < TILT_MARGIN_THRESHOLD)

    print(f"\n{'─'*62}")
    print(f"  Districts projected: {len(winners)}   "
          f"(polled: {len(polled_races)}  ·  lean-only: {len(winners) - len(polled_races)})")
    print(f"  D leads: {d_leads}  (polled {d_polled}, lean-only {d_leads - d_polled})")
    print(f"  R leads: {r_leads}  (polled {r_polled}, lean-only {r_leads - r_polled})")
    print(f"  Within {TILT_MARGIN_THRESHOLD:g}pt (already counted in the leads above): {tilts}")
    print(f"\n  Seat COUNTS only — not a control probability. Lean-only districts")
    print(f"  rest on 2022-vintage lean; see the bias ledger at the top of this file.")
    print(f"{'─'*62}")
