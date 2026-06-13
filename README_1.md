# 2026 Senate Election Predictor

A polling-based election model for the 2026 US Senate races. Combines credibility-weighted, recency-decayed poll averages with a six-indicator economic climate score to produce projected vote shares for each general election matchup.

---

## Project Structure

```
project/
├── data/
│   ├── senate.csv          # Polling data — download manually (see below)
│   ├── nominees.csv        # Ground truth: confirmed general election candidates
│   └── climate.csv         # Optional: hand-authored climate factor overrides
├── db/
│   └── elections.db        # SQLite database — auto-created by init_db.py
├── init_db.py              # Creates the database schema
├── ingest.py               # Loads polling CSV into the database
├── fetch_economics.py      # Pulls economic indicators from FRED API
├── model.py                # Weighted average + economic adjustment → predictions
├── charts.py               # Matplotlib visualizations (pop-up window)
├── dashboard.py            # Streamlit web dashboard
├── wiring_explained.md     # Narrative explanation of the model's math
└── README.md               # This file
```

---

## Setup

### 1. Python environment

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv venv
source venv/bin/activate          # macOS/Linux
# venv\Scripts\activate           # Windows

pip install pandas streamlit matplotlib requests python-dotenv
```

### 2. Environment variables

Create a `.env` file in the project root:

```
FRED_API_KEY=your_key_here
```

Get a free API key at [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html).

### 3. Polling data

FiveThirtyEight shut down in March 2025, so the senate polling CSV must be downloaded manually:

1. Go to [https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv](https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv)
2. Save the file as `data/senate.csv`

### 4. Nominees file

`data/nominees.csv` is manually maintained. It tells the model which candidates are the confirmed general election nominees, filtering out primary-era poll noise. Format:

```csv
state,party,name
TX,R,Ken Paxton
TX,D,James Talarico
KY,D,Charles Booker
...
```

Add a row for each confirmed nominee. The model will only produce a prediction for a state if both a D and an R nominee are present here.

---

## Running the Pipeline

Run these steps in order. After initial setup, only steps 3–5 need to be repeated when you want fresh predictions.

### Step 1 — Initialize the database (first time only)

```bash
python init_db.py
```

Creates `db/elections.db` with all tables. Safe to re-run; existing data is preserved.

### Step 2 — Load polling data

```bash
python ingest.py
```

Clears old poll data and reloads from `data/senate.csv`. Also loads `data/climate.csv` if present.

Three safeguards run automatically during ingestion:

- **Question-block dedup** — some polls include multiple question blocks for the same race (e.g. a head-to-head and a full-field). The block whose candidates best match `nominees.csv` is kept; the rest are discarded. This prevents the same poll from being counted more than once in the weighted average.
- **Stale poll filter** — rows with an `end_date` more than ~18 months before `election_date` are dropped, preventing old cycle data that slipped into the feed from influencing projections.
- **Generic ballot skip** — rows where `candidate_name` is `"Generic Democrat"` or `"Generic Republican"` are logged as warnings and skipped; they test hypothetical matchups, not actual nominees.

> **Note:** A minor date-format warning may appear during this step. It's cosmetic — data loads correctly.

### Step 3 — Fetch economic indicators

```bash
python fetch_economics.py
```

Pulls the six FRED indicators and stores them in the `climate_factors` table. Requires a valid `FRED_API_KEY` in `.env`.

### Step 4 — Run predictions (terminal output)

```bash
python model.py
```

Prints each state's projected vote shares, the economic climate score, and which candidate is projected to lead.

### Step 5 — Visualizations

**Matplotlib charts (pop-up window):**
```bash
python charts.py
```
Generates a margin bar chart and a vote-share comparison chart. Also saves `margins.png` and `vote_shares.png` to the project root.

**Streamlit dashboard (browser):**
```bash
streamlit run dashboard.py
```
Opens an interactive web dashboard at `http://localhost:8501` with sortable tables, a margin chart, and a per-state drilldown.

---

## How the Model Works

### Weighted poll average

Each poll's weight is determined by two factors multiplied together:

- **Pollster credibility** — a 0–3 scale derived from FiveThirtyEight's numeric grade. Ungraded pollsters default to 1.0.
- **Recency decay** — `exp(-λ × days_old)`, where `λ = 0.0231`. This gives a half-life of ~30 days. A poll from two months ago carries roughly 25% of the weight of a poll from today.

The weighted average is: `Σ(pct × credibility × decay) / Σ(credibility × decay)`

### Economic climate score

Six FRED indicators are each normalized to [0, 1] against their historical range, then converted to a [-1, +1] directional score:

| Indicator | FRED Series | Higher value means... |
|---|---|---|
| Unemployment | UNRATE | Bad economy → favors D |
| CPI year-over-year | CPIAUCSL | High inflation → favors D |
| Consumer sentiment | UMCSENT | Low sentiment → favors D |
| Real disposable income | DSPIC96 | Low income → favors D |
| Real GDP growth | A191RL1Q225SBEA | Slow growth → favors D |
| Fed funds rate | FEDFUNDS | High rates → favors D |

These six scores are averaged into a single `climate_score` in [-1, +1], where positive favors Democrats (bad economy for the Republican White House incumbent).

![img.png](img.png)

### Projection formula

```
projected = poll_avg + (party_direction × climate_score × ECON_WEIGHT × 10)
```

- `party_direction`: +1 for D, -1 for R
- `ECON_WEIGHT`: currently 0.3 — tunable in `model.py`. At 0.3, the maximum economic adjustment is ±3 percentage points.

---

## Tuning the Model

| Parameter | Location | Effect |
|---|---|---|
| `LAMBDA` | `model.py` | Controls recency decay rate. Currently `0.0231` (half-life ~30 days). Higher = older polls lose weight faster. |
| `ECON_WEIGHT` | `model.py` | Controls how much the economic climate shifts the poll average. 0 = polls only; 1.0 = full weight. |
| `INDICATOR_RANGES` | `model.py` | Historical min/max used to normalize each FRED indicator. |
| Pollster credibilities | `pollsters` table | Can be manually adjusted in SQLite after ingestion. |

---

## Known Issues & Notes

- **FiveThirtyEight is offline.** `data/senate.csv` must be downloaded and updated manually.
- **Date format warning in `ingest.py`** — a minor pandas warning about mixed date formats. Does not affect data correctness.
- **Junie / AI coding assistants** — PyCharm's built-in AI ("Junie") previously overwrote `ingest.py` and replaced `senate.csv` with synthetic data during a session break. Do not leave AI assistants with write access to project files unattended. Verify file contents after any AI-assisted session.
- **iCloud Drive sync** — the project is stored in iCloud. There is a small risk of sync conflicts or file replacement if iCloud overwrites a locally modified file. Back up `data/` and `db/` regularly.

---

## Planned Enhancements

- Presidential approval rating as a seventh climate factor (currently ~44% for Trump); source: G. Elliott Morris's independent data operation
- House race expansion (architecture should extend cleanly without disrupting Senate work)
- Resolve the `ingest.py` date format warning

---

## Data Sources

- **Polling:** FiveThirtyEight / NYT senate polls CSV (manual download)
- **Economic indicators:** Federal Reserve Bank of St. Louis — FRED API
- **Nominees:** Manually maintained `data/nominees.csv`
