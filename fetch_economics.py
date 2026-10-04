import os
import math
import requests
import pandas as pd
from datetime import date
from dotenv import load_dotenv
from init_db import get_connection
from load_pollster_ratings import RATINGS_PATH

load_dotenv()
API_KEY = os.getenv("FRED_API_KEY")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
YEAR = 2026
LAMBDA = 0.0462  # approval-poll recency decay (half-life ~15 days). NOT the same as
               # senate_model.LAMBDA (0.0154, ~45 days) — the comment here claimed a
               # parity that has not held since senate_model was retuned. Whether the
               # two SHOULD match is an open modeling question rather than a typo:
               # changing this value moves the approval score and every projection
               # that reads it, so it stays where it was measured until backtested.

# Series ID → factor name in our DB
# Each chosen for documented correlation with midterm incumbent party performance
INDICATORS = {
    "UNRATE":          "UNEMPLOYMENT",        # Unemployment rate — high = bad for incumbent
    "CPIAUCSL":        "CPI_YOY",             # Consumer prices — high inflation = bad for incumbent
    "UMCSENT":         "CONSUMER_SENTIMENT",  # U Michigan sentiment — low = bad for incumbent
    "DSPIC96":         "REAL_DISPOSABLE_INC", # Real disposable income — falling = bad for incumbent
    "A191RL1Q225SBEA": "GDP_GROWTH",          # Real GDP growth — slow = bad for incumbent
    "FEDFUNDS":        "FED_FUNDS_RATE",      # Fed funds rate — high = bad for incumbent
}

# ---------------------------------------------------------------------------
# Display metadata for interpretation column
# Mirrors INDICATOR_RANGES / INDICATOR_DIRECTION in senate_model.py but kept here
# to avoid a circular import — this file runs standalone before senate_model.py.
#
# Each entry: (low, high, direction, unit_fmt)
#   low/high   — historical range endpoints (same as senate_model.py)
#   direction  — +1 means higher value = worse economy = helps D
#                -1 means higher value = better economy = helps R
#   unit_fmt   — a callable that formats the raw value for display
# ---------------------------------------------------------------------------
FACTOR_META = {
    # Ranges must stay in sync with INDICATOR_RANGES in senate_model.py.
    # If you change a range there, change it here too (and vice versa).
    #
    # direction: +1 = higher value is worse for R incumbents (e.g. unemployment)
    #            -1 = higher value is better for R incumbents (e.g. approval, sentiment)
    "UNEMPLOYMENT":       (3.5,   7.0,   +1, lambda v: f"{v:.1f}%"),
    "CPI_YOY":            (0.0,   5.0,   +1, lambda v: f"{v:.1f}% YoY"),
    "CONSUMER_SENTIMENT": (40.0,  90.0,  -1, lambda v: f"{v:.1f}"),
    "REAL_DISPOSABLE_INC":(16000, 21000, -1, lambda v: f"${v:,.0f}B"),
    "GDP_GROWTH":         (-2.0,   4.0,  -1, lambda v: f"{v:.1f}%"),
    "FED_FUNDS_RATE":     (0.0,   5.5,   +1, lambda v: f"{v:.2f}%"),
    # Display-only: mirrors APPROVAL_RANGE / APPROVAL_DIRECTION in senate_model.py, where
    # approval is its own signal (get_approval_score) rather than part of the climate block.
    "PRES_APPROVAL":      (25.0,  69.0,  -1, lambda v: f"{v:.1f}%"),  # higher approval = good for R
}

# Thresholds for how far the normalized score deviates from the neutral midpoint (0.5).
# |deviation| < 0.10  → "neutral"
# |deviation| < 0.25  → "mild"
# |deviation| < 0.45  → "moderate"
# else                → "strong"
_STRENGTH_LABELS = [(0.10, "neutral"), (0.25, "mild"), (0.45, "moderate"), (1.01, "strong")]

# Thresholds for the cumulative line, on |D−R margin shift| in points.
# Max possible shift is ±(ECON_WEIGHT + APPROVAL_WEIGHT) * 10 * 2 = ±9 pts today.
_MARGIN_STRENGTH_LABELS = [(1.0, "neutral"), (3.0, "mild"), (5.0, "moderate"), (float("inf"), "strong")]

def interpret_factor(factor_name, value):
    """
    Interprets the impact of a given factor on political incumbents based on its value and metadata.

    This function normalizes a provided factor value, calculates its deviation from a neutral midpoint,
    and determines the resulting impact strength and direction. It assesses how the factor might favor or
    hinder Republican incumbents using predefined metadata and thresholds.

    Parameters:
        factor_name: str
            The name of the factor to evaluate. Must exist in the FACTOR_META mapping.
        value: float
            The numeric value of the factor to be interpreted.

    Returns:
        str
            A descriptive string summarizing the factor's value, the deviation it represents, and
            the resulting tilt/impact for Republican incumbents. Returns an empty string if the provided
            factor_name is not found in FACTOR_META.

    Raises:
        KeyError: Raised if the provided factor_name is not found in FACTOR_META mapping.

    """
    if factor_name not in FACTOR_META:
        return ""

    low, high, direction, fmt = FACTOR_META[factor_name]

    # Normalize to [0, 1], clamped — same math as senate_model.py get_climate_score()
    normalized = max(0.0, min(1.0, (value - low) / (high - low)))

    # Deviation from neutral midpoint; direction flips sign meaning
    deviation = (normalized - 0.5) * direction  # positive → hurts R incumbent

    # Strength label
    abs_dev = abs(deviation)
    strength = next(label for threshold, label in _STRENGTH_LABELS if abs_dev < threshold)

    # Who benefits
    if strength == "neutral":
        beneficiary = "no meaningful tilt"
    elif deviation > 0:
        beneficiary = f"{strength} headwind for R / tailwind for D"
    else:
        beneficiary = f"{strength} tailwind for R / headwind for D"

    return f"{fmt(value)} → {beneficiary}"


def interpret_cumulative(year=YEAR):
    """
    Summarizes the combined effect of every stored climate factor plus presidential
    approval, using the model's own math rather than a re-derivation here.

    Imported lazily from senate_model (which only depends on init_db, so there is no
    cycle) so the summary always matches the adjustment the model will actually apply:
    get_climate_score()/get_approval_score() read the values just stored, and
    climate_adjustment()/approval_adjustment() convert them to vote-share points.

    Returns:
        str: one line giving the combined tilt, the per-candidate vote-share shift for
        each block, and the implied D−R margin shift.
    """
    from senate_model import (
        get_climate_score, get_approval_score,
        climate_adjustment, approval_adjustment,
    )

    climate_adj  = climate_adjustment("D", get_climate_score(year))
    approval_adj = approval_adjustment("D", get_approval_score(year))
    total_adj    = climate_adj + approval_adj  # D gains this, R loses it
    margin_shift = 2 * total_adj

    # Labeled by margin shift, not interpret_factor()'s normalized deviation:
    # averaging mixed factors shrinks the combined score, so a shift worth
    # over a point of margin could otherwise read as "neutral".
    strength = next(label for threshold, label in _MARGIN_STRENGTH_LABELS
                    if abs(margin_shift) < threshold)

    if strength == "neutral":
        beneficiary = "no meaningful tilt"
    elif margin_shift > 0:
        beneficiary = f"{strength} headwind for R / tailwind for D"
    else:
        beneficiary = f"{strength} tailwind for R / headwind for D"

    return (
        f"{beneficiary} — D vote share {total_adj:+.2f} pts "
        f"(climate {climate_adj:+.2f}, approval {approval_adj:+.2f}), "
        f"R {-total_adj:+.2f} pts, D−R margin {margin_shift:+.2f} pts"
    )


# ---------------------------------------------------------------------------
# FRED helpers
# ---------------------------------------------------------------------------

def fetch_latest(series_id):
    """
    Fetches the latest observation data from the FRED API for a given series.

    This function retrieves data from the FRED API based on the provided series
    ID. It fetches the most recent observations, ensures the response is HTTP
    compliant, and removes any entries with missing or invalid values.

    Arguments:
        series_id (str): The ID of the series to fetch data for.

    Returns:
        list[dict]: A list of observation dictionaries, each containing data for
        valid entries. Invalid entries with a value of "." are excluded.

    Raises:
        HTTPError: If the HTTP request to the FRED API fails.

    Notes:
        The function uses parameters to define the number of observations fetched,
        the data format, and the sorting order to ensure consistency.
    """
    params = {
        "series_id":  series_id,
        "api_key":    API_KEY,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      24,  # fetch 24 months to guarantee year-long is available
    }
    r = requests.get(FRED_URL, params=params)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    return [o for o in obs if o["value"] != "."]

def compute_value(series_id, obs):
    """
    Computes a value based on the provided series identifier and observations. The computation adjusts
    based on the series type, calculating percentage changes for specific series or returning the latest
    value as appropriate.

    Parameters:
        series_id (str): The identifier for the data series.
        obs (list[dict]): A list of observations where each observation is a dictionary containing 'value' (str)
            and 'date' (formatted as 'YYYY-MM-DD').

    Returns:
        float or None: The computed value as a percentage change or the latest observation value rounded
        to two decimal places, or None if the input `obs` is empty or specific conditions are not met.

    Raises:
        None
    """
    if not obs:
        return None

    latest = float(obs[0]["value"])
    latest_date = obs[0]["date"]  # e.g. "2026-05-01"

    if series_id == "CPIAUCSL":
        # Find the observation closest to exactly 12 months ago
        # by matching the month rather than blindly using index 11
        latest_year  = int(latest_date[:4])
        latest_month = int(latest_date[5:7])
        target_year  = latest_year - 1
        target_month = latest_month

        year_ago_obs = None
        for o in obs:
            y = int(o["date"][:4])
            m = int(o["date"][5:7])
            if y == target_year and m == target_month:
                year_ago_obs = o
                break

        if year_ago_obs is None:
            print(f"  WARNING: no exact year-ago match for CPI, skipping")
            return None

        year_ago = float(year_ago_obs["value"])
        return round((latest - year_ago) / year_ago * 100, 2)

    return round(latest, 2)

# ---------------------------------------------------------------------------
# Presidential approval from NYT CSV
# ---------------------------------------------------------------------------

def load_grade_lookup(path=RATINGS_PATH):
    """
    Builds a {pollster name: numeric grade} dict from pollster_ratings.csv.

    Names are stripped of stray whitespace because the ratings CSV has a few
    entries with leading spaces (e.g. " Selzer"), which would never match.
    Rows with a blank or non-numeric grade are left out, so a lookup on them
    falls through to the caller's default instead of returning NaN.
    """
    ratings = pd.read_csv(path)
    names   = ratings["Pollster"].astype(str).str.strip()
    grades  = pd.to_numeric(ratings["numeric_grade"], errors="coerce")  # bad values -> NaN
    return {name: grade for name, grade in zip(names, grades) if pd.notna(grade)}

def fetch_approval_rating(filepath, year=2026):
    """
    Fetches and computes a weighted approval rating for Donald Trump based on poll data.

    This function processes a CSV file containing poll data to compute a weighted
    approval rating for Donald Trump. The computation takes into account the population
    type, poll credibility, recency of the poll, and approval percentage. The result is
    stored in a database associated with a given year and factor name.

    Parameters:
        filepath (str): The path to the CSV file containing the poll data.
        year (int): The year for which the computed approval rating should be stored
            in the database. Defaults to 2026.

    Raises:
        ValueError: Raised if the CSV parsing or data processing encounters invalid
            values in required columns.

    Returns:
        None
    """
    df = pd.read_csv(filepath)

    # Guard: the approval CSV and the horse-race CSV share most columns, so a
    # wrong download otherwise surfaces as a cryptic KeyError below
    required = {"politician", "yes", "population", "poll_id", "end_date", "pollster"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"{filepath} is missing approval columns {sorted(missing)} — "
            "did you download the horse-race polls file instead of the "
            "presidential approval file?"
        )

    # Step 1 — filter to Trump only, real population groups
    df = df[
        (df["politician"] == "Donald Trump") &
        (df["population"].isin(["lv", "rv", "a"]))
        ].copy()

    # Prefer lv > rv > a — keep best population per poll
    pop_priority = {"lv": 0, "rv": 1, "a": 2}
    df["pop_rank"] = df["population"].map(pop_priority)
    df = df.sort_values("pop_rank")
    df = df.drop_duplicates(subset=["poll_id"], keep="first")

    # Step 2 — parse dates
    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df = df.dropna(subset=["end_date"])

    # Step 3 — compute weight per row
    today = date.today()
    numerator   = 0.0
    denominator = 0.0

    # Grades come from pollster_ratings.csv, NOT president.csv — the NYT file's
    # numeric_grade column is blank for every row, which silently made every
    # poll credibility 1.0. Built once here, outside the loop.
    grade_lookup = load_grade_lookup()
    unrated = set()  # pollsters that fell back to the default; set = no duplicates

    for _, row in df.iterrows():
        # Credibility — unknown pollsters default to 1.0 ("unknown = unproven"),
        # matching the default in load_pollster_ratings.py
        pollster_name = str(row["pollster"]).strip()
        credibility = grade_lookup.get(pollster_name)
        if credibility is None:
            credibility = 1.0
            unrated.add(pollster_name)

        # Recency
        days = (today - row["end_date"].date()).days
        recency = math.exp(-LAMBDA * days)

        # Approval % (the "yes" column)
        try:
            approval = float(row["yes"])
        except (ValueError, TypeError):
            continue

        weight       = credibility * recency
        numerator   += approval * weight
        denominator += weight

    if denominator == 0:
        print("  SKIP PRES_APPROVAL — no valid rows")
        return

    # Loud, not silent: list every pollster that got the default grade
    if unrated:
        print(f"  NOTE PRES_APPROVAL — {len(unrated)} pollsters unrated, defaulted to 1.0:")
        for name in sorted(unrated):
            print(f"    - {name}")

    weighted_approval = round(numerator / denominator, 2)
    interp = interpret_factor("PRES_APPROVAL", weighted_approval)
    print(f"  {'PRES_APPROVAL':<25} {weighted_approval:<12}  {interp}")

    # Step 5 — store in DB
    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO climate_factors (year, factor_name, value)
        VALUES (?, ?, ?)
        ON CONFLICT(year, factor_name) DO UPDATE SET value = excluded.value
    """, (year, "PRES_APPROVAL", weighted_approval))
    con.commit()
    con.close()

# ---------------------------------------------------------------------------
# Generic congressional ballot from the NYT House CSV
# ---------------------------------------------------------------------------

GENERIC_LAMBDA = 0.0198  # matches senate_model.LAMBDA (~35-day half-life). The
                         # generic ballot moves on the timescale of race polls,
                         # not the ~15-day approval decay above.

def fetch_generic_ballot(filepath, year=2026):
    """
    Weighted national generic-ballot margin (D minus R, in points), stored as
    the GENERIC_BALLOT_D climate factor. house_model.national_environment_margin()
    prefers this over the approval regression, and senate_model uses it to shift
    the baseline of unpolled and thinly polled races.

    Source rows are house.csv's national questions (state == 'US'). senate.csv
    carries no generic-ballot questions, so both chambers read the same number.

    Per poll: one population (lv > rv > a), and the questions within it averaged
    — some pollsters ask with and without leaners. Per pollster: only the most
    recent poll counts, so weekly trackers (YouGov, Morning Consult) get one
    vote like everyone else instead of a dozen. The remaining polls are weighted
    by pollster grade x recency, the same scheme as fetch_approval_rating.
    """
    df = pd.read_csv(filepath)
    df = df[
        (df["state"] == "US") &
        (df["cycle"] == year) &
        (df["stage"] == "general") &
        (df["party"].isin(["DEM", "REP"])) &
        (df["population"].isin(["lv", "rv", "a"]))
    ].copy()
    if df.empty:
        print("  SKIP GENERIC_BALLOT_D — no national generic-ballot rows")
        return

    df["end_date"] = pd.to_datetime(df["end_date"], format="mixed", errors="coerce")
    df["pct"] = pd.to_numeric(df["pct"], errors="coerce")
    df = df.dropna(subset=["end_date", "pct"])

    # One D-minus-R margin per question; a question missing either side is dropped
    q = df.pivot_table(index=["poll_id", "question_id", "pollster", "population", "end_date"],
                       columns="party", values="pct", aggfunc="sum").reset_index()
    q = q.dropna(subset=["DEM", "REP"])
    q["margin"] = q["DEM"] - q["REP"]

    # Best population per poll, then average that population's questions
    q["pop_rank"] = q["population"].map({"lv": 0, "rv": 1, "a": 2})
    q = q[q["pop_rank"] == q.groupby("poll_id")["pop_rank"].transform("min")]
    polls = q.groupby(["poll_id", "pollster", "end_date"], as_index=False)["margin"].mean()

    # Latest poll per pollster
    polls = polls.sort_values("end_date").drop_duplicates(subset=["pollster"], keep="last")

    grade_lookup = load_grade_lookup()
    today = date.today()
    numerator = denominator = 0.0
    for _, row in polls.iterrows():
        credibility = grade_lookup.get(str(row["pollster"]).strip(), 1.0)
        days = (today - row["end_date"].date()).days
        weight = credibility * math.exp(-GENERIC_LAMBDA * days)
        numerator   += row["margin"] * weight
        denominator += weight

    if denominator == 0:
        print("  SKIP GENERIC_BALLOT_D — no valid rows")
        return

    margin = round(numerator / denominator, 2)
    print(f"  {'GENERIC_BALLOT_D':<25} {margin:<12}  "
          f"D{margin:+.1f} margin from {len(polls)} pollsters' latest polls")

    con = get_connection()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO climate_factors (year, factor_name, value)
        VALUES (?, ?, ?)
        ON CONFLICT(year, factor_name) DO UPDATE SET value = excluded.value
    """, (year, "GENERIC_BALLOT_D", margin))
    con.commit()
    con.close()

# ---------------------------------------------------------------------------
# FRED fetch + store
# ---------------------------------------------------------------------------

def fetch_and_store_all():
    """
    Fetches data for multiple indicators, computes values, and stores the results in a database.

    This function retrieves the latest data points for various climate indicators, processes the data
    to compute specific values, and stores the results in a database using an upsert strategy. In
    addition, it attempts to fetch and process the presidential approval rating from a local CSV file,
    if available.

    Raises:
        Exception: Raised during data fetching, computation, or database interaction for any indicator
        in case of an error.

    """
    con = get_connection()
    cur = con.cursor()

    for series_id, factor_name in INDICATORS.items():
        try:
            obs = fetch_latest(series_id)
            value = compute_value(series_id, obs)
            if value is None:
                print(f"  SKIP {factor_name} — no data returned")
                continue

            cur.execute("""
                INSERT INTO climate_factors (year, factor_name, value)
                VALUES (?, ?, ?)
                ON CONFLICT(year, factor_name) DO UPDATE SET value = excluded.value
            """, (YEAR, factor_name, value))

            interp = interpret_factor(factor_name, value)
            print(f"  {factor_name:<25} {value:<12}  {interp}")

        except Exception as e:
            print(f"  ERROR {factor_name}: {e}")

    con.commit()
    con.close()

    # Presidential approval from NYT CSV
    approval_path = os.path.join(os.path.dirname(__file__), "data", "president.csv")
    if os.path.exists(approval_path):
        fetch_approval_rating(approval_path, year=YEAR)
    else:
        print("  SKIP PRES_APPROVAL — file not found")

    # Generic congressional ballot from the NYT House CSV
    house_path = os.path.join(os.path.dirname(__file__), "data", "house.csv")
    if os.path.exists(house_path):
        fetch_generic_ballot(house_path, year=YEAR)
    else:
        print("  SKIP GENERIC_BALLOT_D — file not found")

    print(f"\n  {'CUMULATIVE':<25} {'':<12}  {interpret_cumulative(YEAR)}")

    print("\nAll climate indicators stored.")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Fetching climate indicators for {YEAR}...\n")
    fetch_and_store_all()