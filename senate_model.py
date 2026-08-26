import csv
import math
import os
from datetime import date

from init_db import get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LEAN_ALPHA = 0.78
# Poll weight in the blend; the structural lean gets the remaining 0.22.
# (The comment here read "= 0.2" until 2026-08-10 — stale from when the constant
# was 0.80, and "= 0.18" until 2026-08-25. house_model.LEAN_ALPHA_HOUSE is a
# separate, deliberately unmatched 0.80: see the note there for why it is not
# tracked to this one.)
#
# BACKTESTED 2026-08-10, re-measured 2026-08-25 against 2018 + 2020 (59 races)
# — run backtest_senate.py. Moved 0.82 -> 0.78 on 2026-08-25; the figures below
# are the re-run at 0.78, which the measurement endorses slightly more strongly
# than it did 0.82 (0.01 off the variance-optimal minimum, against 0.02).
# VERDICT: KEEP 0.78. Not because it won, but because the thing that beats it
# wins for the wrong reason:
#   * Minimizing raw RMSE prefers alpha 0.65 (RMSE 5.80 vs 5.98 at 0.78).
#   * But that gain is BIAS CANCELLATION, not accuracy. Over these two cycles
#     the poll leg is +3.9 D-biased (the 2020 polling miss) and the lean leg is
#     -3.8 R-biased (state_lean.csv is a ~2024-vintage file, 8.8 points too R
#     for 2018). Two unrelated errors with opposite signs happen to cancel near
#     alpha 0.5-0.65. Tuning the blend weight to exploit that is fitting noise.
#   * Remove each mix's mean error and ask which has the least SCATTER — the
#     question a blend weight actually answers — and the optimum is alpha 0.79,
#     plateau 0.70-0.88. 0.78 sits inside it, 0.01 off the minimum.
#   * The per-cycle optima disagree completely: 2018 wants 0.86, 2020 wants
#     0.28. There is no single alpha both cycles endorse, so the pooled 0.65 is
#     a compromise between two years, not a property of polling.
#   * Race-level bootstrap (n=2000) puts the raw optimum at 0.65, 90% interval
#     0.49-0.77, so P(optimum >= 0.78) = 4%. That interval is measured on the
#     raw-RMSE criterion this block rejects, and the lean leg's lookahead
#     pushes it down further; it is not evidence against 0.78.
# National bias is what SIGMA_NATIONAL_MARGIN and the econ/approval terms are
# for. Absorbing it into the blend weight would use the wrong knob and would
# have made 2018 worse (RMSE 3.81 -> 4.61) to make 2020 better.
LEAN_ALPHA_BACKTEST_NOTE = "2018+2020, n=59; variance-optimal 0.79 (0.70-0.88)"
LAMBDA = 0.0231      # recency decay — half-life ~30 days
ECON_WEIGHT = 0.26    # how much economics nudges the poll average; tune this
APPROVAL_WEIGHT = 0.1
# separate lever for presidential approval; PROVISIONAL — not
# yet backtested against historical_results, chosen as roughly
# half of ECON_WEIGHT as a placeholder, not a validated value
TOSSUP_THRESHOLD_PP = 1.2 #if the finalists shares are w/i 1.2pp, flag as "toss-up"

# Minimum plausible sum of the top two candidates' poll averages for the pair to
# be read as a two-way general-election matchup. Lives here rather than in
# house_model because both chambers enforce it through two_way_poll_sum_ok();
# house_model imports it. See that function for why it is a data-KIND check,
# not an undecided knob.
MIN_TWO_WAY_POLL_SUM = 60.0


NOMINEES_PATH = os.path.join(os.path.dirname(__file__), "data", "senate_nominees.csv")
STATE_LEAN_PATH = os.path.join(os.path.dirname(__file__), "data", "state_lean.csv")

# States with a 2026 Senate race: 33 regular Class 2 seats + FL/OH special elections
STATES_WITH_2026_RACES = [
    "AL", "AK", "AR", "CO", "DE", "FL", "GA", "IA", "ID", "IL",
    "KS", "KY", "LA", "MA", "ME", "MI", "MN", "MS", "MT", "NC",
    "NE", "NH", "NJ", "NM", "OH", "OK", "OR", "RI", "SC", "SD",
    "TN", "TX", "VA", "WV", "WY",
]

# Incumbent party for 2026 race states NOT tracked in senate_nominees.csv.
# Used as the hold assumption for unpolled races. Derived manually — update
# if a nominee row is later added for any of these states.
UNTRACKED_HOLDS = {
    "AL": "R", "CO": "D", "DE": "D", "IL": "D", "LA": "R",
    "NJ": "D", "NM": "D", "OK": "R", "OR": "D", "RI": "D",
    "TN": "R", "VA": "D", "WV": "R", "WY": "R",
}

# Independents who are expected to caucus with a major party if elected.
# Osborn (NE-I) has stated he would caucus with Democrats.
INDIE_CAUCUS = {"I": "D"}

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
    #"PRES_APPROVAL":      (25.0,  69.0),   # full historical range valid (Nixon low, post-9/11 Bush high)
}

APPROVAL_RANGE = (25.0,69.0)
APPROVAL_DIRECTION = -1 # high approval helps the R incumbent (mirrors direction convention above)

# Direction: +1 means "higher value = worse economy = helps D challenger"
#            -1 means "higher value = better economy = helps R incumbent"
INDICATOR_DIRECTION = {
    "UNEMPLOYMENT":        1,   # high unemployment hurts incumbent (R)
    "CPI_YOY":             1,   # high inflation hurts incumbent (R)
    "CONSUMER_SENTIMENT": -1,   # high sentiment helps incumbent (R)
    "REAL_DISPOSABLE_INC":-1,   # high income helps incumbent (R)
    "GDP_GROWTH":         -1,   # high growth helps incumbent (R)
    "FED_FUNDS_RATE":      1,   # high rates hurt incumbent (R)
    #"PRES_APPROVAL":      -1,   # high approval helps incumbent (R) (direction -1 means that higher value is better
    # for incumbent)
}

# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def days_ago(poll_date_str, as_of=None):
    """
    Days elapsed between a poll date and the reference date.

    Args:
        poll_date_str (str): The date in ISO format (YYYY-MM-DD).
        as_of (date | str | None): Reference date. None = today, which is the
            live-forecast behavior. A date makes the clock explicit, which is
            what a backtest needs: measuring a 2018 poll's staleness against
            2026 would put its recency weight at exp(-0.0231 * ~2900) ≈ 0, and
            EVERY historical race would silently collapse to structural lean.
            The backtest would then be measuring the lean model while looking
            like it was measuring the polling model.
    Returns:
        int: Days between poll_date and as_of. Negative if the poll postdates
            as_of — callers are expected to have filtered those out already;
            see recency_weight, which refuses to weight them.
    Raises:
        ValueError: If either date string is not valid ISO format.
    """
    poll_date = date.fromisoformat(poll_date_str)
    if as_of is None:
        as_of = date.today()
    elif isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    return (as_of - poll_date).days

def recency_weight(poll_date_str, as_of=None):
    """
    Computes a weight based on the recency of a given poll date.

    This function calculates a weight for a given poll date string using an
    exponential decay formula. The weight decreases as the poll date becomes
    further in the past. The decay rate is determined by a constant value, LAMBDA.

    Args:
        poll_date_str: A string representing the date of the poll in a
                       recognized format (e.g., "YYYY-MM-DD").
        as_of: Reference date for "now" (see days_ago). None = today.

    Returns:
        float: The computed weight, which lies between 0 and 1, based on how
               recent the poll date is. A poll dated AFTER as_of gets weight 0
               rather than a weight above 1 — in a backtest that poll is
               information from the future, and silently up-weighting it would
               be lookahead bias in its purest form.

    Raises:
        ValueError: If the provided poll_date_str is invalid or cannot be parsed.
    """
    elapsed = days_ago(poll_date_str, as_of)
    if elapsed < 0:
        return 0.0
    return math.exp(-LAMBDA * elapsed)

def race_has_non_f_polls(race_id):
    """
    True if this race has at least one poll from a non-F-graded pollster.
    Used for the per-race F exclusion rule: F polls are dropped from
    averages ONLY when better polling exists in the same race, and the
    check is race-level (not per-candidate) so both candidates' averages
    are always built from the same poll-set criteria.
    NULL grades (ungraded pollsters) count as non-F.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT EXISTS(
            SELECT 1 FROM polls p
            LEFT JOIN pollsters po ON p.pollster_id = po.id
            WHERE p.race_id = ? AND COALESCE(po.grade, '') != 'F'
        )
    """, (race_id,))
    result = bool(cur.fetchone()[0])
    con.close()
    return result


def matched_poll_blocks(race_id, candidate_ids, as_of=None):
    """
    The (pollster_id, poll_date) blocks in this race that poll EVERY one of
    candidate_ids. Same principle as race_has_non_f_polls: a rule that decides
    which polls count must be applied race-wide, so every finalist's average is
    built from the same poll set.

    Why this exists. weighted_average_and_stderr runs per candidate over that
    candidate's own rows, so without this the two averages need not come from
    the same question — and a "margin" between them is then not a margin at
    all. Senate ID is the live case: 3 of its 6 blocks poll Risch with no Roth
    counterpart, including the two most recent and therefore heaviest under the
    recency decay (2026-08-07 at 34, 2026-08-13 at 52). Averaging Risch over 6
    blocks and Roth over 3 compared a well-polled incumbent against a sparsely
    polled challenger and called the difference a lead.

    Scope is the FINALISTS, not every candidate with a row in the race. Primary
    fields live in the same race_id — TX has 8 polled candidates, ME 5 — so
    requiring all of them to appear in a block matches nothing anywhere and
    would collapse all 23 polled Senate races to lean-only.

    A block dated after as_of is excluded, not just zero-weighted, so a
    backtest cannot use a future poll to qualify a block it then averages over.

    Returns a set. Empty means no question ever tested this matchup head-to-
    head; callers get (None, None) per candidate and the race falls through to
    the lean-only path, which is the correct answer rather than a gap.
    """
    candidate_ids = set(candidate_ids)
    if not candidate_ids:
        return set()

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT pollster_id, poll_date, candidate_id FROM polls "
        "WHERE race_id = ? AND candidate_id IN (%s)"
        % ",".join("?" * len(candidate_ids)),
        (race_id, *candidate_ids),
    )
    rows = cur.fetchall()
    con.close()

    seen = {}
    for pollster_id, poll_date, candidate_id in rows:
        if as_of is not None and days_ago(poll_date, as_of) < 0:
            continue
        seen.setdefault((pollster_id, poll_date), set()).add(candidate_id)

    return {block for block, polled in seen.items() if polled >= candidate_ids}


def weighted_average_and_stderr(race_id: int, candidate_id: int, as_of=None,
                                blocks=None):
    """
    Compute the weighted average and standard error of polling percentages for
    a specific candidate in a race.

    This function retrieves all polls for the given race and candidate, applies
    weights based on pollster credibility and poll recency, then calculates the
    weighted mean percentage and its standard error. For a single poll, the
    standard error is derived from binomial sampling error; for multiple polls,
    it is computed from the weighted variance across polls.

    Parameters
    ----------
    race_id : int
        The unique identifier for the race
    candidate_id : int
        The unique identifier for the candidate
    as_of : date | str | None
        Reference date for the recency decay, and a hard cutoff: polls dated
        after it are excluded rather than down-weighted, so a backtest cannot
        see past its own horizon. None = today (live-forecast behavior).
    blocks : set[tuple] | None
        Restrict the average to these (pollster_id, poll_date) blocks. Callers
        pass matched_poll_blocks(...) so every finalist in a race is averaged
        over the same questions; see that function. None = no restriction,
        which is the right default for any caller averaging one candidate in
        isolation.

    Returns
    -------
    tuple of (float, float | None) or (None, None)
        The weighted average percentage (rounded to 2 decimals) and the
        standard error OF THAT AVERAGE (rounded to 2 decimals) — how uncertain
        the mean is, so it narrows as polling accumulates. Not the spread of
        the polls themselves; see the n_eff branch below.

        The stderr alone is None when a race rests on a single poll whose
        sample size was never recorded: unknown, which display code renders as
        no error bar rather than a zero-width one. Callers must therefore
        tolerate a None stderr alongside a real average.

        (None, None) if no valid polls are found or all weights are zero.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
            SELECT p.pct, p.poll_date, p.sample_size, COALESCE(po.credibility, 1.0),
                   po.grade, p.pollster_id
            FROM polls p
            LEFT JOIN pollsters po ON p.pollster_id = po.id
            WHERE p.race_id = ? AND p.candidate_id = ?
        """, (race_id, candidate_id))
    rows = cur.fetchall()
    con.close()

    if blocks is not None:
        # Drop rows from questions that did not test the full finalist field.
        # An empty result here is a real answer — this candidate was never
        # polled head-to-head — so it returns the same (None, None) as "no
        # polls at all" and the caller's lean-only path takes over.
        rows = [r for r in rows if (r[5], r[1]) in blocks]

    if not rows:
        return None, None

    # Per-race F exclusion: drop F-graded polls when the race has any
    # non-F polling. If only F polls exist, keep them (sole-source fallback).
    # BIAS LEDGER: the lone F pollster (Big Data Poll) leans R, so this
    # rule nudges affected races' averages slightly D-ward where it fires.
    if race_has_non_f_polls(race_id):
        rows = [good_pollster for good_pollster in rows if (good_pollster[4] or "") != "F"]  # grade is column index 4
        if not rows:
            # This candidate was ONLY polled by F pollsters while the race has
            # non-F polling — candidate drops out of the projection entirely.
            # Must return a TUPLE: callers unpack (poll_avg, poll_stderr).
            return None, None

    pcts = []
    weights = []
    sample_sizes = []
    for pct, poll_date, sample_size, cred, _grade, _pollster_id in rows:
        w = cred * recency_weight(poll_date, as_of)
        if w > 0:
            pcts.append(pct)
            weights.append(w)
            sample_sizes.append(sample_size)

    if not weights:
        return None, None

    w_sum = sum(weights)
    mean = sum(w * p for w, p in zip(weights, pcts)) / w_sum

    # Effective sample size (Kish): how many polls this average is really made
    # of, once the credibility x recency weights are accounted for. Three polls
    # where one carries most of the weight are worth ~1.2 polls, not 3.
    n_eff = (w_sum ** 2) / sum(w * w for w in weights)

    if n_eff <= 1:
        # One poll, or weight so concentrated on one poll that there is no
        # usable poll-to-poll spread. Fall back to the binomial sampling error
        # implied by the dominant poll's sample size.
        # key= on the weight alone: tied weights would otherwise fall through
        # to comparing sample sizes, which may be None.
        n = max(zip(weights, sample_sizes), key=lambda t: t[0])[1]
        if n:
            p = mean / 100
            stderr = math.sqrt(p * (1 - p) / n) * 100
        else:
            # Sample size not recorded, so there is nothing to estimate from.
            # None, not 0.0 — the uncertainty is UNKNOWN, and returning zero
            # would render as a zero-width error bar claiming false certainty.
            # Display code maps None to NaN and draws no bar; see charts.py.
            stderr = None
    else:
        # Standard error OF THE MEAN, not the spread of the polls. The weighted
        # variance below measures how much individual polls disagree; dividing
        # by the effective sample size converts that into how uncertain their
        # AVERAGE is, which is the quantity an error bar drawn on an average
        # represents. (Was sqrt(var) — the weighted SD — until 2026-08-19,
        # which made a race with 25 scattered polls draw a WIDER bar than one
        # with 2 tight ones, exactly backwards.)
        #
        # The n_eff - 1 denominator is the unbiased form: var * n_eff/(n_eff-1)
        # for the sample variance, then / n_eff for the mean, which cancels to
        # var / (n_eff - 1). It cuts well-polled races roughly threefold (TX
        # 2.94 -> 1.01) and WIDENS thin ones (KS 1.16 -> 3.34). Both directions
        # are the point: bar width now tracks how much polling actually exists.
        var = sum(w * (p - mean)**2 for w, p in zip(weights, pcts)) / w_sum
        stderr = math.sqrt(var / (n_eff - 1)) if var > 0 else 0.0

    return round(mean, 2), (None if stderr is None else round(stderr, 2))

# ---------------------------------------------------------------------------
# Economic climate score
# ---------------------------------------------------------------------------

def get_climate_score(year=2026):
    """
    Calculates a climate score based on the most recent data for various climate indicators
    in a specific year and their historical ranges.

    The climate score represents an aggregated and normalized value for multiple climate
    factors. Each factor is normalized to a range of [0, 1] using historical data, adjusted
    for its directionality, and combined to produce a single weighted score.

    Parameters:
        year (int, optional): The year for which the climate data should be fetched. Defaults to 2026.

    Returns:
        float: A composite climate score ranging from -1 to +1. A positive score indicates
        conditions moving toward historical improvement (using provided directionalities),
        while a negative score indicates worsening conditions.

    Raises:
        None
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

def get_approval_score(year=2026):
    """
    Fetches the latest PRES_APPROVAL value from climate_factors and normalizes it
    into a -1..+1 score, using the same normalize-then-direction method as
    get_climate_score() — but kept as its own separate signal rather than being
    averaged into the six-indicator climate block.

    Positive = favors D (i.e., low presidential approval, since Republicans hold
    the White House this cycle). Returns 0.0 if no approval data is stored yet,
    same "no adjustment" fallback behavior as get_climate_score().
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        SELECT value FROM climate_factors
        WHERE year = ? AND factor_name = 'PRES_APPROVAL'
    """, (year,))
    row = cur.fetchone()
    con.close()

    if row is None:
        return 0.0

    value = row[0]
    low, high = APPROVAL_RANGE
    normalized = max(0.0, min(1.0, (value - low) / (high - low)))
    return round((normalized - 0.5) * 2 * APPROVAL_DIRECTION, 3)

def national_env_party(party):
    """
    Resolves a ballot party label to the side of the national environment the
    candidate actually runs on. Independents who caucus with a major party are
    mapped through INDIE_CAUCUS — the same convention lean_baseline() uses, so
    the structural leg and the national-environment leg treat a candidate like
    Osborn (NE-I) as one and the same person.

    Returns 'D', 'R', or the original label if it maps to neither.
    """
    return INDIE_CAUCUS.get(party, party)


def climate_adjustment(party, climate_score):
    """
    Converts a climate score into a percentage point adjustment for a candidate.
    party: 'D' or 'R', or an independent label resolved through INDIE_CAUCUS
    climate_score: float in [-1, +1], positive = favors D
    """
    # Scale: a climate_score of 1.0 = full ECON_WEIGHT adjustment
    # e.g. ECON_WEIGHT=0.18 means max ±1.8 percentage points
    raw = climate_score * ECON_WEIGHT * 10

    party = national_env_party(party)
    if party == "D":
        return round(raw, 2)
    if party == "R":
        return round(-raw, 2)  # inverse for R
    return 0.0  # unaligned independents: no national-environment adjustment

def approval_adjustment(party, approval_score):
    """
    Converts an approval score into a percentage-point adjustment for a candidate.
    Mirrors climate_adjustment() exactly, but scaled by APPROVAL_WEIGHT instead of
    ECON_WEIGHT — this is what keeps approval's influence tunable independently of
    the six-indicator climate block.
    """
    raw = approval_score * APPROVAL_WEIGHT * 10
    party = national_env_party(party)
    if party == "D":
        return round(raw, 2)
    if party == "R":
        return round(-raw, 2)
    return 0.0  # unaligned independents: no adjustment (matches climate_adjustment)

def load_state_lean():
    """
    Loads and parses state lean data from a CSV file and returns it as a dictionary.

    This function reads a CSV file located at the path defined by STATE_LEAN_PATH.
    It processes the data to extract state abbreviations and their corresponding
    Democratic margins, converting them into a dictionary where the state serves
    as the key and the Democratic lean margin as the value.

    Raises:
    FileNotFoundError
        If the file specified by STATE_LEAN_PATH does not exist.
    csv.Error
        If there's an error parsing the CSV file.

    Returns:
    dict[str, float]
        A dictionary mapping state abbreviations (uppercase) to their corresponding
        Democratic margin values as floats.
    """
    lean = {}
    with open(STATE_LEAN_PATH, newline="") as f:
        for row in csv.DictReader(f):
            state = row["state"].strip().upper()
            margin = row["dem_margin"].strip()
            if state and margin:
                lean[state] = float(margin)
    return lean

def lean_baseline(state, party, state_lean):
    """
    Structural baseline as a vote-share %, derived from the stored signed margin.
    margin = dem_share - rep_share = dem_share - (100 - dem_share) = 2*dem_share - 100,
    so dem_share = margin/2 + 50
    """
    margin = state_lean.get(state)
    if margin is None:
        return 50.0 # Unknown state: neutral, no structural pull

    dem_share = 50.00 + margin/2.00
    rep_share = 50.00 - margin/2.00

    if party == "D":
        return dem_share
    if party == "R":
        return rep_share
    # Independent candidates: if they caucus with the democrats, receive dem_share; else they receive rep_share
    return dem_share if INDIE_CAUCUS.get(party) == "D" else rep_share


# ---------------------------------------------------------------------------
# Nominees
# ---------------------------------------------------------------------------

def load_nominees():
    """
    Loads nominees' data from a CSV file and organizes it into a dictionary.

    The function reads data from a predefined CSV file containing information about
    state nominees, including their state, party affiliation, name, and optionally
    the incumbent party. The data is cleaned and stored in a dictionary where the
    keys are tuples of state and party, and the values are dictionaries containing
    the nominee's name and the incumbent party (if provided).

    Returns:
        dict: A dictionary where keys are tuples (state: str, party: str) and
        values are dictionaries with keys:
            - "name" (str): Name of the nominee
            - "incumbent_party" (str): Incumbent party of the nominee or an empty string
    """
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

def _finalize_race(finalists):
    """
    Shared post-processing for one race's finalists: sort by projection,
    mark winner, detect flip, detect toss-up. Used by both the poll-based
    path and the lean-only fallback path so the two can never drift apart.
    Assumes len(finalists) >= 2.
    """
    finalists.sort(key=lambda x: x["projected"], reverse=True)
    finalists[0]["winner"] = True
    for f in finalists[1:]:
        f["winner"] = False

    winner = finalists[0]
    winner["is_flip"] = (
            winner["party"] != winner["incumbent_party"]
            and winner["incumbent_party"] != ""
    )
    for f in finalists[1:]:
        f["is_flip"] = False

    margin = finalists[0]["projected"] - finalists[1]["projected"]
    is_tossup = margin < TOSSUP_THRESHOLD_PP
    for f in finalists:
        f["is_tossup"] = is_tossup
        f["margin"] = round(margin, 2)
    return finalists


def two_way_poll_sum_ok(finalists, label):
    """
    True if the top two poll averages can plausibly be a two-way general
    election. Shared by both chambers so they can never drift apart.

    Each candidate's average is built from that candidate's own poll rows, so
    the two need not come from the same question block. When they don't, the
    pair is not a matchup. AK-01 is the live example: a 3.8% "Democratic
    nominee" (a primary-field share) against a Republican at 48.9%, sum 52.7.
    Blending that against a lean baseline — which sums to 100 by construction —
    mixes incomparable scales, and produced a projected R+37.6 in a district
    whose lean is R+14.6. The caller discards the polled projection and lets the
    lean-only pass rebuild the race. Same spirit as the never-polled-nominee
    rule: a gap is better than a bad number.

    The bar is deliberately low. Early polls legitimately leave 25-30%
    undecided (House IA-02 sums to 69, Senate SD to 68.7), and those are
    usable — the blend shrinks their margins a little, which is a known and
    modest bias. This catches data that is the wrong KIND, not data that is
    merely undecided-heavy.

    This runs AFTER matched_poll_blocks has already restricted every average to
    questions that tested the whole finalist field, and it still earns its
    keep: the two checks catch different things. Mismatched poll SETS are the
    block filter's job, and it is the precise instrument for them — the sum is
    only a proxy, and a loose one (ID had the defect while summing to 60.7,
    which no threshold rejects without also rejecting SD at 68.7, a legitimate
    undecided-heavy race). What survives block matching is a genuine head-to-
    head question whose SHARES are still not general-election shares: AK-01
    polls both nominees in the same question and still sums to 48.6, because
    what it is measuring is a primary field.
    """
    if len(finalists) < 2:
        return True                  # the len < 2 rule already discards these

    top_two = sorted(finalists, key=lambda f: f["poll_avg"], reverse=True)[:2]
    poll_sum = sum(f["poll_avg"] for f in top_two)
    if poll_sum < MIN_TWO_WAY_POLL_SUM:
        print(f"WARNING: {label} polled shares sum to {poll_sum:.1f} "
              f"(< {MIN_TWO_WAY_POLL_SUM:.0f}) — not a two-way general-election "
              f"average; falling back to lean-only for this race.")
        return False
    return True


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_all_races(year=2026):
    """
    Predict race outcomes for a given year based on polling data and climate-adjusted projections.

    This function analyzes electoral races for the specified year, evaluates nominees' standings
    through weighted polling averages, and applies climate-based adjustments. The method determines
    winners, identifies flips (when a party differs from the incumbent's), and returns detailed results
    with relevant projections. Only state-level races are considered, excluding 'US' as a state.

    Args:
        year (int, optional): The year for which races will be evaluated. Defaults to 2026.

    Returns:
        tuple: A tuple containing three elements:
            - list[dict]: A list of dictionaries, with each dictionary representing a race participant and
              their attributes, including final projections, incumbency status, and whether their party represents a flip.
            - float: The climate score used for adjustment purposes.
            - int: The count of unique states available in the nominee dataset.

    Raises:
        None directly raised by this function; exceptions may propagate from database operations or auxiliary utility functions.
    """
    nominees            = load_nominees()
    state_lean          = load_state_lean()
    nominees_state_count = len({s for (s, _) in nominees})
    climate             = get_climate_score(year)
    approval             = get_approval_score(year)


    con = get_connection()
    cur = con.cursor()
    cur.execute("SELECT id, state FROM races WHERE year = ? AND state != 'US' AND district = ''", (year,))
    races = cur.fetchall()
    con.close()

    results = []

    for race_id, state in races:
        finalists = []

        state_parties = [p for (s, p) in nominees if s == state]

        # Resolve every nominee to a candidate row FIRST. The matched-block set
        # is a property of the finalist field as a whole, so it cannot be
        # computed inside the per-candidate loop that consumes it.
        contenders = []
        con = get_connection()
        cur = con.cursor()
        for party in state_parties:
            nominee_info = nominees.get((state, party))
            if not nominee_info:
                continue
            cur.execute(
                "SELECT id FROM candidates WHERE race_id = ? AND name = ?",
                (race_id, nominee_info["name"])
            )
            row = cur.fetchone()
            if row:                      # never polled — skip (scripture rule)
                contenders.append((party, nominee_info, row[0]))
        con.close()

        blocks = matched_poll_blocks(race_id, [c[2] for c in contenders])

        for party, nominee_info, candidate_id in contenders:
            name = nominee_info["name"]

            poll_avg, poll_stderr = weighted_average_and_stderr(
                race_id, candidate_id, blocks=blocks)
            if poll_avg is None:
                continue

            adjustment = climate_adjustment(party, climate)
            approval_adj = approval_adjustment(party, approval)

            lean = lean_baseline(state, party, state_lean)
            blended = LEAN_ALPHA*poll_avg + (1-LEAN_ALPHA)*lean
            projected = round(blended + adjustment + approval_adj, 2)

            incumbent_party = nominee_info["incumbent_party"]
            is_incumbent = (party == incumbent_party) # this is a simplification; in reality we should check if the incumbent is actually running for re-election, but we'll assume that if the incumbent's party is listed, then the nominee from that party is the incumbent for modeling purposes
            is_flip = False  # set after we know the winner

            finalists.append({
                "state": state,
                "name": name,
                 "party": party,
                "poll_avg": poll_avg,
                "poll_stderr": poll_stderr,
                "has_polls": True,
                "lean": round(lean, 2),
                "blended": round(blended, 2),
                "adjustment": adjustment,
                "approval_adjustment": approval_adj,
                "projected": projected,
                "incumbent_party": incumbent_party,
                "is_incumbent": is_incumbent,
            })



        # A race is poll-based only if BOTH finalists have poll averages.
        # A lone polled candidate is a data gap, not a contest — discard and
        # let the lean-only fallback below rebuild the whole race coherently.
        # The same goes for a pair whose shares cannot be a two-way general
        # election; see two_way_poll_sum_ok.
        if len(finalists) < 2 or not two_way_poll_sum_ok(finalists, state):
            continue

        results.extend(_finalize_race(finalists))

        # ---- Lean-only fallback ----------------------------------------------
    # Any 2026 race state without a poll-based projection gets a structural
    # one: lean baseline + national adjustments, i.e. the standard blend with
    # the poll term unavailable (LEAN_ALPHA effectively 0). These rows carry
    # has_polls=False and poll_stderr=None so downstream display can flag
    # them honestly instead of dressing them up as polled projections.
    projected_states = {r["state"] for r in results}
    for state in STATES_WITH_2026_RACES:
        if state in projected_states:
            continue
        finalists = []
        for party in [p for (s, p) in nominees if s == state]:
            info = nominees[(state, party)]
            lean = lean_baseline(state, party, state_lean)
            projected = round(
                lean
                + climate_adjustment(party, climate)
                + approval_adjustment(party, approval), 2
            )
            finalists.append({
                "state": state,
                "name": info["name"],
                "party": party,
                "poll_avg": None,
                "poll_stderr": None,
                "has_polls": False,
                "lean": round(lean, 2),
                "blended": round(lean, 2),   # blend degenerates to pure lean
                "adjustment": climate_adjustment(party, climate),
                "approval_adjustment": approval_adjustment(party, approval),
                "projected": projected,
                "incumbent_party": info["incumbent_party"],
                "is_incumbent": (party == info["incumbent_party"]),
            })
        if len(finalists) < 2:
            print(f"WARNING: {state} has <2 nominees — no projection possible")
            continue
        results.extend(_finalize_race(finalists))

    return results, climate, nominees_state_count

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def project_senate_control(predictions):
    """
    Projects final Senate seat counts and which party controls the chamber.

    Seats not up in 2026: 65 total (R holds 31, D holds 34).
    These are Class 1 and Class 3 senators not facing election, minus the
    FL/OH Class 3 seats vacated into 2026 special elections.
    Derivation: current chamber is 53R–47D; of the 35 seats up, 22 are
    R-held and 13 D-held (per senate_nominees.csv incumbent_party +
    UNTRACKED_HOLDS), so not-up = 53−22 = 31 R and 47−13 = 34 D.
    """
    # Seats not up for election in 2026 — see derivation in docstring
    SAFE_R = 31
    SAFE_D = 34
    # Class 2 seats; used for seats_remaining (fills seat chart to 100)

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

    # Unpolled/untracked 2026 seats: assume the incumbent party holds.
    # Runs ONCE, after all predicted winners are counted — seen_states is
    # complete at this point. Untracked states are safe seats by definition,
    # but they are NOT all Republican — CO, DE, IL, NJ, NM, OR, RI are safe D.
    not_called = 0
    for untracked_state in STATES_WITH_2026_RACES:
        if untracked_state in seen_states:
            continue
        hold_party = UNTRACKED_HOLDS.get(untracked_state)
        if hold_party == "D":
            projected_d += 1
        elif hold_party == "R":
            projected_r += 1
        else:
            # Tracked in nominees CSV but produced no prediction (missing
            # polls) and not in UNTRACKED_HOLDS — leave it uncalled instead
            # of guessing, so the invariant below still holds and callers
            # (dashboard/charts/monte_carlo) don't crash on a data gap.
            print(f"WARNING: {untracked_state} unassigned — no prediction and not in UNTRACKED_HOLDS")
            not_called += 1

    # Invariant: every path through this function must account for exactly 100
    # seats (R + D + not_called). Any double-count, missed state, or
    # loop-nesting mistake dies loudly here instead of producing a quietly
    # wrong forecast — but an ordinary data gap (a tracked state losing all
    # its polls) degrades into "not called" rather than crashing.
    #
    # RAISES rather than asserts, for two reasons. An `assert` is stripped
    # under `python -O`, so the one build where this matters most is the one
    # where it silently returns a wrong seat total. And AssertionError is not
    # ValueError: dashboard.load_sim_or_error catches ValueError precisely so
    # one chamber's data gap cannot blank the other, and load_predictions()
    # calls this function at dashboard.py module top level — an AssertionError
    # there takes the whole page down instead of the Senate section.
    # Mirrors monte_carlo_senate.baseline_seats, promoted for the same reason.
    total = projected_r + projected_d + not_called
    if total != 100:
        raise ValueError(
            f"Senate seat accounting broken: R={projected_r} + D={projected_d} + "
            f"not_called={not_called} = {total}, expected 100 "
            f"(seen_states={len(seen_states)}).\n"
            f"Every seat must be counted exactly once — projected for a party or "
            f"left uncalled, never both and never neither.\n"
            f"Usual causes: a state counted under two parties by the "
            f"INDIE_CAUCUS convention; a STATES_WITH_2026_RACES entry with no "
            f"prediction and no UNTRACKED_HOLDS fallback; or SAFE_D/SAFE_R "
            f"drifting out of date against the seats not up this cycle."
        )

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


DIVIDER = "─" * 45


def format_control_summary(control):
    """
    Builds the printable Senate-control summary as a list of lines.

    Kept separate from project_senate_control() (pure calculation) and from
    the printing loop below (I/O) so the summary text can be unit-tested or
    reused (e.g. by dashboard.py) without capturing stdout, and so the
    trailing "seats" line is fully assembled before it's printed instead of
    relying on a fragile print(..., end="") / print() pairing.
    """
    lines = [
        f"\n{DIVIDER}",
        f"  PROJECTED SENATE CONTROL: {control['control']}",
    ]
    if control["tiebreaker"]:
        lines.append("  (50-50 tie — Vance tiebreaker gives R control)")

    seats_line = f"  R: {control['R']} seats  |  D: {control['D']} seats"
    if control["not_called"] > 0:
        seats_line += f"  |  not called: {control['not_called']}"
    lines.append(seats_line)

    if control["flips"]:
        lines.append(f"\n  Projected flips ({len(control['flips'])}):")
        lines.extend(
            f"    ⚡ {f['state']}  {f['party']}  {f['name']}"
            for f in control["flips"]
        )

    lines.append(DIVIDER)
    return lines

# After

if __name__ == "__main__":
    predictions, climate, nominees_count = predict_all_races()
    approval = get_approval_score()  # queried separately rather than added to
    # predict_all_races()'s return tuple, so
    # dashboard.py's unpacking stays untouched

    climate_direction  = "favors D" if climate > 0 else "favors R"
    approval_direction = "favors D" if approval > 0 else "favors R"

    print(f"Climate score: {climate:+.2f} ({climate_direction})")
    print(f"Climate adjustment: ±{abs(climate * ECON_WEIGHT * 10):.2f}pp")
    print(f"Approval score: {approval:+.2f} ({approval_direction})")
    print(f"Approval adjustment: ±{abs(approval * APPROVAL_WEIGHT * 10):.2f}pp\n")

    def print_section(rows, heading):
        """
        Print one basis section: races grouped by state, states alphabetical.

        Splitting the output by basis rather than interleaving means the reader
        never has to check each row's detail field to learn whether they're
        looking at a poll-backed projection or a structural guess — the section
        they're in already answers that.
        """
        print(f"\n{'═'*60}\n{heading}\n{'═'*60}")
        if not rows:
            print("  (none)")
            return

        current_state = None
        # Stable sort by state so each state's nominee rows stay grouped (and
        # keep their within-state order) while states print alphabetically.
        for r in sorted(rows, key=lambda p: p["state"]):
            if r["state"] != current_state:
                current_state = r["state"]
                print(f"\n── {current_state} ──────────────")
            marker  = "★" if r.get("winner") else " "
            inc     = " [incumbent]" if r["is_incumbent"] else ""
            flip    = " ⚡FLIP" if r.get("winner") and r["is_flip"] else ""
            tossup  = " 🪙TOSSUP" if r.get("winner") and r["is_tossup"] else ""
            econ_adj = f"{r['adjustment']:+.2f}pp"
            appr_adj = f"{r['approval_adjustment']:+.2f}pp"
            if r.get("has_polls", True):
                detail = f"poll: {r['poll_avg']}%  blend: {r['blended']}%"
            else:
                detail = f"lean-only: {r['lean']}%"
            print(f"  {marker} {r['party']}  {r['name']:<32}  {detail}  "
                  f"econ: {econ_adj}  appr: {appr_adj}  → {r['projected']}%{inc}{flip}{tossup}")

    polled    = [r for r in predictions if r.get("has_polls", True)]
    lean_only = [r for r in predictions if not r.get("has_polls", True)]

    print_section(polled,
                  f"POLLED RACES — {len({r['state'] for r in polled})} states")
    print_section(lean_only,
                  f"LEAN-ONLY RACES (no polling) — {len({r['state'] for r in lean_only})} states")

    # Senate control projection
    control = project_senate_control(predictions)
    print("\n".join(format_control_summary(control)))

