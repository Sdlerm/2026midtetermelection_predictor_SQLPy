import os
import math
import requests
import pandas as pd
from datetime import date
from dotenv import load_dotenv
from init_db import get_connection

load_dotenv()
API_KEY = os.getenv("FRED_API_KEY")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
YEAR = 2026
LAMBDA = 0.03  # same recency decay as model.py

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
# FRED helpers
# ---------------------------------------------------------------------------

def fetch_latest(series_id):
    """Fetch the most recent observation for a FRED series."""
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

def fetch_approval_rating(filepath, year=2026):
    """
    Computes a credibility × recency weighted average of Trump approval
    from the NYT presidential approval polls CSV and stores it in climate_factors.
    """
    df = pd.read_csv(filepath)

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

    for _, row in df.iterrows():
        # Credibility
        try:
            credibility = float(row["numeric_grade"]) if pd.notna(row["numeric_grade"]) else 1.0
        except (ValueError, TypeError):
            credibility = 1.0

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

    weighted_approval = round(numerator / denominator, 2)
    print(f"  {'PRES_APPROVAL':<25} {weighted_approval}")

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
# FRED fetch + store
# ---------------------------------------------------------------------------

def fetch_and_store_all():
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

            print(f"  {factor_name:<25} {value}")

        except Exception as e:
            print(f"  ERROR {factor_name}: {e}")

    con.commit()
    con.close()

    # Presidential approval from NYT CSV
    approval_path = os.path.join(os.path.dirname(__file__), "data", "president_approval_polls.csv")
    if os.path.exists(approval_path):
        fetch_approval_rating(approval_path, year=YEAR)
    else:
        print("  SKIP PRES_APPROVAL — file not found")

    print("\nAll climate indicators stored.")

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Fetching climate indicators for {YEAR}...\n")
    fetch_and_store_all()