# 2026 Senate Election Predictor

A polling-based election model for the 2026 US Senate races. Combines credibility-weighted, recency-decayed poll averages with a six-indicator economic climate score and state structural lean to produce projected vote shares for each general election matchup.

---

## Project Structure

```
project/
├── data/
│   ├── senate.csv                  # Polling data — downloaded from FiveThirtyEight
│   ├── senate_nominees.csv         # Ground truth: confirmed general election candidates
│   ├── state_lean.csv              # State structural lean data
│   └── president_approval_polls.csv # Presidential approval polling data
├── db/
│   └── elections.db                # SQLite database — auto-created by init_db.py
├── init_db.py                      # Creates the database schema
├── senate_ingest.py                # Loads polling CSV into the database
├── fetch_economics.py              # Pulls economic indicators from FRED API
├── senate_model.py                 # Weighted average + economic adjustment → predictions
├── charts.py                       # Matplotlib visualizations (pop-up window)
├── dashboard.py                    # Streamlit web dashboard
├── wiring_explained.md             # Narrative explanation of the model's math
└── README.md                       # This file
```

---

## Setup

### 1. Python environment

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv venv
source venv/bin/activate          # macOS/Linux
# venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 2. Environment variables

Create a `.env` file in the project root:

```
FRED_API_KEY=your_key_here
```

Get a free API key at [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html).

### 3. Polling data

The senate polling CSV is included in `data/senate.csv`. For updates:

1. Go to [https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv](https://projects.fivethirtyeight.com/polls-page/data/senate_polls.csv)
2. Save the file as `data/senate.csv`

### 4. Nominees file

`data/senate_nominees.csv` is manually maintained. It tells the model which candidates are the confirmed general election nominees, filtering out primary-era poll noise. Format:

```csv
state,party,name
TX,R,Ken Paxton
TX,D,James Talarico
KY,D,Charles Booker
...
```

Add a row for each confirmed nominee. The model will only produce a prediction for a state if both a D and an R nominee are present here.

### 5. State lean data

`data/state_lean.csv` contains structural partisan lean information for each state, based on historical voting patterns. This provides a baseline expectation that is blended with polling data.

---

## Running the Pipeline

Run these steps in order. After initial setup, only steps 2–5 need to be repeated when you want fresh predictions.

### Step 1 — Initialize the database (first time only)

```bash
python init_db.py
```

Creates `db/elections.db` with all tables. Safe to re-run; existing data is preserved.

### Step 2 — Load polling data

```bash
python senate_ingest.py
```

Clears old poll data and reloads from `data/senate.csv`. Also loads nominees from `data/senate_nominees.csv` and state lean from `data/state_lean.csv`.

Three safeguards run automatically during ingestion:

- **Question-block dedup** — some polls include multiple question blocks for the same race (e.g. a head-to-head and a full-field). The block whose candidates best match `senate_nominees.csv` is kept; the rest are discarded. This prevents the same poll from being counted more than once in the weighted average.
- **Stale poll filter** — rows with an `end_date` more than ~18 months before `election_date` are dropped, preventing old cycle data that slipped into the feed from influencing projections.
- **Generic ballot skip** — rows where `candidate_name` is `"Generic Democrat"` or `"Generic Republican"` are logged as warnings and skipped; they test hypothetical matchups, not actual nominees.

### Step 3 — Fetch economic indicators

```bash
python fetch_economics.py
```

Pulls the six FRED indicators and stores them in the `climate_factors` table. Requires a valid `FRED_API_KEY` in `.env`.

### Step 4 — Run predictions (terminal output)

```bash
python senate_model.py
```

Prints each state's projected vote shares, the economic climate score, and which candidate is projected to lead. Also displays the projected Senate control outcome.

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

### Three-layer projection system

The model combines three information sources to project vote shares:

1. **State structural lean** — partisan baseline from historical voting patterns
2. **Weighted poll average** — current polling with credibility and recency weights
3. **Economic climate adjustment** — national economic conditions

### 1. State structural lean

Each state has a baseline partisan tendency captured in `state_lean.csv`. This represents the expected vote share in a neutral environment based on recent election history.

### 2. Weighted poll average

Each poll's weight is determined by two factors multiplied together:

- **Pollster credibility** — a 0–3 scale derived from FiveThirtyEight's numeric grade. Ungraded pollsters default to 1.0.
- **Recency decay** — `exp(-λ × days_old)`, where `λ = 0.0231`. This gives a half-life of ~30 days. A poll from two months ago carries roughly 25% of the weight of a poll from today.

The weighted average is: `Σ(pct × credibility × decay) / Σ(credibility × decay)`

### 3. Poll-lean blend

The model blends the state lean with the poll average using a parameter `LEAN_ALPHA` (currently 0.8):

```
base_projection = (LEAN_ALPHA × poll_avg) + ((1 - LEAN_ALPHA) × state_lean)
```

This means polls get 80% weight and structural lean gets 20% weight. If no polls exist for a state, it falls back to 100% structural lean.

### 4. Economic climate score

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

### 5. Final projection formula

```
projected = base_projection + (party_direction × climate_score × ECON_WEIGHT × 10)
```

- `party_direction`: +1 for D, -1 for R
- `ECON_WEIGHT`: currently 0.3 — tunable in `senate_model.py`. At 0.3, the maximum economic adjustment is ±3 percentage points.

---

## Tuning the Model

| Parameter | Location | Effect |
|---|---|---|
| `LAMBDA` | `senate_model.py` | Controls recency decay rate. Currently `0.0231` (half-life ~30 days). Higher = older polls lose weight faster. |
| `LEAN_ALPHA` | `senate_model.py` | Controls blend between polls (0.8) and structural lean (0.2). Higher = more weight to polls. |
| `ECON_WEIGHT` | `senate_model.py` | Controls how much the economic climate shifts the projection. 0 = no economic adjustment; 1.0 = full weight. |
| `INDICATOR_RANGES` | `senate_model.py` | Historical min/max used to normalize each FRED indicator. |
| Pollster credibilities | `pollsters` table | Can be manually adjusted in SQLite after ingestion. |
| State lean values | `data/state_lean.csv` | Baseline partisan expectations for each state. |

---

## Independent Candidates

The model handles independent candidates who caucus with major parties. Currently configured:

- **Nebraska Independent**: Dan Osborn is coded as "I" but caucuses with Democrats for seat control calculations.

This is configured in the `INDIE_CAUCUS` dictionary in `senate_model.py`.

---

## Senate Control Projection

After projecting individual races, the model calculates projected Senate control:

1. Counts existing seats not up for election
2. Adds projected winners from races being modeled
3. Adds independent seats based on their caucus alignment
4. Reports the final D/R/I seat count and which party holds the majority

---

## Data Sources

- **Polling:** FiveThirtyEight senate polls CSV (`data/senate.csv`)
- **Economic indicators:** Federal Reserve Bank of St. Louis — FRED API
- **Nominees:** Manually maintained `data/senate_nominees.csv`
- **State lean:** Manually maintained `data/state_lean.csv`
- **Presidential approval:** FiveThirtyEight approval polls (`data/president_approval_polls.csv`)

---

## Technical Details

### Database Schema

The SQLite database (`db/elections.db`) contains the following tables:

- **pollsters** — pollster metadata (name, credibility, partisan lean)
- **races** — electoral races (year, state, competitiveness)
- **candidates** — candidates in each race (name, party, incumbency status)
- **polls** — individual poll results with all metadata
- **climate_factors** — economic indicators and climate scores
- **historical_results** — past election outcomes (for future use)

### File Naming Conventions

- `senate_*.py` — senate-specific pipeline scripts
- `*_model.py` — prediction and projection logic
- `*_ingest.py` — data loading and database population
- `init_db.py` — database initialization
- `fetch_*.py` — external data retrieval

---

## Planned Enhancements

- Presidential approval rating as a seventh climate factor
- House race expansion (architecture extends cleanly)
- Historical election results integration
- Automated data refresh workflows

---

## Notes

- The project uses SQLite for data storage and pandas for data manipulation
- All predictions are probabilistic projections, not certainties
- The model is designed for the 2026 midterm election cycle
- Economic adjustments assume Republicans hold the White House (2025-2027)

---

## License

This project is for educational and analytical purposes.
