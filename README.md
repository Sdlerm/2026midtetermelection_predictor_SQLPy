# 2026 Midterm Election Predictor

A polling-based forecaster for the 2026 US midterms: full Senate modeling plus Tier 2 House projections covering all 435 districts. The model blends a credibility- and recency-weighted poll average with a state structural-lean baseline, a six-indicator FRED economic climate score, and a presidential approval adjustment into projected vote shares per candidate. A Monte Carlo simulation then turns the Senate point estimates into win probabilities and a projected seat distribution.

---

## Project Structure

```
project/
├── data/
│   ├── senate.csv                  # Senate polling data — NYT polls feed
│   ├── house.csv                   # House polling data (generic ballot + district) — NYT polls feed
│   ├── senate_nominees.csv         # Ground truth: confirmed Senate general election candidates
│   ├── house_nominees.csv          # Ground truth: confirmed House nominees per district (wide format)
│   ├── state_lean.csv              # State structural lean data
│   ├── district_lean.csv           # District structural lean (GENERATED — run fetch_district_lean.py)
│   ├── district_lean_overrides.csv # Hand-maintained district lean corrections (always wins)
│   ├── president.csv               # Presidential approval polling data (Trump approval)
│   ├── pollster_ratings.csv        # Pollster letter grades (from 538's archived 2023 ratings)
│   ├── fetch_nyt_polls.sh          # Helper script: downloads fresh NYT poll CSVs as nyt_*.csv
│   ├── cd119.geojson               # 119th Congress district boundaries (generated, gitignored)
│   ├── charts/                     # Generated chart PNGs land here
│   ├── 2018-senate-state.csv       # Historical results (summary format)
│   ├── 2020-senate-state.csv       # Historical results (summary format)
│   ├── 2022-senate-state.csv       # Historical results (summary format)
│   └── 2024-senate-state.csv       # Historical results (MEDSL raw precinct format)
├── db/
│   └── elections.db                # SQLite database — auto-created by init_db.py
├── init_db.py                      # Creates the database schema (Senate + House district support)
├── senate_ingest.py                # Loads Senate polling CSV into the database
├── house_ingest.py                 # Loads House polling CSV + district nominee roster
├── load_pollster_ratings.py        # Applies pollster letter grades / credibility to the pollsters table
├── fetch_economics.py              # Pulls FRED indicators + computes weighted approval rating
├── load_historical.py              # Loads 2018/2020/2022/2024 Senate results (historical_results table)
├── senate_model.py                 # Point estimates: poll average + lean + econ + approval
├── house_model.py                  # House Tier 2 projections (all 435 districts, seat counts, no control probability)
├── calibration.py                  # Monte Carlo error constants + race rating thresholds, with provenance
├── monte_carlo_senate.py           # Simulates Senate elections → win probabilities and seat distribution
├── monte_carlo_house.py            # Simulates House elections → district win probabilities and P(control)
├── make_district_geojson.py        # One-time: Census cd119 shapefile → data/cd119.geojson
├── charts.py                       # Matplotlib visualizations (margins, seat count, vote shares)
├── dashboard.py                    # Streamlit dashboard: maps, Monte Carlo probabilities, seat distributions
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

Get a free API key at [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html). The `.env` file is gitignored — never commit it.

### 3. Polling data

Polling CSVs come from the NYT polls feed:

- Senate: https://www.nytimes.com/newsgraphics/polls/senate.csv → save as `data/senate.csv`
- House: https://www.nytimes.com/newsgraphics/polls/house.csv → save as `data/house.csv`

`data/fetch_nyt_polls.sh` automates the download. It deliberately saves as `nyt_*.csv` so a fetch can never clobber the files the pipeline has already ingested — review the fresh copies, then replace the real ones. The script's comments include a crontab line for scheduling a daily fetch.

### 4. Nominees files

**`data/senate_nominees.csv`** is manually maintained. It tells the model which candidates are the confirmed general election nominees, filtering out primary-era poll noise. Format:

```csv
state,party,name
TX,R,Ken Paxton
TX,D,James Talarico
KY,D,Charles Booker
...
```

**`data/house_nominees.csv`** is the House equivalent, in wide format (one row per district with `dem_nominee` and `rep_nominee` columns). A district enters the roster only when **both** nominees are filled in; anything else is skipped with a reason printed in the ingest summary.

### 5. State lean data

`data/state_lean.csv` contains structural partisan lean information for each state, based on historical voting patterns. This provides a baseline expectation that is blended with polling data — and serves as the sole basis for unpolled races (see the lean-only fallback below).

### 6. District boundaries (House map only)

The dashboard's House map needs `data/cd119.geojson` — 119th Congress district boundaries keyed by census `GEOID`. To generate it:

1. Download the Census cartographic boundary file (~30 MB): https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip into `data/`
2. `pip install geopandas`, then run `python make_district_geojson.py` (simplifies geometry so Streamlit stays responsive)

If the file is absent, the dashboard skips the House map with a warning and shows the district table instead — nothing crashes.

---

## Running the Pipeline

Order matters — each step depends on data written by the previous one.

### Step 1 — Initialize the database (first time only)

```bash
python init_db.py
```

Creates `db/elections.db` with all tables. Idempotent and migration-aware: re-running on an older database adds any columns introduced since (e.g. `races.district`).

### Step 2 — Load Senate polling data

```bash
python senate_ingest.py
```

Clears old Senate poll data and reloads from `data/senate.csv`. Also loads nominees from `data/senate_nominees.csv` and state lean from `data/state_lean.csv`.

Three safeguards run automatically during ingestion:

- **Question-block dedup** — some polls include multiple question blocks for the same race (e.g. a head-to-head and a full-field). The block whose candidates best match `senate_nominees.csv` is kept; the rest are discarded. This prevents the same poll from being counted more than once in the weighted average.
- **Stale poll filter** — rows with an `end_date` more than ~18 months before `election_date` are dropped, preventing old cycle data that slipped into the feed from influencing projections.
- **Generic ballot skip** — rows where `candidate_name` is `"Generic Democrat"` or `"Generic Republican"` are logged as warnings and skipped; they test hypothetical matchups, not actual nominees.

### Step 2b — Load House polling data (optional)

```bash
python house_ingest.py
```

Wipes and reloads only the 2026 **House** rows (`district != ''` — the Senate wipe owns `district = ''`, so the two ingests can never touch each other's data). Reuses the Senate ingest's pollster/race/candidate upserts and question-block dedup, keyed on `data/house_nominees.csv`. The run summary lists skipped nominee rows, polled districts missing from the roster, and rostered districts with no polls yet.

### Step 3 — Apply pollster ratings

```bash
python load_pollster_ratings.py
```

Updates `pollsters.credibility` and adds a letter `grade` from `data/pollster_ratings.csv` (sourced from FiveThirtyEight's archived 2023 pollster ratings). Unmatched pollsters default to a neutral 1.0 ("unknown = unproven"). Safe to run before or after either ingest — the ingests never overwrite rating data.

### Step 4 — Fetch economic indicators + approval

```bash
python fetch_economics.py
```

Pulls the six FRED indicators into the `climate_factors` table (requires `FRED_API_KEY` in `.env`), and computes a credibility/recency-weighted presidential approval rating from the approval polling CSV in `data/`.

### Step 4b — Load historical results (optional, one-time)

```bash
python load_historical.py
```

Loads 2018, 2020, 2022, and 2024 Senate election results into the `historical_results` table, for reference and future backtesting work. Two parsers handle the two source formats: the 2018/2020/2022 files are already one winner + one runner-up row per race; the 2024 MEDSL file is precinct-level and gets aggregated to statewide totals first. Not part of the core prediction pipeline; idempotent.

### Step 5 — Run point-estimate predictions

```bash
python senate_model.py
```

Prints each state's projected vote shares, the economic climate and approval scores, toss-up flags, and the projected Senate control outcome.

### Step 5a — District lean data (required once, before House projections)

```bash
python fetch_district_lean.py
```

Downloads FiveThirtyEight's district partisan-lean file, merges
`data/district_lean_overrides.csv` on top of it, and writes
`data/district_lean.csv` (435 districts). **The output is generated — edit the
overrides file, not the output.** Re-running re-downloads the base and rebuilds
the output; your overrides survive.

The base file is **2022 vintage on 2022 maps**. Every run prints the districts in
redrawn states (TX/NC/OH/FL — 95 of them) whose lean describes boundaries that no
longer exist. Add rows to the overrides file as you source replacements:

```csv
district,dem_margin,note
TX-35,12.4,2024 pres margin on post-2025 lines
```

### Step 5b — House Tier 2 projections (optional)

```bash
python house_model.py
```

Projects **all 435 districts**. Polled districts blend polls with district lean
(`LEAN_ALPHA_HOUSE = 0.80`, matching the Senate); unpolled districts fall back to
lean plus the same climate and approval adjustments — the identical formula with
the poll term unavailable, mirroring the Senate's lean-only fallback. Rows carry
`has_polls` so display can separate the two.

It reports **seat counts, not a control probability** — probabilities come from
`monte_carlo_house.py` (Step 6b). Two gaps are deliberate and documented in the
file's bias ledger: there is no generic-ballot term (538's feed is dead), and flip
detection only works for the ~83 districts in `house_nominees.csv` — elsewhere the
incumbent is unknown, so no flip means *unknown*, not *hold*.

### Step 6 — Monte Carlo simulation (Senate)

```bash
python monte_carlo_senate.py
```

Runs 1,000,000 simulated elections on top of the model's adjusted margins and prints per-race win probabilities, the mean Democratic seat count, P(D majority), P(R control), and Nebraska-specific probabilities (see below).

### Step 6b — Monte Carlo simulation (House)

```bash
python monte_carlo_house.py
```

Same 1,000,000 simulations, same `margin + national_error + local_error` structure, over all 435 districts. Prints per-district win probabilities for competitive seats, the D/R seat distribution with a 90% range, and P(D majority ≥ 218). Runs in ~5 seconds at ~700 MB.

Two things differ from the Senate version, both forced by the data:

- **Per-district local σ.** Only ~36 districts are polled; the other ~399 rest on `district_lean.csv`. Their error isn't polling error, it's "how wrong is 2022-vintage lean about 2026" — roughly double. So σ_local is a vector selected by `has_polls`, not a scalar.
- **Chunked simulation.** A full 1,000,000 × 435 float64 error matrix is 3.5 GB, and the calculation needs two at once. This version streams in chunks of `SIM_CHUNK_HOUSE` and accumulates. Identical arithmetic, bounded memory — a sanity check asserts two different chunk sizes agree.

⚠️ **The House σ values in `calibration.py` are reasoned, not backtested.** The Senate's trace to published pollster-accuracy work; the House's are argued by analogy from them. Since 399 of 435 districts depend on the lean-only σ, treat P(control) as order-of-magnitude until those numbers are measured against real House results.

### Step 7 — Visualizations

**Matplotlib charts:**
```bash
python charts.py
```
Generates a margin bar chart, a projected seat-count chart, and a vote-share comparison chart. Saved to `data/charts/` as `margins.png`, `seat_count.png`, and `vote_shares.png`.

**Streamlit dashboard:**
```bash
streamlit run dashboard.py
```
Opens an interactive web dashboard at `http://localhost:8501` with:

- **Senate margin map** — continuous D/R gradient; lean-only races (no polls) render at reduced opacity with an explicit hover label, so polled and unpolled projections are visually distinct
- **House Tier 2 map** — all 435 districts colored by discrete rating bins (Safe/Likely/Lean/Toss Up) driven by the margin thresholds in `calibration.py`; lean-only districts render at reduced opacity so coverage never reads as confidence. Requires `data/cd119.geojson` (see Setup §6) and `data/district_lean.csv` (Step 5a). For TX/NC/OH/FL both the boundaries *and* the lean are pre-2025 redraw.
- **Senate outlook** — P(D majority ≥51), P(R control ≥50 + VP), mean D seats, the Nebraska independent's win/pivot probabilities, and a seat-distribution histogram colored by which side of the majority line each bar falls on (the blue share of the mass *is* P(D majority))
- **House outlook** — P(D majority ≥218), P(R majority), mean D seats, a 90% seat range, the same seat-distribution histogram, and a sortable table of the ~150 competitive districts (P(D win) between 5% and 95%), which is where the House simulation becomes readable — a choropleth can show a rating but not a probability
- Senate control banner, projected flips, sortable race table with per-race Monte Carlo win probabilities, and a per-state drilldown

Both outlook sections run the full 1,000,000 simulations rather than a reduced count, so dashboard figures always match the CLI exactly. They sit behind `@st.cache_data(ttl=300)`; a cold load costs ~4.8s and ~1.0 GB for both chambers' models and simulations together.

⚠️ The House probability block carries a standing warning that its σ values are unvalidated. Do not remove it without backtesting the constants first — see Step 6b.

---

## How the Model Works

### Four-layer projection system

1. **State structural lean** — partisan baseline from historical voting patterns
2. **Weighted poll average** — current polling with credibility and recency weights
3. **Economic climate adjustment** — national economic conditions
4. **Presidential approval adjustment** — a separate, independently tunable lever

### 1. State structural lean

Each state has a baseline partisan tendency captured in `state_lean.csv`. This represents the expected vote share in a neutral environment based on recent election history.

### 2. Weighted poll average

Each poll's weight is determined by two factors multiplied together:

- **Pollster credibility** — a 0–3 scale derived from FiveThirtyEight's archived letter grades. Ungraded pollsters default to 1.0.
- **Recency decay** — `exp(-λ × days_old)`, where `λ = 0.0231`. This gives a half-life of ~30 days. A poll from two months ago carries roughly 25% of the weight of a poll from today.

The weighted average is: `Σ(pct × credibility × decay) / Σ(credibility × decay)`

**F-rated poll exclusion:** polls from F-graded pollsters are dropped per-race whenever the race has at least one poll from a non-F pollster. If F-rated polls are the *only* polling a race has, they're kept — bad data beats no data, and the lean blend tempers them.

### 3. Poll-lean blend

The model blends the state lean with the poll average using `LEAN_ALPHA` (currently 0.8):

```
base_projection = (LEAN_ALPHA × poll_avg) + ((1 - LEAN_ALPHA) × state_lean)
```

Polls get 80% weight, structural lean 20%.

**Lean-only fallback:** races with no usable polling are still projected, from 100% structural lean plus the national climate/approval adjustments. These are flagged as lean-only throughout the outputs — no polling standard error is fabricated for them, and the dashboard renders them at reduced opacity.

### 4. Economic climate score

Six FRED indicators are each normalized to [0, 1] against their historical range, then converted to a [-1, +1] directional score:

| Indicator              | FRED Series     | Higher value means...     |
|------------------------|-----------------|---------------------------|
| Unemployment           | UNRATE          | Bad economy → favors D    |
| CPI year-over-year     | CPIAUCSL        | High inflation → favors D |
| Consumer sentiment     | UMCSENT         | Low sentiment → favors D  |
| Real disposable income | DSPIC96         | Low income → favors D     |
| Real GDP growth        | A191RL1Q225SBEA | Slow growth → favors D    |
| Fed funds rate         | FEDFUNDS        | High rates → favors D     |

These six scores are averaged into a single `climate_score` in [-1, +1], where positive favors Democrats (bad economy for the Republican White House incumbent).

### 5. Presidential approval score

Approval polling is aggregated with the same credibility/recency weighting as horse-race polls, then converted to a [-1, +1] score where positive favors Democrats (low approval of the Republican incumbent). It enters the projection through its own weight (`APPROVAL_WEIGHT`) rather than being folded into the climate score, so its influence is tunable independently.

### 6. Final projection formula

```
projected = base_projection
          + (party_direction × climate_score  × ECON_WEIGHT     × 10)
          + (party_direction × approval_score × APPROVAL_WEIGHT × 10)
```

- `party_direction`: +1 for D, -1 for R
- `ECON_WEIGHT`: currently **0.20** → maximum economic adjustment ±2 points
- `APPROVAL_WEIGHT`: currently **0.06** → maximum approval adjustment ±0.6 points

Races where the finalists land within `TOSSUP_THRESHOLD_PP` (1.1 points) of each other are flagged as toss-ups.

---

## Monte Carlo Simulation

`monte_carlo_senate.py` and `monte_carlo_house.py` turn the point estimates into probabilities. Each of the 1,000,000 simulated elections perturbs every race's margin with two error draws:

```
simulated_margin = adjusted_margin + national_error + local_error
```

- **National error** (σ = 2.5 on the margin scale) is drawn **once per simulation** and applied to every race with the same sign — the "all the polls missed in the same direction" mode (2016, 2020). This correlation is why five projected narrow flips are not five independent coin flips: on a bad night they fall together.
- **Local error** is drawn per race per simulation — independent state-specific noise. Its σ is derived so total error matches the long-run average Senate polling miss (σ_total = 5.2).

All error constants live in `calibration.py`, each with a source, date, and rationale in comments — if a constant changes, the old value stays in a comment as an audit trail.

Sign convention throughout: `margin = D − R`, positive = Democrat leads. Nebraska is the exception — `margin = Osborn(I) − Ricketts(R)` — and the simulation reports Osborn's seat separately (including P(Osborn wins) and P(Osborn is the pivotal seat)) rather than assuming who he caucuses with.

---

## Tuning the Model

| Parameter               | Location              | Effect                                                                                                         |
|-------------------------|-----------------------|----------------------------------------------------------------------------------------------------------------|
| `LAMBDA`                | `senate_model.py`     | Recency decay rate. Currently `0.0231` (half-life ~30 days). Higher = older polls lose weight faster.          |
| `LEAN_ALPHA`            | `senate_model.py`     | Blend between polls (0.8) and structural lean (0.2). Higher = more weight to polls.                            |
| `ECON_WEIGHT`           | `senate_model.py`     | Economic climate influence. Currently `0.20` (max ±2 pts). 0 = no economic adjustment.                         |
| `APPROVAL_WEIGHT`       | `senate_model.py`     | Presidential approval influence. Currently `0.06` (max ±0.6 pts).                                              |
| `TOSSUP_THRESHOLD_PP`   | `senate_model.py`     | Margin (1.1 pts) inside which a race is flagged as a toss-up.                                                  |
| `INDICATOR_RANGES`      | `senate_model.py`     | Historical min/max used to normalize each FRED indicator.                                                      |
| `SIGMA_NATIONAL_MARGIN` | `calibration.py`      | Correlated national polling error (2.5 pts on the margin scale).                                               |
| `SIGMA_TOTAL_MARGIN`    | `calibration.py`      | Total polling error (5.2 pts); local error is derived from total and national.                                 |
| `N_SIMS`                | `calibration.py`      | Number of Monte Carlo simulations (1,000,000).                                                                 |
| Rating thresholds       | `calibration.py`      | Margin bins for race ratings: Toss Up < 5, Lean < 10, Likely < 15, Safe beyond. Used by the House map.         |
| `LEAN_ALPHA_HOUSE`      | `house_model.py`      | Poll/lean blend for House races. 0.80 — 80% poll, 20% district lean, matching `LEAN_ALPHA`.                    |
| `RANDOM_SEED`           | `monte_carlo_senate.py`, `monte_carlo_house.py` | Set to an int for reproducible simulation runs; `None` = fresh randomness.                   |
| House error σ values    | `calibration.py`      | `SIGMA_*_HOUSE_*` — reasoned, not backtested. The lean-only σ drives 399 of 435 districts.                     |
| `SIM_CHUNK_HOUSE`       | `calibration.py`      | Simulations per chunk. Memory knob; changing it changes the random stream, so it is visible in output.          |
| Pollster grades         | `data/pollster_ratings.csv` | Letter grade + credibility per pollster; applied by `load_pollster_ratings.py`.                          |
| State lean values       | `data/state_lean.csv` | Baseline partisan expectations for each state.                                                                 |
| District lean values    | `data/district_lean_overrides.csv` | Per-district corrections layered over the 538 base. Edit this, never `district_lean.csv`.         |

---

## Independent Candidates

Any function that keys a state's finalists by party string (e.g. building a `{"D": ..., "R": ...}` dict) needs an explicit fallback for `"I"` — Nebraska (Ricketts-R vs. Osborn-I) has no Democratic nominee, so a D/R-only lookup silently drops that race. `charts.py` and the model's race-finalizing logic both handle this; mirror that pattern in any new code that groups by party.

Two layers treat independents differently, on purpose:

- **Point-estimate seat count** (`senate_model.py`): `INDIE_CAUCUS = {"I": "D"}` assigns Osborn's seat to the Democratic caucus for the control projection.
- **Monte Carlo** (`monte_carlo_senate.py`): makes no caucus assumption — Osborn's seat is tracked separately, with explicit probabilities for him winning and for his seat being pivotal.

---

## Senate Control Projection

After projecting individual races, the model calculates projected Senate control:

1. Counts existing seats not up for election
2. Adds projected winners from races being modeled
3. Adds independent seats based on their caucus alignment
4. Reports the final D/R/I seat count and which party holds the majority

The Monte Carlo layer supplements this single-outcome projection with a full seat distribution: mean D seats, P(D majority ≥51), and P(R control ≥50 + VP tiebreak).

---

## Data Sources

- **Polling (Senate + House):** NYT polls feed (`data/senate.csv`, `data/house.csv`)
- **Presidential approval:** NYT approval polling CSV (`data/president.csv`)
- **Pollster ratings:** FiveThirtyEight's archived 2023 pollster ratings (github.com/fivethirtyeight/data), joined on Pollster Rating ID
- **Economic indicators:** Federal Reserve Bank of St. Louis — FRED API
- **Nominees:** Manually maintained `data/senate_nominees.csv` and `data/house_nominees.csv`
- **State lean:** Manually maintained `data/state_lean.csv`
- **District lean:** FiveThirtyEight partisan-lean districts file (github.com/fivethirtyeight/data), 2022 vintage on 2022 maps, plus hand overrides — see `fetch_district_lean.py`
- **Historical results:** MEDSL precinct-level 2024 data and summary-format 2018/2020/2022 results (`data/*-senate-state.csv`), loaded via `load_historical.py`

---

## Technical Details

### Database Schema

The SQLite database (`db/elections.db`) contains the following tables:

- **pollsters** — pollster metadata (name, credibility, letter grade, partisan lean)
- **races** — electoral races, keyed `(year, state, district)`. `district = ''` means Senate; a zero-padded `'01'`–`'53'` means a House district. This partition is what lets the Senate and House ingests wipe/reload independently.
- **candidates** — candidates in each race (name, party, incumbency status)
- **polls** — individual poll results with all metadata
- **climate_factors** — economic indicators, climate scores, and the approval rating
- **historical_results** — past election outcomes (2018/2020/2022/2024), loaded by `load_historical.py`

### Conventions

- `snake_case` for functions/variables, `UPPER_CASE` for module-level constants.
- DB access goes through `get_connection()` / manual `.close()` — no context managers for SQLite connections; follow the existing pattern.
- States are always 2-letter abbreviations at the DB layer; full names are mapped at the ingestion boundary. House districts are zero-padded TEXT (`'01'`), because TEXT sorts character-by-character.
- No test suite — verify changes by running the affected script directly and spot-checking output.

### Generated artifacts

`data/charts/*.png` and `db/elections.db` are regenerated by the pipeline — don't hand-edit them.

---

## Planned Enhancements

- District lean on post-redistricting maps: replace the 95 stale TX/NC/OH/FL rows via `data/district_lean_overrides.csv` (every `fetch_district_lean.py` run lists them)
- Generic-ballot term for the House — needs a live feed; 538's `polls-page` CSVs now return HTML
- Backtested House error σ values to replace the reasoned ones in `calibration.py` — the single biggest source of doubt in `monte_carlo_house.py`
- Incumbent party for all 435 districts, so flip detection stops being limited to the ~83 rostered ones
- Updated TX/NC/OH/FL boundary data reflecting the 2025 mid-decade redraws
- Backtesting the model against the loaded 2018/2020/2022/2024 historical results
- Automated data refresh via `fetch_nyt_polls.sh` on a cron schedule

---

## Notes

- The project uses SQLite for data storage, pandas for data manipulation, and NumPy for simulation
- All predictions are probabilistic projections, not certainties
- The model is designed for the 2026 midterm election cycle
- Economic and approval adjustments assume Republicans hold the White House (2025–2027)

---

## License

This project is for educational and analytical purposes.
