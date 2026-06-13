import sqlite3
import math
import os
import csv
from datetime import date
from init_db import get_connection

LAMBDA = 0.03        # recency decay — half-life ~23 days
ECON_WEIGHT = 0.3    # how much economics nudges the poll average; tune this
NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "data", "nominees.csv")

# ---------------------------------------------------------------------------
# Historical ranges for normalization — based on postwar US data
# Each tuple: (low, high) where LOW = bad economy, HIGH = good economy
# We invert the score for indicators where high = bad for incumbent
# ---------------------------------------------------------------------------
INDICATOR_RANGES = {
    "UNEMPLOYMENT":       (3.5, 7.0),    # tightened — 10% is GFC territory, irrelevant now
    "CPI_YOY":            (0.0, 7.0),    # tightened — 9% was 2022 peak, unlikely to return
    "CONSUMER_SENTIMENT": (40.0, 90.0),  # widened floor — sentiment has been structurally lower post-pandemic
    "REAL_DISPOSABLE_INC":(15000, 21000),# tightened to realistic 2020s range
    "GDP_GROWTH":         (-2.0, 4.0),   # tightened — 5% growth isn't happening in this environment
    "FED_FUNDS_RATE":     (0.0, 6.0),    # tightened — peak was 5.5%, not 8%
    "PRES_APPROVAL":      (25.0, 69.0),  # keep as-is, historical range still valid
}

# Direction: +1 means "higher value = worse economy = helps D challenger"
#            -1 means "higher value = better economy = helps R incumbent"
INDICATOR_DIRECTION = {
    "UNEMPLOYMENT":        1,   # high unemployment hurts incumbent (R)
    "CPI_YOY":             1,   # high inflation hurts incumbent (R)
    "CONSUMER_SENTIMENT": -1,   # high sentiment helps incumbent (R)
    "REAL_DISPOSABLE_INC":-1,   # high income helps incumbent (R)
    "GDP_GROWTH":         -1,   # high growth helps incumbent (R)
    "FED_FUNDS_RATE":      1,   # high rates hurt incumbent (R)
    "PRES_APPROVAL":      -1,   # high approval helps incumbent (R) (direction -1 means that higher value is better for incumbent)
}

# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def days_ago(poll_date_str):
    poll_date = date.fromisoformat(poll_date_str)
    return (date.today() - poll_date).days

def recency_weight(poll_date_str):
    return math.exp(-LAMBDA * days_ago(poll_date_str))

def weighted_average(race_id, candidate_id):
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

    numerator   = 0.0
    denominator = 0.0
    for pct, poll_date, credibility in rows:
        w = credibility * recency_weight(poll_date)
        numerator   += pct * w
        denominator += w

    return round(numerator / denominator, 1) if denominator > 0 else None

# ---------------------------------------------------------------------------
# Economic climate score
# ---------------------------------------------------------------------------

def get_climate_score(year=2026):
    """
    Returns a single float in [-1, +1].
    Positive = economic environment favors Democrats.
    Negative = economic environment favors Republicans.
    """
    # Fetch the latest value for each indicator from the database, normalize it to [0, 1] based on historical ranges, and apply directionality
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT factor_name, value FROM climate_factors
        WHERE year = ?
    """, (year,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        return 0.0  # no data — no adjustment

    factors = {name: value for name, value in rows}
    scores = []

    # Normalize each indicator to [0, 1], apply direction, and weight them equally
    for indicator, direction in INDICATOR_DIRECTION.items():
        if indicator not in factors:
            continue

        # Get the raw value and the historical range
        value = factors[indicator]
        low, high = INDICATOR_RANGES[indicator]

        # Normalize to 0-1 within historical range, clamp to bounds
        normalized = (value - low) / (high - low)
        # This essentially says "if it's at the historical worst, score is 1; if it's at the historical best, score is 0"
        normalized = max(0.0, min(1.0, normalized))

        # Convert to -1 to +1, apply direction
        # normalized=0 means low end of range, normalized=1 means high end
        score = (normalized - 0.5) * 2 * direction
        scores.append(score)

    # if we have no valid indicators, return 0; otherwise average indicators that are available and round to 2
    # decimals; the rounding is just for cleaner display, it doesn't affect the math much; we want to avoid giving a false sense of precision, since these are all estimates
    if not scores:
        return 0.0

    return round(sum(scores) / len(scores), 3)


def climate_adjustment(party, climate_score):
    """
    Converts a climate score into a percentage point adjustment for a candidate.
    party: 'D' or 'R'
    climate_score: float in [-1, +1], positive = favors D
    """
    # Scale: a climate_score of 1.0 = full ECON_WEIGHT adjustment
    # e.g. ECON_WEIGHT=0.3 means max ±3 percentage points
    raw = climate_score * ECON_WEIGHT * 10

    if party == "D":
        return round(raw, 2)
    else:
        return round(-raw, 2)  # inverse for R

# ---------------------------------------------------------------------------
# Nominees
# ---------------------------------------------------------------------------

def load_nominees():
    nominees = {}
    with open(NOMINEES_PATH, newline="") as f:
        for row in csv.DictReader(f):
            state = row["state"].strip()
            party = row["party"].strip()
            name  = row["name"].strip()
            incumbent_party = row.get("incumbent_party", "").strip()
            if state and party and name:
                nominees[(state, party)] = {
                    "name": name,
                    "incumbent_party": incumbent_party
                }
    return nominees

# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_all_races(year=2026):
    nominees     = load_nominees()
    climate      = get_climate_score(year)

    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT id, state FROM races WHERE year = ? AND state != 'US'", (year,))
    races = cur.fetchall()
    con.close()

    results = []

    for race_id, state in races:
        finalists = []

        for party in ["D", "R"]:
            nominee_info = nominees.get((state, party))
            if not nominee_info:
                continue
            name = nominee_info["name"]

            con = get_connection()
            cur = con.cursor()
            cur.execute(
                "SELECT id FROM candidates WHERE race_id = ? AND name = ?",
                (race_id, name)
            )
            row = cur.fetchone()
            con.close()

            if not row:
                continue

            candidate_id = row[0]
            poll_avg = weighted_average(race_id, candidate_id)
            if poll_avg is None:
                continue

            adjustment = climate_adjustment(party, climate)
            projected = round(poll_avg + adjustment, 1)
            incumbent_party = nominee_info["incumbent_party"]
            is_incumbent = (party == incumbent_party)
            is_flip = False  # set after we know the winner

            finalists.append({
                "state": state,
                "name": name,
                "party": party,
                "poll_avg": poll_avg,
                "adjustment": adjustment,
                "projected": projected,
                "incumbent_party": incumbent_party,
                "is_incumbent": is_incumbent,
            })

        if not finalists:
            continue

        finalists.sort(key=lambda x: x["projected"], reverse=True)
        finalists[0]["winner"] = True
        for f in finalists[1:]:
            f["winner"] = False

        # Flip detection — winner's party differs from incumbent party
        winner = finalists[0]
        for f in finalists:
            f["is_flip"] = (
                    winner["party"] != f["incumbent_party"]
                    and f["incumbent_party"] != ""
                )

        results.extend(finalists)

    return results, climate

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def project_senate_control(predictions):
    """
    Projects final Senate seat counts and which party controls the chamber.

    Seats not up in 2026: 65 total (R holds 30, D holds 35)
    These are Class 1 and Class 3 senators not facing election.
    """
    # Seats not up for election in 2026 (Class 1 + Class 3)
    # R holds 23, D holds 42 of the 65 not up
    SAFE_R = 23
    SAFE_D = 42

    projected_r = SAFE_R
    projected_d = SAFE_D

    flips = []
    seen_states = set()

    for r in predictions:
        if not r.get("winner"):
            continue
        state = r["state"]
        if state in seen_states:
            continue
        seen_states.add(state)

        if r["party"] == "R":
            projected_r += 1
        else:
            projected_d += 1

        if r["is_flip"]:
            flips.append(r)

    total = projected_r + projected_d
    not_called = 100 - total  # states missing nominees etc.

    if projected_r > 50:
        control = "Republicans"
        tiebreaker = False
    elif projected_d > 50:
        control = "Democrats"
        tiebreaker = False
    elif projected_r == 50 and projected_d == 50:
        control = "Republicans"  # Vance tiebreaker
        tiebreaker = True
    else:
        control = "Unclear"
        tiebreaker = False

    return {
        "R": projected_r,
        "D": projected_d,
        "not_called": not_called,
        "control": control,
        "tiebreaker": tiebreaker,
        "flips": flips,
    }

if __name__ == "__main__":
    predictions, climate = predict_all_races()

    direction = "favors D" if climate > 0 else "favors R"
    print(f"Climate score: {climate:+.3f} ({direction})")
    print(f"Econ adjustment: ±{abs(climate * ECON_WEIGHT * 10):.1f}pp\n")

    current_state = None
    for r in predictions:
        if r["state"] != current_state:
            current_state = r["state"]
            print(f"\n── {current_state} ──────────────")
        marker  = "★" if r.get("winner") else " "
        inc     = " [incumbent]" if r["is_incumbent"] else ""
        flip    = " ⚡FLIP" if r.get("winner") and r["is_flip"] else ""
        adj     = f"{r['adjustment']:+.1f}pp"
        print(f"  {marker} {r['party']}  {r['name']:<32}  poll: {r['poll_avg']}%  adj: {adj}  → {r['projected']}%{inc}{flip}")

    # Senate control projection
    control = project_senate_control(predictions)
    print(f"\n{'─'*45}")
    print(f"  PROJECTED SENATE CONTROL: {control['control']}")
    if control['tiebreaker']:
        print(f"  (50-50 tie — Vance tiebreaker gives R control)")
    print(f"  R: {control['R']} seats  |  D: {control['D']} seats", end="")
    if control['not_called'] > 0:
        print(f"  |  not called: {control['not_called']}")
    else:
        print()
    if control['flips']:
        print(f"\n  Projected flips ({len(control['flips'])}):")
        for f in control['flips']:
            print(f"    ⚡ {f['state']}  {f['party']}  {f['name']}")
    print(f"{'─'*45}")