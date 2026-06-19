import sqlite3
import math
import os
import csv
from datetime import date
from init_db import get_connection

# Constants
APPROVAL_WEIGHT = 0.15 # how much approval nudges the poll average;
APPROVAL_LAMBDA = 0.0277 # half-life ~30 days t =
APPROVAL_MIN_N = 400
APPROVAL_CSV = os.path.join(os.path.dirname(__file__), "data", "president_approval_polls.csv")

LAMBDA = 0.0231      # recency decay — half-life ~30 days
ECON_WEIGHT = 0.3    # how much economics nudges the poll average; tune this
LEAN_ALPHA  = 0.8    # polls vs. state lean blend (0=lean only, 1=polls only)

# Post-2024 election swing — Democratic overperformance vs. 2024 baseline,
# blended from special elections + 2025 gubernatorials, expressed in raw
# percentage points (positive = favors D). Updated by hand as new results land.
SWING_RAW           = 10.4   # current observed median D overperformance (pp)
HOUSE_SWING_WEIGHT  = 0.25   # House trusts 25% of raw swing (primary Tier-2 env signal)
SENATE_SWING_WEIGHT = 0.10   # Senate muted: national env already in climate+approval

RUNOFF_STATES = {"GA"}   # general election runoff required if no candidate clears 50%

NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "data", "senate_nominees.csv")

# ---------------------------------------------------------------------------
# Historical ranges for normalization — based on postwar US data
# Each tuple: (low, high) where LOW = bad economy, HIGH = good economy
# We invert the score for indicators where high = bad for incumbent
# ---------------------------------------------------------------------------
INDICATOR_RANGES = {
    "UNEMPLOYMENT":       (3.5,   7.0),    # tightened — 10% is GFC territory, irrelevant now
    "CPI_YOY":            (0.0,   5.0),    # 5% is modern crisis ceiling; 7% made 4%+ look neutral
    "CONSUMER_SENTIMENT": (40.0,  90.0),   # 40 floor captures post-pandemic low; 100 ceiling unrealistic (index never reaches it)
    "REAL_DISPOSABLE_INC":(16000, 21000),  # 15000 was pre-pandemic floor, 16000 more accurate for 2020s
    "GDP_GROWTH":         (-2.0,   4.0),   # ±5% is outside the realistic 2020s envelope
    "FED_FUNDS_RATE":     (0.0,   5.5),    # 5.5% was the actual cycle peak; 6% was never reached
    "PRES_APPROVAL":      (25.0,  69.0),   # full historical range valid (Nixon low, post-9/11 Bush high)
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
# State partisan lean — average of 2020 and 2024 presidential D margins
# Positive = D-leaning, negative = R-leaning
# ---------------------------------------------------------------------------
STATE_LEAN = {
    "AK": -11.5,  "AR": -29.0,  "FL":  -8.0,  "GA":  -1.0,
    "IA": -11.0,  "ID": -32.0,  "KS": -17.5,  "KY": -28.0,
    "MA": +31.0,  "ME":  +8.0,  "MI":  +1.5,  "MN":  +5.0,
    "MS": -18.5,  "MT": -18.5,  "NC":  -2.0,  "NE": -20.0,
    "NH":  +5.0,  "OH": -10.0,  "SC": -13.5,  "SD": -27.5,
    "TX": -10.0,  "VA":  +8.0,
}

# Independents who caucus with a major party for lean purposes
INDIE_CAUCUS_LEAN = {"I": "D"}   # Osborn caucuses with Dems

def lean_baseline(state, party):
    lean       = STATE_LEAN.get(state, 0.0)
    lean_party = INDIE_CAUCUS_LEAN.get(party, party)
    if lean_party == "D":
        return 50.0 + lean / 2
    else:
        return 50.0 - lean / 2

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



def get_approval_score():
    """
    Returns a weighted average presidential approval percentage.
    Filters by population quality: LV preferred, then RV, then Adults.
    Recency-decayed with a 25-day half-life.
    """
    import csv as _csv

    def approval_recency_weight(date_str):
        try:
            from datetime import datetime
            d = datetime.strptime(date_str.strip(), "%m/%d/%y").date()
            days = (date.today() - d).days
            return math.exp(-APPROVAL_LAMBDA * days)
        except (ValueError, TypeError):
            return 0.0

    rows = []

    with open(APPROVAL_CSV, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            pop  = row.get("population", "").strip().lower()
            try:
                yes  = float(row["yes"])
                n    = float(row["sample_size"])
                end  = row["end_date"].strip()
            except (ValueError, KeyError):
                continue
            if pop in ("lv", "rv", "a") and n > 0:
                rows.append({"pop": pop, "yes": yes, "n": n, "end": end})

    # Tier selection: LV first, add RV if insufficient, add A if still insufficient
    for tiers in (["lv"], ["lv", "rv"], ["lv", "rv", "a"]):
        subset = [r for r in rows if r["pop"] in tiers]
        total_n = sum(r["n"] for r in subset)
        if total_n >= APPROVAL_MIN_N:
            break

    if not subset:
        return None

    numerator   = 0.0
    denominator = 0.0
    for r in subset:
        w = r["n"] * approval_recency_weight(r["end"])
        numerator   += r["yes"] * w
        denominator += w

    return round(numerator / denominator, 2) if denominator > 0 else None

def approval_adjustment(party, approval_pct):
    """
    Converts presidential approval % into a per-candidate point adjustment.
    50% approval = neutral.
    Above 50% helps the incumbent party (R in 2026).
    Below 50% hurts the incumbent party (R in 2026), helps D challenger.
    """
    score = (approval_pct - 50.0) / 50.0   # normalize to [-1, +1]
    raw   = score * APPROVAL_WEIGHT * 10

    if party == "D":
        return round(-raw, 2)
    else:
        return round(raw, 2)

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

def swing_adjustment(party, swing_pp, weight):
    """
    Converts post-2024 election swing (in pp, D-positive) into a per-candidate
    adjustment. Mirrors climate_adjustment's party-sign convention: D gets the
    raw swing, all other parties get its inverse (independents treated as R,
    consistent with the existing climate/approval handling — flagged for the
    same future fix). Unlike climate_adjustment there is no ×10, because
    swing_pp is already in percentage points, not a [-1,+1] score.
    """
    raw = swing_pp * weight
    if party == "D":
        return round(raw, 2)
    else:
        return round(-raw, 2)

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
    nominees            = load_nominees()
    nominees_state_count = len({s for (s, _) in nominees})
    climate             = get_climate_score(year)
    approval_pct       = get_approval_score()

    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT id, state FROM races WHERE year = ? AND state != 'US'", (year,))
    races = cur.fetchall()
    con.close()

    results = []

    for race_id, state in races:
        finalists = []

        state_parties = [p for (s, p) in nominees if s == state]

        for party in state_parties:
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

            # AFTER:
            adjustment   = climate_adjustment(party, climate)
            appr_adj     = approval_adjustment(party, approval_pct) if approval_pct is not None else 0.0
            swing_adj    = swing_adjustment(party, SWING_RAW, SENATE_SWING_WEIGHT)
            baseline     = lean_baseline(state, party)
            blended      = LEAN_ALPHA * poll_avg + (1 - LEAN_ALPHA) * baseline
            projected    = round(blended + adjustment + appr_adj + swing_adj, 1)

            incumbent_party = nominee_info["incumbent_party"]
            is_incumbent = (party == incumbent_party) # this is a simplification; in reality we should check if the incumbent is actually running for re-election, but we'll assume that if the incumbent's party is listed, then the nominee from that party is the incumbent for modeling purposes
            is_flip = False  # set after we know the winner

            finalists.append({
                "state": state,
                "name": name,
                "party": party,
                "poll_avg": poll_avg,
                "lean_baseline": round(baseline, 1),
                "adjustment": adjustment,
                "appr_adj": appr_adj,
                "swing_adj": swing_adj,
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
        winner["is_flip"] = (
                winner["party"] != winner["incumbent_party"]
                and winner["incumbent_party"] != ""
        )
        for f in finalists[1:]:
            f["is_flip"] = False

        # Runoff detection — winner leads but hasn't cleared 50% in a runoff state
        runoff_likely = state in RUNOFF_STATES and winner["projected"] < 50.0
        for f in finalists:
            f["runoff_likely"] = runoff_likely

        results.extend(finalists)

    return results, climate, nominees_state_count

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def project_senate_control(predictions, nominees_state_count):
    """
    Projects final Senate seat counts and which party controls the chamber.

    Seats not up in 2026: 65 total (R holds 30, D holds 35)
    These are Class 1 and Class 3 senators not facing election.
    """
    # Seats not up for election in 2026 (Class 1 + Class 3)
    # R holds 23, D holds 42 of the 65 not up
    SAFE_R = 23
    SAFE_D = 42
    SEATS_UP_2026 = 35  # Class 2 seats; used for seats_remaining (fills seat chart to 100)

    # Independents who are expected to caucus with a major party if elected.
    # Osborn (NE-I) has stated he would caucus with Democrats.
    INDIE_CAUCUS = {"I": "D"}

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

        effective_party = INDIE_CAUCUS.get(r["party"], r["party"])
        if effective_party == "R":
            projected_r += 1
        else:
            projected_d += 1

        if r["is_flip"]:
            flips.append(r)

    # Unpolled/untracked Class 2 seats (outside senate_nominees.csv or lacking polls) are
    # assumed Republican holds — safe red seats are the ones that go unpolled.
    projected_r    += SEATS_UP_2026 - len(seen_states)
    not_called      = 0
    seats_remaining = 0

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
        "seats_remaining": seats_remaining,
        "control": control,
        "tiebreaker": tiebreaker,
        "flips": flips,
    }

if __name__ == "__main__":
    predictions, climate, nominees_count = predict_all_races()

    approval_pct = get_approval_score()

    direction = "favors D" if climate > 0 else "favors R"
    print(f"Climate score:   {climate:+.3f} ({direction})")
    print(f"Econ adjustment: ±{abs(climate * ECON_WEIGHT * 10):.1f}pp")
    if approval_pct is not None:
        appr_pp = abs((approval_pct - 50) / 50 * APPROVAL_WEIGHT * 10)
        print(f"Approval score:  {approval_pct}%  (adjustment: ±{appr_pp:.2f}pp)")
    else:
        print("Approval score:  unavailable")
    print()
    print(f"Election swing:  +{SWING_RAW:.1f}pp raw  (Senate ×{SENATE_SWING_WEIGHT} = +{SWING_RAW*SENATE_SWING_WEIGHT:.2f}pp toward D)")

    current_state = None
    for r in predictions:
        if r["state"] != current_state:
            current_state = r["state"]
            print(f"\n── {current_state} ──────────────")
        marker  = "★" if r.get("winner") else " "
        inc     = " [incumbent]" if r["is_incumbent"] else ""
        flip    = " ⚡FLIP" if r.get("winner") and r["is_flip"] else ""
        runoff  = " 🔄RUNOFF?" if r.get("winner") and r.get("runoff_likely") else ""
        adj     = f"{r['adjustment']:+.1f}pp"
        appr    = f"appr: {r['appr_adj']:+.2f}pp"
        swing   = f"swing: {r['swing_adj']:+.2f}pp"
        base    = f"lean: {r['lean_baseline']}%"
        print(f"  {marker} {r['party']}  {r['name']:<32}  poll: {r['poll_avg']}%  {base}  adj: {adj}  {appr}  {swing}  → "
              f"{r['projected']}%{inc}{flip}{runoff}")

    # Senate control projection
    control = project_senate_control(predictions, nominees_count)
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