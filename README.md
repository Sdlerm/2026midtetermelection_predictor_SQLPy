# 2026 Midterm Election Predictor

A polling-based forecaster for the 2026 US midterms: full Senate modeling plus Tier 2 House projections covering all 435 districts. The model blends a credibility- and recency-weighted poll average with a structural-lean baseline into projected vote shares per candidate. The Senate adds a six-indicator FRED economic climate score and a presidential approval adjustment; the House instead shifts its district-lean baseline by a national environment term inferred from approval via a midterm regression, because 399 of its 435 districts have no polling and the small share-level nudges left them describing a 2022 presidential map rather than a 2026 midterm. Monte Carlo simulations turn both chambers' point estimates into win probabilities and seat distributions.

---

## Project Structure

```
project/
├── data/
│   ├── senate.csv                  # Senate polling data — NYT polls feed
│   ├── house.csv                   # House polling data (generic ballot + district) — NYT polls feed
│   ├── district_lean.csv           # District structural lean (GENERATED — run fetch_district_lean.py)
│   ├── district_lean_overrides.csv # Hand-maintained district lean corrections (always wins)
│   ├── president.csv               # Presidential approval polling data (Trump approval)
│   ├── fetch_nyt_polls.sh          # Helper script: downloads fresh NYT poll CSVs as nyt_*.csv
│   ├── cd119.geojson               # 119th Congress district boundaries (generated, gitignored)
│   ├── charts/                     # Generated chart PNGs land here
│   ├── senate_polls_538_historical.csv # 2018+2020 Senate polls (538 mirror, general stage only)
│   ├── 2018-senate-state.csv       # Historical results (summary format)
│   ├── 2020-senate-state.csv       # Historical results (summary format)
│   ├── 2022-senate-state.csv       # Historical results (summary format)
│   └── 2024-senate-state.csv       # Historical results (MEDSL raw precinct format)
├── db/
│   └── elections.db                # SQLite database — auto-created by init_db.py
├── senate_nominees.csv             # Ground truth: confirmed Senate general election candidates
├── house_nominees.csv              # Ground truth: confirmed House nominees per district (wide format)
├── state_lean.csv                  # State structural lean data
├── pollster_ratings.csv            # Pollster letter grades (from 538's archived 2023 ratings)
├── init_db.py                      # Creates the database schema (Senate + House district support)
├── senate_ingest.py                # Loads Senate polling CSV into the database
├── house_ingest.py                 # Loads House polling CSV + district nominee roster
├── load_pollster_ratings.py        # Applies pollster letter grades / credibility to the pollsters table
├── fetch_economics.py              # Pulls FRED indicators + computes weighted approval rating
├── load_historical.py              # Loads 2018/2020/2022/2024 Senate results (historical_results table)
├── load_historical_polls.py        # Loads 2018/2020 Senate polls (538 mirror) for backtesting
├── backtest_senate.py              # Sweeps LEAN_ALPHA against 2018/2020 outcomes; reports bias, sigma, per-cycle optima
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
# venvScriptsactivate           # Windows

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

-   Senate: [https://www.nytimes.com/newsgraphics/polls/senate.csv](https://www.nytimes.com/newsgraphics/polls/senate.csv) → save as `data/senate.csv`
-   House: [https://www.nytimes.com/newsgraphics/polls/house.csv](https://www.nytimes.com/newsgraphics/polls/house.csv) → save as `data/house.csv`

`data/fetch_nyt_polls.sh` automates the download. It deliberately saves as `nyt_*.csv` so a fetch can never clobber the files the pipeline has already ingested — review the fresh copies, then replace the real ones. The script's comments include a crontab line for scheduling a daily fetch.

### 4. Nominees files

**`senate_nominees.csv`** (project root) is manually maintained. It tells the model which candidates are the confirmed general election nominees, filtering out primary-era poll noise. Format:

```csv
state,party,name
TX,R,Ken Paxton
TX,D,James Talarico
KY,D,Charles Booker
...
```

**`house_nominees.csv`** is the House equivalent, in wide format (one row per district with `dem_nominee` and `rep_nominee` columns). A district enters the roster only when **both** nominees are filled in; anything else is skipped with a reason printed in the ingest summary.

**Same-party generals** are the one documented exception. California and Washington run a top-two primary, so a safe district can send two candidates of the *same* party to the general — CA-07 is Doris Matsui vs. Mai Vang, both Democrats. The opposing column is legitimately empty there, which the both-sides rule read as "one side unsettled" and skipped; the district then fell through to the lean-only path and was modeled as a generic Democrat against a generic Republican who is not on the ballot.

Such a row sets **`same_party_general`** to `D` or `R` and puts *both* finalists in that party's nominee column, separated by `; `, **incumbent first** (one incumbent flag covers the pair, so the ordering is what tells them apart):

```csv
state,district,dem_nominee,rep_nominee,...,same_party_general
CA,7,Doris Matsui; Mai Vang,,...,D
```

The separator is a semicolon, never a comma — real names carry commas (`Victor Aguilar, Jr.`), and several nominee cells already use a comma to list unresolved primary contenders as one opaque string.

Downstream this is a **locked seat**: certain for the party, unmodeled between the people. `house_model.py` emits its rows with `same_party_general=True` and `projected`/`lean`/`margin` set to `None` — no number is known, rather than one being missing — and neither the polled nor the lean-only path touches the district. `monte_carlo_house.baseline_seats()` carries it as a baseline seat instead of simulating it, which is why that function now takes `(predictions, races)` like its Senate counterpart. Nothing in this repo measures an intra-party split, so nobody is marked the winner.

### 5. State lean data

`state_lean.csv` contains structural partisan lean information for each state, based on historical voting patterns. This provides a baseline expectation that is blended with polling data — and serves as the sole basis for unpolled races (see the lean-only fallback below).

### 6. District boundaries (House map only)

The dashboard's House map needs `data/cd119.geojson` — 119th Congress district boundaries keyed by census `GEOID`. To generate it:

1.  Download the Census cartographic boundary file (~30 MB): [https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip](https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_500k.zip) into `data/`
2.  `pip install geopandas`, then run `python make_district_geojson.py` (simplifies geometry so Streamlit stays responsive)

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

Clears old Senate poll data and reloads from `data/senate.csv`. Also loads nominees from `senate_nominees.csv` and state lean from `state_lean.csv`.

Three safeguards run automatically during ingestion:

-   **Question-block dedup** — some polls include multiple question blocks for the same race (e.g. a head-to-head and a full-field). The block whose candidates best match `senate_nominees.csv` is kept; the rest are discarded. This prevents the same poll from being counted more than once in the weighted average.
-   **Stale poll filter** — rows with an `end_date` more than ~18 months before `election_date` are dropped, preventing old cycle data that slipped into the feed from influencing projections.
-   **Generic ballot skip** — rows where `candidate_name` is `"Generic Democrat"` or `"Generic Republican"` are logged as warnings and skipped; they test hypothetical matchups, not actual nominees.

### Step 2b — Load House polling data (optional)

```bash
python house_ingest.py
```

Wipes and reloads only the 2026 **House** rows (`district != ''` — the Senate wipe owns `district = ''`, so the two ingests can never touch each other's data). Reuses the Senate ingest's pollster/race/candidate upserts and question-block dedup, keyed on `house_nominees.csv`. The run summary lists skipped nominee rows, polled districts missing from the roster, and rostered districts with no polls yet.

### Step 3 — Apply pollster ratings

```bash
python load_pollster_ratings.py
```

Updates `pollsters.credibility` and adds a letter `grade` from `pollster_ratings.csv` (sourced from FiveThirtyEight's archived 2023 pollster ratings). Unmatched pollsters default to a neutral 1.0 ("unknown = unproven"). Safe to run before or after either ingest — the ingests never overwrite rating data.

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

Downloads FiveThirtyEight's district partisan-lean file, merges `data/district_lean_overrides.csv` on top of it, and writes `data/district_lean.csv` (435 districts). **The output is generated — edit the overrides file, not the output.** Re-running re-downloads the base and rebuilds the output; your overrides survive.

The base file is **2022 vintage on 2022 maps**. Every run prints the districts in redrawn states (TX/NC/OH/FL — 95 of them) whose lean describes boundaries that no longer exist. Add rows to the overrides file as you source replacements:

```csv
district,dem_margin,note
TX-35,12.4,2024 pres margin on post-2025 lines
```

### Step 5b — House Tier 2 projections (optional)

```bash
python house_model.py
```

Projects **all 435 districts**:

```
polled district:    0.80 * poll_avg + 0.20 * (lean + env/2)
unpolled district:                          (lean + env/2)
```

`env` is the **national House environment** as a D-minus-R margin, from `national_environment_margin()`. It enters each candidate's *share* at half weight because one point of margin is half a point of two-party vote share, and it shifts the **lean baseline** rather than the finished projection — polls already contain the 2026 environment, so a polled district must not be shifted twice and feels the term only through its 20% lean weight. Rows carry `has_polls` so display can separate the two paths.

Where `env` comes from (in order): a stored `GENERIC_BALLOT_D` climate factor if one has been ingested, otherwise presidential approval run through the midterm regression in `calibration.py` (`MIDTERM_APPROVAL_HISTORY`). At 39.7% approval that inference is **D+7.2**.

> **Why this term exists.** Before it, the only 2026 information reaching the 399 unpolled districts was the per-candidate climate and approval nudges: ±0.65 points of share, i.e. a national environment of D+1.3 on the margin scale. That is not a midterm environment, and it left the House pinned to a 2022-vintage *presidential* map while the Senate — every race polled, 80% poll weight — absorbed the real one. The result was a model more confident in Democratic **Senate** control (a net-4 map through TX/AK/OH/IA) than in a Democratic **House**, which inverts the actual difficulty of the two maps. The climate and approval adjustments are no longer applied to House projections at all: the national signal now enters exactly once, through `env`. The Senate still uses them, where they are a small correction on top of polls rather than the only 2026 input.

It reports **seat counts, not a control probability** — probabilities come from `monte_carlo_house.py` (Step 6b). Remaining gaps, documented in the file's bias ledger: the environment is *inferred* from approval rather than measured from a generic ballot; there is **no incumbency term**, so uniform swing is applied to a presidential lean with no correction for entrenched incumbents; and flip detection only works for the ~83 districts in `house_nominees.csv` — elsewhere the incumbent is unknown, so no flip means *unknown*, not *hold*.

A district whose top two poll averages sum to less than `MIN_TWO_WAY_POLL_SUM` (60) is not a two-way general-election matchup — AK-01's "Democratic nominee" averages 3.8%, a primary-field share — so its polled projection is discarded with a warning and the lean-only path rebuilds it. Blending a primary-field share against a two-party baseline produced a projected R+37.6 in an R+14.6 district.

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

Districts decided by a **same-party general** are the exception: they carry no D-vs-R margin, so they are not simulated at all. `baseline_seats()` counts them as seats held regardless of the draw and checks that simulated + decided still equals 435 — it raises if not, because P(majority) measured against the wrong chamber size is a false forecast, not a degraded one.

Two things differ from the Senate version, both forced by the data:

-   **Per-district local σ.** Only ~36 districts are polled; the other ~399 rest on `district_lean.csv`. Their error isn't polling error, it's "how wrong is 2022-vintage lean about 2026" — roughly double. So σ_local is a vector selected by `has_polls`, not a scalar.
-   **Chunked simulation.** A full 1,000,000 × 435 float64 error matrix is 3.5 GB, and the calculation needs two at once. This version streams in chunks of `SIM_CHUNK_HOUSE` and accumulates. Identical arithmetic, bounded memory — a sanity check asserts two different chunk sizes agree.

⚠️ **The House σ values in `calibration.py` are reasoned, not backtested.** The Senate's trace to published pollster-accuracy work; the House's are argued by analogy from them. Since 399 of 435 districts depend on the lean-only σ, treat P(control) as order-of-magnitude until those numbers are measured against real House results.

### Step 6c — Backtest the Senate poll weighting (optional, one-off)

```bash
python load_historical_polls.py     # 2018 + 2020 polls -> DB (one-off)
python backtest_senate.py --both    # sweep LEAN_ALPHA, weighted and unweighted
```

Rebuilds what the model would have projected on the eve of the 2018 and 2020 Senate elections, then sweeps `LEAN_ALPHA` from 0 to 1 to find the value that minimizes error against what actually happened. **59 races scored.** Safe to run repeatedly: the ingest is year-scoped to 2018/2020 and registers pollsters insert-only, so it cannot alter the live 2026 forecast — verified by hashing the 2026 projection before and after.

The correctness pivot is the `as_of` date now threaded through `days_ago` → `recency_weight` → `weighted_average_and_stderr` (`None` = today, so live behavior is unchanged). Without it a 2018 poll would be measured as ~8 years stale, its recency weight would round to zero, every historical race would silently collapse to structural lean, and the backtest would be scoring the *lean* model while looking fine.

**Result: `LEAN_ALPHA` is 0.788** (re-measured 2026-08-25, walked from 0.82 via 0.78). Minimizing raw RMSE prefers 0.65 — but that gain is two unrelated biases cancelling, not accuracy: the poll leg runs +3.9 toward D (the 2020 polling miss) and the lean leg −3.8 toward R (`state_lean.csv` is ~2024-vintage and 8.8 points too Republican for 2018). Remove each mix's mean error and the variance-optimal weight — the question a blend weight actually answers — is **0.79, plateau 0.70–0.88**, which 0.788 sits on to three decimals. The per-cycle optima flatly disagree (2018 wants 0.86, 2020 wants 0.28), so the pooled figure is a compromise between two years rather than a measurement. Correcting national bias is `SIGMA_NATIONAL_MARGIN`'s job, not the blend weight's.

⚠️ **Two cycles, not four.** 538 shut down in 2025 and its polls-page CSVs now return the ABC News HTML shell with a `200` status — a naive downloader saves 314 KB of markup as `.csv`. 2018 and 2020 come from the git-scraped mirror `simonw/fivethirtyeight-polls` (last commit 2021-04-05); 2022 and 2024 Senate polling is not in any mirror found. Sixty races is a usable sample for a per-race blend weight and a useless one for per-cycle error, so this run does **not** license changing `SIGMA_NATIONAL_MARGIN`. Full method, limits, and open items in `BACKTEST_SCOPE.md` §7.

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

-   **Senate margin map** — continuous D/R gradient; lean-only races (no polls) render at reduced opacity with an explicit hover label, so polled and unpolled projections are visually distinct
-   **House Tier 2 map** — all 435 districts colored by discrete rating bins (Safe/Likely/Lean/Tilt, D and R) driven by the margin thresholds in `calibration.py`. There is **no neutral toss-up bin**: every district is colored for whichever candidate has the greater projected vote share, and margins under `TILT_MARGIN_THRESHOLD` (5 pts) land in the pale Tilt D / Tilt R shades rather than a shared yellow. Closeness is conveyed by the band, not by withholding a call. Lean-only districts render at reduced opacity so coverage never reads as confidence; districts decided by a same-party general render at full opacity and hover as `SAME-PARTY GENERAL, seat certain`, with no vote shares — the opacity channel encodes how much is known, and that is the one basis that knows outright. Requires `data/cd119.geojson` (see Setup §6) and `data/district_lean.csv` (Step 5a). For TX/NC/OH/FL both the boundaries *and* the lean are pre-2025 redraw.
-   **Senate outlook** — P(D majority ≥51), P(R control ≥50 + VP), mean D seats, the Nebraska independent's win/pivot probabilities, and a seat-distribution histogram colored by which side of the majority line each bar falls on (the blue share of the mass *is* P(D majority))
-   **House outlook** — P(D majority ≥218), P(R majority), mean D seats, a 90% seat range, the same seat-distribution histogram, and a sortable table of the ~150 competitive districts (P(D win) between 5% and 95%), which is where the House simulation becomes readable — a choropleth can show a rating but not a probability
-   Senate control banner, projected flips, sortable race table with per-race Monte Carlo win probabilities, and a per-state drilldown

Both outlook sections run the full 1,000,000 simulations rather than a reduced count, so dashboard figures always match the CLI exactly. They sit behind `@st.cache_data(ttl=300)`; a cold load costs ~4.8s and ~1.0 GB for both chambers' models and simulations together.

⚠️ The House probability block carries a standing warning that its σ values are unvalidated. Do not remove it without backtesting the constants first — see Step 6b. The warning's "+1pp national shift" sensitivity is computed live by `house_sensitivity.py`, not typed: it had already gone stale once as a hardcoded string, and the national-environment term moved it by tens of points.

⚠️ **The two chambers' P(control) figures are not on equal footing and should not be read as directly comparable.** Every Senate race is polled and carries 80% poll weight, so the Senate forecast tracks current polling. 399 of 435 House districts have no 2026 polling at all and rest on a lean baseline shifted by an *inferred* national environment. Same simulation machinery, very different evidence underneath.

---

## How the Model Works

### Four-layer projection system

1.  **State structural lean** — partisan baseline from historical voting patterns
2.  **Weighted poll average** — current polling with credibility and recency weights
3.  **Economic climate adjustment** — national economic conditions
4.  **Presidential approval adjustment** — a separate, independently tunable lever

Layers 3 and 4 are **Senate-only**. The House replaces both with a single national environment term applied to the district lean baseline (Step 5b) — same signal, expressed at a magnitude a midterm actually has, and counted once. The rest of this section describes the Senate path.

### 1. State structural lean

Each state has a baseline partisan tendency captured in `state_lean.csv`. This represents the expected vote share in a neutral environment based on recent election history.

### 2. Weighted poll average

Each poll's weight is determined by two factors multiplied together:

-   **Pollster credibility** — a 0–3 scale derived from FiveThirtyEight's archived letter grades. Ungraded pollsters default to 1.0.
-   **Recency decay** — `exp(-λ × days_old)`, where `λ = 0.0231`. This gives a half-life of ~30 days. A poll from two months ago carries roughly 25% of the weight of a poll from today.

The weighted average is: `Σ(pct × credibility × decay) / Σ(credibility × decay)`

**F-rated poll exclusion:** polls from F-graded pollsters are dropped per-race whenever the race has at least one poll from a non-F pollster. If F-rated polls are the *only* polling a race has, they're kept — bad data beats no data, and the lean blend tempers them.

### 3. Poll-lean blend

The model blends the state lean with the poll average using `LEAN_ALPHA` (0.788 — backtested, see Step 6c; `senate_model.py` is the source of truth):

```
base_projection = (LEAN_ALPHA × poll_avg) + ((1 - LEAN_ALPHA) × state_lean)
```

Polls get 80% weight, structural lean 20%.

**Lean-only fallback:** races with no usable polling are still projected, from 100% structural lean plus the national climate/approval adjustments. These are flagged as lean-only throughout the outputs — no polling standard error is fabricated for them, and the dashboard renders them at reduced opacity.

### 4. Economic climate score

Six FRED indicators are each normalized to [0, 1] against their historical range, then converted to a [-1, +1] directional score:

Indicator

FRED Series

Higher value means...

Unemployment

UNRATE

Bad economy → favors D

CPI year-over-year

CPIAUCSL

High inflation → favors D

Consumer sentiment

UMCSENT

Low sentiment → favors D

Real disposable income

DSPIC96

Low income → favors D

Real GDP growth

A191RL1Q225SBEA

Slow growth → favors D

Fed funds rate

FEDFUNDS

High rates → favors D

These six scores are averaged into a single `climate_score` in [-1, +1], where positive favors Democrats (bad economy for the Republican White House incumbent).

### 5. Presidential approval score

Approval polling is aggregated with the same credibility/recency weighting as horse-race polls, then converted to a [-1, +1] score where positive favors Democrats (low approval of the Republican incumbent). It enters the projection through its own weight (`APPROVAL_WEIGHT`) rather than being folded into the climate score, so its influence is tunable independently.

### 6. Final projection formula

```
projected = base_projection
          + (party_direction × climate_score  × ECON_WEIGHT     × 10)
          + (party_direction × approval_score × APPROVAL_WEIGHT × 10)
```

-   `party_direction`: +1 for D, -1 for R
-   `ECON_WEIGHT`: currently **0.26** → maximum economic adjustment ±2.6 points
-   `APPROVAL_WEIGHT`: currently **0.11** → maximum approval adjustment ±1.1 points

Races where the finalists land within `TOSSUP_THRESHOLD_PP` (1.2 points) of each other are flagged as toss-ups.

---

## Monte Carlo Simulation

`monte_carlo_senate.py` and `monte_carlo_house.py` turn the point estimates into probabilities. Each of the 1,000,000 simulated elections perturbs every race's margin with two error draws:

```
simulated_margin = adjusted_margin + national_error + local_error
```

-   **National error** (σ = 2.5 on the margin scale) is drawn **once per simulation** and applied to every race with the same sign — the "all the polls missed in the same direction" mode (2016, 2020). This correlation is why five projected narrow flips are not five independent coin flips: on a bad night they fall together.
-   **Local error** is drawn per race per simulation — independent state-specific noise. Its σ is derived so total error matches the long-run average Senate polling miss (σ_total = 5.2).

All error constants live in `calibration.py`, each with a source, date, and rationale in comments — if a constant changes, the old value stays in a comment as an audit trail.

Sign convention throughout: `margin = D − R`, positive = Democrat leads. Nebraska is the exception — `margin = Osborn(I) − Ricketts(R)` — and the simulation reports Osborn's seat separately (including P(Osborn wins) and P(Osborn is the pivotal seat)) rather than assuming who he caucuses with.

---

## Tuning the Model

Parameter

Location

Effect

`LAMBDA`

`senate_model.py`

Recency decay rate. Currently `0.0231` (half-life ~30 days). Higher = older polls lose weight faster.

`LEAN_ALPHA`

`senate_model.py`

Blend between polls (0.788) and structural lean (0.212). Higher = more weight to polls.

`ECON_WEIGHT`

`senate_model.py`

Economic climate influence. Currently `0.26` (max ±2.6 pts). 0 = no economic adjustment.

`APPROVAL_WEIGHT`

`senate_model.py`

Presidential approval influence. Currently `0.11` (max ±1.1 pts).

`TOSSUP_THRESHOLD_PP`

`senate_model.py`

Margin (1.2 pts) inside which a race is flagged as a toss-up.

`INDICATOR_RANGES`

`senate_model.py`

Historical min/max used to normalize each FRED indicator.

`SIGMA_NATIONAL_MARGIN`

`calibration.py`

Correlated national polling error (2.5 pts on the margin scale).

`SIGMA_TOTAL_MARGIN`

`calibration.py`

Total polling error (5.2 pts); local error is derived from total and national.

`N_SIMS`

`calibration.py`

Number of Monte Carlo simulations (1,000,000).

Rating thresholds

`calibration.py`

Margin bins for race ratings: Tilt < 5, Lean < 10, Likely < 15, Safe beyond. Every bin names a side — the House map has no neutral toss-up bin.

`LEAN_ALPHA_HOUSE`

`house_model.py`

Poll/lean blend for House races. 0.80 — 80% poll, 20% district lean. Deliberately *not* matched to `LEAN_ALPHA`: that one has been backtested and moved, this one is unbacktested and stays put until district lean vintages exist. See `house_model.py`.

`MIDTERM_APPROVAL_HISTORY`

`calibration.py`

The 8 midterms (1994–2022) the national House environment is regressed from. Slope/intercept are fit at import, never pasted.

`PRESIDENT_PARTY`

`calibration.py`

`"R"` for 2026. Flips the regression from the president's-party direction into the D-margin convention.

`MIN_TWO_WAY_POLL_SUM`

`house_model.py`

60.  Below this, a district's two poll averages aren't a general-election matchup; it falls back to lean-only.

`RANDOM_SEED`

`monte_carlo_senate.py`, `monte_carlo_house.py`

Set to an int for reproducible simulation runs; `None` = fresh randomness.

House error σ values

`calibration.py`

`SIGMA_*_HOUSE_*` — reasoned, not backtested. The lean-only σ drives 399 of 435 districts.

`SIM_CHUNK_HOUSE`

`calibration.py`

Simulations per chunk. Memory knob; changing it changes the random stream, so it is visible in output.

Pollster grades

`pollster_ratings.csv`

Letter grade + credibility per pollster; applied by `load_pollster_ratings.py`.

State lean values

`state_lean.csv`

Baseline partisan expectations for each state.

District lean values

`data/district_lean_overrides.csv`

Per-district corrections layered over the 538 base. Edit this, never `district_lean.csv`.

---

## Independent Candidates

Any function that keys a state's finalists by party string (e.g. building a `{"D": ..., "R": ...}` dict) needs an explicit fallback for `"I"` — Nebraska (Ricketts-R vs. Osborn-I) has no Democratic nominee, so a D/R-only lookup silently drops that race. `charts.py` and the model's race-finalizing logic both handle this; mirror that pattern in any new code that groups by party.

Two layers treat independents differently, on purpose:

-   **Point-estimate seat count** (`senate_model.py`): `INDIE_CAUCUS = {"I": "D"}` assigns Osborn's seat to the Democratic caucus for the control projection.
-   **Monte Carlo** (`monte_carlo_senate.py`): makes no caucus assumption — Osborn's seat is tracked separately, with explicit probabilities for him winning and for his seat being pivotal.

---

## Senate Control Projection

After projecting individual races, the model calculates projected Senate control:

1.  Counts existing seats not up for election
2.  Adds projected winners from races being modeled
3.  Adds independent seats based on their caucus alignment
4.  Reports the final D/R/I seat count and which party holds the majority

The Monte Carlo layer supplements this single-outcome projection with a full seat distribution: mean D seats, P(D majority ≥51), and P(R control ≥50 + VP tiebreak).

---

## Data Sources

-   **Polling (Senate + House):** NYT polls feed (`data/senate.csv`, `data/house.csv`)
-   **Presidential approval:** NYT approval polling CSV (`data/president.csv`)
-   **Pollster ratings:** FiveThirtyEight's archived 2023 pollster ratings (github.com/fivethirtyeight/data), joined on Pollster Rating ID
-   **Economic indicators:** Federal Reserve Bank of St. Louis — FRED API
-   **Nominees:** Manually maintained `senate_nominees.csv` and `house_nominees.csv`
-   **State lean:** Manually maintained `state_lean.csv`
-   **District lean:** FiveThirtyEight partisan-lean districts file (github.com/fivethirtyeight/data), 2022 vintage on 2022 maps, plus hand overrides — see `fetch_district_lean.py`
-   **Historical results:** MEDSL precinct-level 2024 data and summary-format 2018/2020/2022 results (`data/*-senate-state.csv`), loaded via `load_historical.py`

---

## Technical Details

### Database Schema

The SQLite database (`db/elections.db`) contains the following tables:

-   **pollsters** — pollster metadata (name, credibility, letter grade, partisan lean)
-   **races** — electoral races, keyed `(year, state, district)`. `district = ''` means Senate; a zero-padded `'01'`–`'53'` means a House district. This partition is what lets the Senate and House ingests wipe/reload independently.
-   **candidates** — candidates in each race (name, party, incumbency status)
-   **polls** — individual poll results with all metadata
-   **climate_factors** — economic indicators, climate scores, and the approval rating
-   **historical_results** — past election outcomes (2018/2020/2022/2024), loaded by `load_historical.py`

### Conventions

-   `snake_case` for functions/variables, `UPPER_CASE` for module-level constants.
-   DB access goes through `get_connection()` / manual `.close()` — no context managers for SQLite connections; follow the existing pattern.
-   States are always 2-letter abbreviations at the DB layer; full names are mapped at the ingestion boundary. House districts are zero-padded TEXT (`'01'`), because TEXT sorts character-by-character.
-   No test suite — verify changes by running the affected script directly and spot-checking output.

### Generated artifacts

`data/charts/*.png` and `db/elections.db` are regenerated by the pipeline — don't hand-edit them.

---

## Planned Enhancements

-   District lean on post-redistricting maps: replace the 95 stale TX/NC/OH/FL rows via `data/district_lean_overrides.csv` (every `fetch_district_lean.py` run lists them)
-   **Measured** generic ballot for the House — the `GENERIC_BALLOT_D` slot exists and `national_environment_margin()` prefers it automatically; nothing populates it yet, so the environment is inferred from approval (±2.6pp regression residual, n=8). Needs a live feed; 538's `polls-page` CSVs now return HTML
-   Incumbency term for the House, so the national environment isn't applied as pure uniform swing over a presidential lean
-   Senate poll-weight review — **done 2026-08-10, re-measured 2026-08-25; `LEAN_ALPHA` is 0.788.** See Step 6c above and §7 of `BACKTEST_SCOPE.md`. What it does *not* settle: 0.788 is right on average, but it says nothing about whether a race resting on three July polls of a hypothetical matchup (ME) should be trusted like one resting on 28 polls. That is a poll-depth question, not a blend-weight question, and it is still open
-   **Incumbency and house-effect terms for the Senate** — the backtest's worst single miss is 2020 ME: predicted D+5.0, actual R+9.1, a 14-point miss against a four-term incumbent. That is the shape of error an incumbency term catches and a blend weight cannot
-   Backtested House error σ values to replace the reasoned ones in `calibration.py` — the single biggest source of doubt in `monte_carlo_house.py`
-   Incumbent party for all 435 districts, so flip detection stops being limited to the ~83 rostered ones
-   Updated TX/NC/OH/FL boundary data reflecting the 2025 mid-decade redraws
-   Extending the backtest past two cycles — 2022/2024 Senate polling is not in any surviving 538 mirror (see `load_historical_polls.py`); recovering it means a different source, not a different script
-   Climate/approval backfill so `ECON_WEIGHT` and `APPROVAL_WEIGHT` can be backtested too — FRED series are historical by nature, so this is the cheapest remaining win
-   Automated data refresh via `fetch_nyt_polls.sh` on a cron schedule

---

## Notes

-   The project uses SQLite for data storage, pandas for data manipulation, and NumPy for simulation
-   All predictions are probabilistic projections, not certainties
-   The model is designed for the 2026 midterm election cycle
-   Economic and approval adjustments assume Republicans hold the White House (2025–2027)

---

## License

This project is for educational and analytical purposes.