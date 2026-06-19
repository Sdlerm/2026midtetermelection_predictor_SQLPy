# 2026 US Election Predictor

A polling-based model for the 2026 midterm elections covering all 35 Senate seats and competitive House districts. Combines credibility-weighted, recency-decayed poll averages with a multi-indicator economic climate score and presidential approval ratings to project vote shares, seat counts, and chamber control.

---

## Goals

- Predict outcomes for 2026 Senate races (all 35 Class 2 seats) and polled House districts
- Project which party controls the Senate (51 seats) and House (218 seats) after November 2026
- Identify likely seat flips from current party control
- Surface all predictions in an interactive Streamlit dashboard with drill-down by state/district

---

## Data Flow

```
External sources
      │
      ├── FiveThirtyEight/NYT polling CSVs   (data/senate.csv, data/house.csv)
      ├── Manually maintained nominee lists  (data/senate_nominees.csv, data/house_nominees.csv)
      ├── Presidential approval polls CSV    (data/president_approval_polls.csv)
      └── FRED API (via fetch_economics.py)
             │
             ▼
      ┌─────────────┐
      │  init_db.py │  ← run once to create db/elections.db schema
      └──────┬──────┘
             │
      ┌──────┴─────────────────────────┐
      │                                │
      ▼                                ▼
senate_ingest.py               house_ingest.py
  • Wipes Senate rows             • Wipes House rows
  • Loads senate.csv polls        • Loads district polls (confirmed nominees only)
  • Loads climate factors         • Loads national generic ballot
      │                                │
      └──────────────┬─────────────────┘
                     │
                     ▼
             db/elections.db  (SQLite)
             ┌──────────────────────────┐
             │ pollsters                │
             │ races (Senate + House)   │
             │ candidates               │
             │ polls                    │
             │ climate_factors          │
             │ historical_results       │
             └──────────────────────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
  senate_model.py          house_model.py
  • Weighted poll avg       • Weighted poll avg
  • State lean baseline     • District lean baseline
  • Economic adjustment     • Economic adjustment (shared)
  • Approval adjustment     • Approval adjustment (shared)
  • Seat count projection   • Generic ballot seat projection
          │                     │
          └──────────┬──────────┘
                     │
                     ▼
               charts.py
        (Matplotlib figure builders)
                     │
                     ▼
              dashboard.py
          (Streamlit web app)
```

---

## File Structure

```
election_predictor/
├── data/
│   ├── senate.csv                   # Senate polling data (download manually — see below)
│   ├── house.csv                    # House polling data (download manually — see below)
│   ├── senate_nominees.csv          # Confirmed Senate general-election nominees
│   ├── house_nominees.csv           # Confirmed House general-election nominees
│   └── president_approval_polls.csv # Presidential approval polling data
├── db/
│   └── elections.db                 # SQLite database (auto-created by init_db.py)
├── init_db.py                       # Creates the database schema
├── senate_ingest.py                 # Loads Senate polls + climate factors into DB
├── house_ingest.py                  # Loads House polls + generic ballot into DB
├── fetch_economics.py               # Pulls FRED economic indicators into DB
├── senate_model.py                  # Senate prediction engine + seat projection
├── house_model.py                   # House prediction engine + seat projection
├── charts.py                        # Matplotlib figure builders (used by dashboard)
├── dashboard.py                     # Streamlit interactive dashboard
└── wiring_explained.md              # Narrative walkthrough of the model math
```

---

## Getting Started

### 1. Python environment

```bash
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

pip install pandas streamlit matplotlib requests python-dotenv
```

### 2. FRED API key

Create a `.env` file in the project root:

```
FRED_API_KEY=your_key_here
```

Get a free key at [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html).

### 3. Polling data

Download the polling CSVs from FiveThirtyEight/NYT and place them in `data/`:

- **Senate polls** → save as `data/senate.csv`
- **House polls** → save as `data/house.csv`

The CSVs use a FiveThirtyEight column schema (`poll_id`, `pollster`, `candidate_name`, `party`, `pct`, `end_date`, `population`, `stage`, `numeric_grade`, etc.).

### 4. Run the pipeline

Run these steps in order. After first-time setup, only steps 3–6 need to be repeated when refreshing predictions.

```bash
# 1. Initialize the database (first time only)
python init_db.py

# 2. Fetch economic indicators from FRED
python fetch_economics.py

# 3. Load Senate polls
python senate_ingest.py

# 4. Load House polls + generic ballot
python house_ingest.py

# 5. (Optional) Verify predictions in the terminal
python senate_model.py
python house_model.py

# 6. Launch the dashboard
streamlit run dashboard.py
```

The dashboard opens at `http://localhost:8501`.

---

## How the Model Works

### Poll weighting

Each poll is weighted by two factors multiplied together:

- **Pollster credibility** — derived from FiveThirtyEight's `numeric_grade` (0–3 scale). Ungraded pollsters default to 1.0.
- **Recency decay** — `exp(-λ × days_old)` where `λ ≈ 0.023`. Gives roughly a 30-day half-life; a 90-day-old poll carries ~12% of the weight of a poll from today.

Population tier preference: Likely Voters (LV) > Registered Voters (RV) > Adults. When a poll covers multiple populations, only the best tier is kept.

### State/district lean baseline

Each race has a structural baseline derived from the 2020 and 2024 average presidential margin in that state or congressional district. This baseline is blended 20/80 with the poll average (`LEAN_ALPHA = 0.8`), so polls dominate but unpolled fringe candidates don't distort results.

### Economic climate score

Seven indicators are pulled from the FRED API and normalized to historical ranges:

| Indicator | FRED Series | Higher value favors |
|---|---|---|
| Unemployment rate | UNRATE | Democrats |
| CPI year-over-year | CPIAUCSL | Democrats |
| Consumer sentiment | UMCSENT | Republicans |
| Real disposable income | DSPIC96 | Republicans |
| Real GDP growth | A191RL1Q225SBEA | Republicans |
| Fed funds rate | FEDFUNDS | Democrats |
| Presidential approval | (from approval CSV) | Republicans |

Each is normalized to [-1, +1] against its historical range and averaged into a single `climate_score`. A positive score means the economic environment favors Democrats.

### Presidential approval adjustment

Approval ratings are read from `data/president_approval_polls.csv`, recency-weighted with a ~25-day half-life. Approval above 50% helps the incumbent party (Republicans in 2026); below 50% hurts them. Maximum adjustment is ±1.5pp (`APPROVAL_WEIGHT = 0.15`).

### Projection formula

```
blended   = 0.8 × poll_avg + 0.2 × lean_baseline
projected = blended + climate_adjustment + approval_adjustment
```

Maximum total adjustment from economic/approval factors is approximately ±4.5pp at current weight settings.

### Senate seat projection

- 65 seats not up in 2026 (23 R, 42 D)
- 35 Class 2 seats are modeled
- Unpolled safe seats (not in `senate_nominees.csv`) default to Republican holds
- Control threshold: 51 seats; 50-50 tie goes to Republicans via VP tiebreaker

### House seat projection

- Polled competitive districts (Tier 1) are modeled individually
- Remaining ~375 unpolled seats are projected via generic ballot swing from the 2024 baseline (R 220, D 215)
- Seat swing formula: `~1 seat per 0.5pp national generic ballot swing`
- Control threshold: 218 seats

---

## Updating Data

| What changed | What to re-run |
|---|---|
| New polls added to CSV | `senate_ingest.py` and/or `house_ingest.py` |
| New nominee confirmed | Edit `senate_nominees.csv` or `house_nominees.csv`, then re-run ingest |
| Economic data refresh | `fetch_economics.py` |
| New approval polls | Update `president_approval_polls.csv` (no DB re-run needed; read directly) |

---

## Data Sources

- **Senate/House polls:** FiveThirtyEight / NYT polling CSV (manual download)
- **Economic indicators:** Federal Reserve Bank of St. Louis — [FRED API](https://fred.stlouisfed.org/)
- **Nominee lists:** Manually maintained in `data/senate_nominees.csv` and `data/house_nominees.csv`
- **Approval polls:** Manually maintained in `data/president_approval_polls.csv`