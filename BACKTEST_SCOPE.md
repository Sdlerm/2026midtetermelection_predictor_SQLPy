# Scoping: deriving sigma from a real backtest

**Status:** ~~plan only~~ — **partly executed 2026-08-10.** Built:
`load_historical_polls.py` (2018 + 2020 polls), `as_of` threading through
`senate_model.py`, and `backtest_senate.py`. The blend weight `LEAN_ALPHA` has
been backtested; the sigma constants have **not** been changed. Jump to
[§7 Results](#7-results-2026-08-10) for what came back — including two places
where this plan's expectations were wrong.
**Date:** 2026-07-29 (results appended 2026-08-10)
**Goal:** replace `calibration.py`'s sigma constants with values measured from this model's own historical error, instead of borrowed from published pollster-accuracy work.

---

## 1. The idea in one paragraph

Sigma answers "when this model says a race is D+3, how wrong is that likely to be?" The only way to measure it is to make predictions for elections whose outcomes are already known, subtract, and look at the spread of the errors. Concretely: rebuild what the model *would have* forecast on the eve of the 2018, 2020, 2022, and 2024 Senate elections, compare each prediction to the actual margin, and take the standard deviation of the residuals. That number is `SIGMA_TOTAL_MARGIN`. It is a genuine measurement in a way the current 5.2 — accurate, but measured on someone else's model — is not.

---

## 2. What you already have

| Component | Table / file | Coverage | Status |
|---|---|---|---|
| Realized outcomes | `historical_results` | 266 rows, 133 races, 2018–2024 | ✅ Ready |
| Candidates + party + incumbency | `candidates` | 64/68/68/66 for 2018/20/22/24 | ✅ Ready |
| Year-parameterized prediction | `predict_all_races(year=)` | already takes a year | ✅ Ready |
| Year-parameterized climate | `get_climate_score(year)`, `get_approval_score(year)` | already take a year | ✅ Ready |

The wiring is genuinely in place. `load_historical.py` did real work here, and the year parameters mean you were already half-anticipating this.

## 3. What is missing

| Gap | Current state | Difficulty |
|---|---|---|
| **Historical polls** | `polls` has 596 rows, **all 2026** (min date 2025-05-08) | Medium — data exists publicly, ingest needs a date-aware variant |
| **Historical climate factors** | `climate_factors` has 7 indicators, **2026 only** | Low — FRED series are historical by nature |
| **Frozen "today"** | `days_ago()` in `senate_model.py` uses `date.today()` | Low-medium — needs an as-of parameter threaded through |
| **Era-appropriate pollster ratings** | `pollster_ratings.csv` is one current snapshot | Hard — see §5, this is a correctness issue not a plumbing one |

---

## 4. Implementation sketch

**Step 1 — Backfill polls.** 538's historical Senate polling files (`senate_polls_historical.csv` and per-cycle archives) carry the same column shape `senate_ingest.py` already parses: pollster, poll date, sample size, candidate, pct. The ingest logic mostly transfers; what changes is that it must write to races keyed by the historical year rather than wiping and rebuilding 2026. Note `senate_ingest.py`'s existing partition discipline (`district = ''` for Senate) — a historical loader has to respect the same convention or it will collide with House rows.

Expect real friction on candidate-name matching. Your `candidates` table was populated by `load_historical.py` from MEDSL/summary files; the poll files spell names differently ("Beto O'Rourke" vs "Robert Francis O'Rourke"), and unmatched polls silently become missing data rather than errors. Budget more time here than feels reasonable.

**Step 2 — Backfill climate factors.** All seven indicators are FRED series with decades of history; presidential approval likewise. Loading four extra years per indicator is a small loop over what `fetch_economics.py` already does. Low risk.

**Step 3 — Freeze the clock.** `days_ago()` and the exponential recency decay currently measure backward from `date.today()`. For a 2018 backtest they must measure backward from 2018-11-06. Thread an `as_of` date through `predict_all_races` → poll averaging → decay. This is the single most important correctness change: forget it and every historical poll reads as ~8 years stale, the decay weights collapse to nearly zero, and every race falls back to structural lean. The backtest would then be measuring the lean model, not the polling model, while looking superficially fine.

**Step 4 — Compute residuals.** For each historical race: `error = predicted_margin - actual_margin`, sign convention per `calibration.py` (D minus R). Store them. This is also your only real chance to check for *bias* — a nonzero mean error means the model systematically favors one party, which sigma alone won't reveal and which matters more than the variance.

**Step 5 — Derive the constants.**
- `SIGMA_TOTAL_MARGIN` = SD of all residuals.
- `SIGMA_NATIONAL_MARGIN` = SD of the four per-cycle mean errors.
- `SIGMA_LOCAL_MARGIN` stays derived — `calibration.py` already computes it from the other two, so don't touch it.

---

## 5. The three honest problems

**Sample size on the national term is fatal.** `SIGMA_NATIONAL_MARGIN` is the SD of election-level bias — one number per cycle. Four cycles gives you **four observations**. A standard deviation estimated from n=4 has a confidence interval so wide it's nearly uninformative; you could easily get 1.2 or 4.0 from the same underlying truth. The published 2.5 comes from Shirani-Mehr et al. pooling far more elections. **Recommendation: derive `SIGMA_TOTAL_MARGIN` from your backtest, but keep `SIGMA_NATIONAL_MARGIN` sourced from the literature.** The backtest can't improve on it, and pretending otherwise would make the model *look* better calibrated while actually being noisier.

**Pollster ratings leak the future.** `pollster_ratings.csv` is a 2026 snapshot. Those grades were assigned partly *because* of how pollsters performed in 2018–2024 — the exact elections you'd be predicting. Weighting a 2018 poll by a rating that encodes 2018's outcome is lookahead bias, and it biases sigma **downward**: the backtest looks more accurate than the model would really have been, so you'd end up over-confident. Fixing it properly means era-appropriate rating vintages, which may not be recoverable. If you can't fix it, the mitigation is to run the backtest with ratings disabled (equal weights) and treat the gap between weighted and unweighted sigma as a lower bound on the leak.

**The House backtest is a different, larger project.** `SIGMA_TOTAL_MARGIN_HOUSE_LEAN = 11.0` governs 399 of 435 districts, and it isn't a polling-error quantity at all — it's "how wrong is a 2022-vintage partisan lean about a 2026 race." Backtesting it needs *historical vintages of district lean*, plus handling districts whose boundaries changed mid-decade. Your own comment in `calibration.py` already names this as the number that, if wrong, invalidates every House P(control). It deserves its own scoping pass; don't fold it into the Senate work.

---

## 6. Effort and recommendation

| Phase | Rough effort |
|---|---|
| Climate backfill | half a session |
| Historical poll ingest + name matching | 2–3 sessions, most of it matching |
| As-of threading | 1 session, plus careful verification |
| Residual analysis + sigma derivation | 1 session |
| **Senate total** | **~5 sessions** |
| House lean backtest | separate project, larger |

**Worth doing?** Yes — but for the diagnostics more than the sigma. The residual *mean* (is the model systematically biased?), the per-cycle pattern (does it miss worse in midterms?), and the by-state breakdown are things no published constant can give you, and they're the kind of thing that will teach you what your model actually does. The sigma refinement itself is a modest gain over 5.2, and §5 says the national term shouldn't move at all.

The as-of threading in Step 3 is the highest-value piece to build carefully, because it's both the easiest thing to get silently wrong and the thing that determines whether the whole exercise measures anything real.

**Suggested first move:** climate backfill plus as-of threading, then run the backtest on *2026 polls against 2026 races* as a smoke test — it should reproduce your current forecast exactly when `as_of = today`. If it doesn't, the harness is broken, and you'll find that out before spending three sessions on name matching.

---

## 7. Results (2026-08-10)

Run `python load_historical_polls.py && python backtest_senate.py --both`.

### What got built

| Piece | Where | Note |
|---|---|---|
| Historical poll ingest | `load_historical_polls.py` | 2,596 rows, 59 scorable races |
| `as_of` clock | `senate_model.py` | `days_ago` / `recency_weight` / `weighted_average_and_stderr`; `None` = today, so live behavior is byte-identical (verified by hashing the 2026 projection before and after) |
| Alpha sweep + diagnostics | `backtest_senate.py` | RMSE / MAE / bias / debiased RMSE, race-level bootstrap, per-cycle optima, lean-vintage check |

### Where this plan was wrong

**§4 Step 1 assumed the poll data was obtainable.** It is not, for 2022 and 2024.
538 shut down in 2025; its polls-page CSVs now return the ABC News HTML shell
with a `200` status, which a naive downloader would happily save as `.csv`. The
only surviving copy found is the git-scraped mirror `simonw/fivethirtyeight-polls`,
last commit **2021-04-05** — complete 2018 and 2020, 34 rows of 2022. So the
backtest is **two cycles, not four**: one midterm, one presidential year.

**§5's "pollster ratings leak the future" is real but nearly worthless in
practice.** Predicted a downward sigma bias; measured, the grades are worth
**0.05 points of margin** (best RMSE 5.76 weighted vs 5.81 at equal
credibility), and they move the optimal alpha by 0.01. The leak exists and does
not matter. Worth knowing before anyone spends a session on rating vintages.

**A leak §5 missed, and it is the one that bites:** `data/state_lean.csv` is an
undated snapshot that scores best against **2024** (RMSE 6.06, offset −0.69) and
is **8.8 points too Republican for 2018** (RMSE 12.38). So the lean leg is
lookahead-flattered in 2020 and handicapped in 2018 — in opposite directions,
inside the same pooled sweep. Any future backtest of a lean-blended quantity has
to deal with this first; it is a bigger problem than the pollster grades.

### The finding

`LEAN_ALPHA` **stays at 0.82.** Full reasoning is in the comment above the
constant in `senate_model.py`; the short version:

| Criterion | Optimal alpha | At 0.82 |
|---|---|---|
| RMSE | 0.65 (plateau 0.59–0.72) | 6.10 vs 5.76 |
| MAE | 0.62 | 4.67 vs 4.35 |
| **RMSE, mean error removed** | **0.77 (plateau 0.68–0.85)** | **5.57 vs 5.55** |
| 2018 alone | 0.84 | — |
| 2020 alone | 0.28 | — |

Raw RMSE prefers a low alpha only because the poll leg (+3.9 D, the 2020 miss)
and the lean leg (−3.8 R, the stale-vintage artifact above) have opposite biases
that cancel near 0.5–0.65. Strip the mean error and the variance-optimal alpha is
0.77, whose plateau contains 0.82. The per-cycle optima — 0.84 versus 0.28 — do
not agree that any single alpha is right, so the pooled 0.65 is a compromise
between two years rather than a measurement.

### What is still open

- **`SIGMA_TOTAL_MARGIN`.** Residual SD at alpha 0.82 is **5.62** against the
  constant's 5.2, i.e. the model is mildly overconfident. But per-cycle it is
  3.69 (2018) and 7.65 (2020) — the two cycles bracket 5.2 rather than agreeing
  on 5.62, so this is a two-observation average, not a measurement. Leave 5.2
  sourced from the literature until more cycles exist.
- **`SIGMA_NATIONAL_MARGIN` remains untouchable at n=2**, exactly as §5 argued.
- **`ECON_WEIGHT` / `APPROVAL_WEIGHT`** still unbacktested; needs the §4 Step 2
  climate backfill, which is genuinely low-risk and is now the cheapest
  remaining win.
- **A House equivalent** still needs historical *district* lean vintages, and
  the state-lean vintage finding above says that data problem is the whole job.
