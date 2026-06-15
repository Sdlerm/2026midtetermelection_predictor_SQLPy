"""
house_model.py — 2026 House Election Model

Queries elections.db for House poll data (district != '' or state='US').
Mirrors model.py architecture exactly: same weighted average, same
climate/approval adjustments, same lean-blend formula.

Pipeline:
    house.csv + house_nominees.csv
        → house_ingest.py
            → elections.db (races with district != '', generic ballot at state='US')
                → house_model.py
                    → predictions + generic ballot + House control projection
"""

import math
from datetime import date
from init_db import get_connection
from senate_model import (
    get_climate_score,
    get_approval_score,
    climate_adjustment,
    approval_adjustment,
    LAMBDA,
    ECON_WEIGHT,
    APPROVAL_WEIGHT,
    LEAN_ALPHA,
)

# ---------------------------------------------------------------------------
# District partisan lean — 2024 presidential D margin by district
# Positive = D-leaning, negative = R-leaning.
# Only districts with polling data in house.csv are listed for now.
# ---------------------------------------------------------------------------
DISTRICT_LEAN = {
    ("AZ",  "6"): -4.0,
    ("CA",  "3"): -4.0,
    ("CA", "22"): -4.0,
    ("CA", "48"): -2.0,
    ("CO",  "3"): -10.0,
    ("CO",  "5"): -18.0,
    ("FL", "13"): -4.0,
    ("FL", "28"): -10.0,
    ("IA",  "1"): -6.0,
    ("IA",  "2"): -2.0,
    ("IA",  "3"): -4.0,
    ("ME",  "2"): -6.0,
    ("MI",  "4"): -14.0,
    ("MN",  "1"): -10.0,
    ("MO",  "2"): -20.0,
    ("MT",  "1"): -22.0,
    ("NC",  "1"): -4.0,
    ("NC", "10"): -16.0,
    ("NC", "14"): -12.0,
    ("NE",  "1"): -14.0,
    ("NE",  "2"): +2.0,
    ("NH",  "2"): +6.0,
    ("NJ",  "2"): -14.0,
    ("NJ",  "7"): -2.0,
    ("NM",  "2"): -6.0,
    ("NY",  "1"): -8.0,
    ("OH",  "9"): -4.0,
    ("PA",  "1"): +4.0,
    ("PA",  "8"): -2.0,
    ("PA", "10"): -8.0,
    ("SC",  "1"): -14.0,
    ("TX", "15"): -4.0,
    ("TX", "23"): -6.0,
    ("TX", "34"): -4.0,
    ("VA",  "1"): -14.0,
    ("WA",  "3"): -4.0,
    ("WI",  "1"): -14.0,
    ("WI",  "3"): -4.0,
    ("WI",  "6"): -20.0,
}

# ---------------------------------------------------------------------------
# Core math (mirrors model.py)
# ---------------------------------------------------------------------------

def _recency_weight(poll_date_str):
    d = date.fromisoformat(poll_date_str)
    days = (date.today() - d).days
    return math.exp(-LAMBDA * days)


def _weighted_average(race_id, candidate_id):
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT p.pct, p.poll_date, COALESCE(po.credibility, 1.0)
        FROM polls p
        LEFT JOIN pollsters po ON p.pollster_id = po.id
        WHERE p.race_id = ? AND p.candidate_id = ?
    """, (race_id, candidate_id))
    rows = cur.fetchall()
    con.close()

    if not rows:
        return None

    num = den = 0.0
    for pct, poll_date, credibility in rows:
        w    = credibility * _recency_weight(poll_date)
        num += pct * w
        den += w
    return round(num / den, 1) if den > 0 else None


def _district_lean_baseline(state, district, party):
    lean = DISTRICT_LEAN.get((state, district), 0.0)
    if party == "D":
        return round(50.0 + lean / 2, 1)
    else:
        return round(50.0 - lean / 2, 1)

# ---------------------------------------------------------------------------
# Generic ballot
# ---------------------------------------------------------------------------

def get_generic_ballot(year=2026):
    """
    Returns {"D": float, "R": float} — recency/credibility weighted averages
    of national generic House ballot polls stored in DB as (state='US', district='').
    """
    con = get_connection()
    cur = con.cursor()

    cur.execute("""
        SELECT r.id FROM races r
        WHERE r.year = ? AND r.state = 'US' AND r.district = ''
    """, (year,))
    row = cur.fetchone()
    con.close()

    if not row:
        return {"D": None, "R": None}

    race_id = row[0]

    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT c.party, p.pct, p.poll_date, COALESCE(po.credibility, 1.0)
        FROM polls p
        JOIN candidates c ON p.candidate_id = c.id
        LEFT JOIN pollsters po ON p.pollster_id = po.id
        WHERE p.race_id = ?
    """, (race_id,))
    rows = cur.fetchall()
    con.close()

    buckets = {"D": (0.0, 0.0), "R": (0.0, 0.0)}
    for party, pct, poll_date, credibility in rows:
        if party not in buckets:
            continue
        w = credibility * _recency_weight(poll_date)
        num, den = buckets[party]
        buckets[party] = (num + pct * w, den + w)

    result = {}
    for party, (num, den) in buckets.items():
        result[party] = round(num / den, 1) if den > 0 else None
    return result

# ---------------------------------------------------------------------------
# Incumbent lookup from house_nominees.csv
# Used for flip detection — the DB candidates table doesn't store incumbency.
# ---------------------------------------------------------------------------

def _load_incumbency():
    """
    Returns dict (state, district, name) -> is_incumbent (bool).
    Reads house_nominees.csv directly — incumbency is editorial data,
    not something we derive from polls.
    """
    import csv, os
    path = os.path.join(os.path.dirname(__file__), "data", "house_nominees.csv")
    inc = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            state    = row["state"].strip()
            district = row["district"].strip()
            dem      = row.get("dem_nominee", "").strip()
            rep      = row.get("rep_nominee", "").strip()
            d_inc    = row.get("dem_incumbent", "").strip() == "1"
            r_inc    = row.get("rep_incumbent", "").strip() == "1"
            if dem:
                inc[(state, district, dem)] = d_inc
            if rep:
                inc[(state, district, rep)] = r_inc
    return inc

# ---------------------------------------------------------------------------
# Main prediction
# ---------------------------------------------------------------------------

def predict_house_races(year=2026):
    """
    Returns (results, generic_ballot, climate, approval_pct).
    results: list of candidate dicts, same shape as model.py predict_all_races().
    """
    climate      = get_climate_score(year)
    approval_pct = get_approval_score()
    incumbency   = _load_incumbency()

    # Fetch all House races (district != '', state != 'US')
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT id, state, district FROM races
        WHERE year = ? AND district != '' AND state != 'US'
    """, (year,))
    races = cur.fetchall()
    con.close()

    results = []

    for race_id, state, district in races:
        # Get all candidates for this race
        con = get_connection()
        cur = con.cursor()
        cur.execute("""
            SELECT id, name, party FROM candidates WHERE race_id = ?
        """, (race_id,))
        candidates = cur.fetchall()
        con.close()

        finalists = []

        for candidate_id, name, party in candidates:
            poll_avg = _weighted_average(race_id, candidate_id)
            if poll_avg is None:
                continue

            clim_adj  = climate_adjustment(party, climate)
            appr_adj  = approval_adjustment(party, approval_pct) if approval_pct is not None else 0.0
            baseline  = _district_lean_baseline(state, district, party)
            blended   = LEAN_ALPHA * poll_avg + (1 - LEAN_ALPHA) * baseline
            projected = round(blended + clim_adj + appr_adj, 1)

            is_incumbent = incumbency.get((state, district, name), False)

            finalists.append({
                "state":         state,
                "district":      district,
                "name":          name,
                "party":         party,
                "poll_avg":      poll_avg,
                "lean_baseline": baseline,
                "adjustment":    clim_adj,
                "appr_adj":      appr_adj,
                "projected":     projected,
                "is_incumbent":  is_incumbent,
                "is_flip":       False,
                "winner":        False,
            })

        if not finalists:
            continue

        finalists.sort(key=lambda x: x["projected"], reverse=True)
        finalists[0]["winner"] = True

        # Flip detection: winner is not an incumbent, but someone else in the
        # race is — meaning the seat is changing hands
        winner = finalists[0]
        any_incumbent = any(f["is_incumbent"] for f in finalists)
        winner["is_flip"] = any_incumbent and not winner["is_incumbent"]

        results.extend(finalists)

    generic_ballot = get_generic_ballot(year)
    return results, generic_ballot, climate, approval_pct

# ---------------------------------------------------------------------------
# House control projection
# ---------------------------------------------------------------------------

def project_house_control(results, generic_ballot):
    """
    Rough House control projection.
    Polled races override; unpolled seats estimated from generic ballot swing
    vs. 2024 baseline (R 220, D 215).
    """
    BASELINE_R        = 220
    BASELINE_D        = 215
    GENERIC_2024_MARGIN = -2.0   # R+2 in 2024 (D-margin convention)

    d_generic = generic_ballot.get("D")
    r_generic = generic_ballot.get("R")

    if d_generic is not None and r_generic is not None:
        current_margin = d_generic - r_generic
        swing          = current_margin - GENERIC_2024_MARGIN
        seat_swing     = round(swing / 0.5)   # ~1 seat per 0.5pp national swing
    else:
        swing      = 0.0
        seat_swing = 0

    projected_r = BASELINE_R - seat_swing
    projected_d = BASELINE_D + seat_swing

    # Count polled race outcomes
    winners = [r for r in results if r["winner"]]
    polled_r = sum(1 for r in winners if r["party"] == "R")
    polled_d = sum(1 for r in winners if r["party"] in ("D", "I"))

    control = (
        "Republicans" if projected_r > 217 else
        "Democrats"   if projected_d > 217 else
        "Unclear"
    )

    return {
        "R":          projected_r,
        "D":          projected_d,
        "control":    control,
        "seat_swing": seat_swing,
        "generic_D":  d_generic,
        "generic_R":  r_generic,
        "polled_R":   polled_r,
        "polled_D":   polled_d,
        "polled_total": polled_r + polled_d,
    }

# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results, generic_ballot, climate, approval_pct = predict_house_races()
    control = project_house_control(results, generic_ballot)

    d_gen = generic_ballot["D"]
    r_gen = generic_ballot["R"]
    if d_gen and r_gen:
        print(f"Generic ballot:  D {d_gen}%  R {r_gen}%  (margin: {d_gen - r_gen:+.1f})")
    print(f"Climate score:   {climate:+.3f}")
    if approval_pct:
        print(f"Approval:        {approval_pct}%")
    print()

    current = None
    for r in sorted(results, key=lambda x: (x["state"], x["district"].zfill(3))):
        label = f"{r['state']}-{r['district']}"
        if label != current:
            current = label
            print(f"\n── {label} ──────────────")
        marker = "★" if r["winner"] else " "
        inc    = " [inc]" if r["is_incumbent"] else ""
        flip   = " ⚡FLIP" if r["winner"] and r["is_flip"] else ""
        print(f"  {marker} {r['party']}  {r['name']:<32}  poll: {r['poll_avg']}%  "
              f"lean: {r['lean_baseline']}%  adj: {r['adjustment']:+.1f}pp  "
              f"appr: {r['appr_adj']:+.2f}pp  → {r['projected']}%{inc}{flip}")

    print(f"\n{'─'*50}")
    print(f"  PROJECTED HOUSE CONTROL: {control['control']}")
    print(f"  R: {control['R']}  D: {control['D']}")
    print(f"  Generic ballot swing from 2024: {control['seat_swing']:+d} seats toward "
          f"{'D' if control['seat_swing'] > 0 else 'R'}")
    print(f"  Polled races called:  R {control['polled_R']}  D {control['polled_D']}")
    print(f"{'─'*50}")