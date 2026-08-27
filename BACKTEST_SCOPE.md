# Scoping: deriving sigma from a real backtest

**Status:** ~~plan only~~ — **Senate partly executed 2026-08-10; House executed
2026-08-27.** Built: `load_historical_polls.py` (2018 + 2020 polls), `as_of`
threading through `senate_model.py`, `backtest_senate.py`, and — for the House
project §5 deferred — `fetch_house_backtest_data.py` and `backtest_house.py`.

The Senate blend weight `LEAN_ALPHA` has been backtested; the Senate sigma
constants have **not** been changed, and §7 explains why they cannot be at n=2.
The **House lean sigmas HAVE been changed**: they were the ones §5 called the
larger project, and they are now measured rather than reasoned.

Jump to [§7 Results](#7-results-2026-08-10) for the Senate, or
[§8 The House backtest](#8-the-house-backtest-2026-08-27) for the House —
including the place where §5's framing of the House problem turned out to have
the right ingredients in the wrong proportions.
**Date:** 2026-07-29 (Senate results 2026-08-10, House results 2026-08-27)
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

**A leak §5 missed, and it is the one that bites:** `state_lean.csv` is an
undated snapshot that scores best against **2024** (RMSE 6.06, offset −0.69) and
is **8.8 points too Republican for 2018** (RMSE 12.38). So the lean leg is
lookahead-flattered in 2020 and handicapped in 2018 — in opposite directions,
inside the same pooled sweep. Any future backtest of a lean-blended quantity has
to deal with this first; it is a bigger problem than the pollster grades.

### The finding

`LEAN_ALPHA` **is 0.788** (re-measured 2026-08-25, walked from 0.82 via 0.78).
Full reasoning is in the comment above the constant in `senate_model.py`; the
short version:

| Criterion | Optimal alpha | At 0.788 (current vs best) |
|---|---|---|
| RMSE | 0.65 (plateau 0.59–0.72) | 6.00 vs 5.80 |
| MAE | 0.64 | 4.59 vs 4.41 |
| **RMSE, mean error removed** | **0.79 (plateau 0.70–0.88)** | **5.54 vs 5.54 (0.000)** |
| 2018 alone | 0.86 | — |
| 2020 alone | 0.28 | — |

Raw RMSE prefers a low alpha only because the poll leg (+3.9 D, the 2020 miss)
and the lean leg (−3.8 R, the stale-vintage artifact above) have opposite biases
that cancel near 0.5–0.65. Strip the mean error and the variance-optimal alpha is
0.79, which 0.788 sits on to three decimals — finer than a 0.01 sweep grid and an
0.70–0.88 plateau can resolve, so read it as "not worse," not as "better." The
per-cycle optima — 0.86 versus 0.28 — do not agree that any single alpha is
right, so the pooled 0.65 is a compromise between two years rather than a
measurement.

### What is still open

- **`SIGMA_TOTAL_MARGIN`.** Residual SD at alpha 0.788 is **5.59** against the
  constant's 5.2, i.e. the model is mildly overconfident. But per-cycle it is
  3.78 (2018) and 7.46 (2020) — the two cycles bracket 5.2 rather than agreeing
  on 5.59, so this is a two-observation average, not a measurement. Leave 5.2
  sourced from the literature until more cycles exist.
- **`SIGMA_NATIONAL_MARGIN` remains untouchable at n=2**, exactly as §5 argued.
- **`ECON_WEIGHT` / `APPROVAL_WEIGHT`** still unbacktested; needs the §4 Step 2
  climate backfill, which is genuinely low-risk and is now the cheapest
  remaining win.
- **A House equivalent** still needs historical *district* lean vintages, and
  the state-lean vintage finding above says that data problem is the whole job.

---

## 8. The House backtest (2026-08-27)

**Status:** executed. §5 called this "a different, larger project" that "deserves
its own scoping pass"; §7 closed with "a House equivalent still needs historical
*district* lean vintages, and the state-lean vintage finding above says that data
problem is the whole job." Both were right about the shape and wrong about the
difficulty. The data problem was the whole job, and it turned out to be solvable
in an afternoon.

Run `python fetch_house_backtest_data.py && python backtest_house.py`.

### The data problem, and why it was easier than §7 expected

**Lean vintages were hiding in git.** `fetch_district_lean.py` pulls 538's
partisan-lean file from `master`, which holds exactly one column (`2022`), so
there appeared to be one vintage in existence. But 538 rewrote that file in
place four times and never deleted a version. Pinning the commits recovers four
vintages — 2018, 2020, 2021, 2022 — *each published before the election it is
then used to predict*. No lookahead, no reconstruction, no approximation. This
is the single reason the House backtest exists.

**Results came from the FEC, not MEDSL.** `load_historical.py` sourced the
Senate's realized outcomes from MEDSL. MEDSL's Dataverse copy of the House file
now sits behind a guestbook that returns `400 "You may not download this file
without the required Guestbook response"` — not scriptable. The FEC's
*Federal Elections* workbooks are the same certifications, ungated, one per
cycle. **They stop at 2022**: there is no `federalelections2024.xlsx`, and the
Clerk's equivalent is a PDF. So this is a three-cycle backtest whose newest
observation is four years before the election being forecast.

Four parsing rules turned out to be load-bearing rather than cosmetic, and they
are documented at the top of `fetch_house_backtest_data.py`: fusion voting in
NY/CT (sum per FEC candidate ID, since the "Combined Parties:" row carries no
vote count and therefore cannot double-count), interleaved aggregate rows (all
carry FEC ID `n/a`), concurrent unexpired-term specials, and at-large districts
written `00` at the FEC and `-1` at 538.

### The finding

**`SIGMA_TOTAL_MARGIN_HOUSE_LEAN = 11.0` was not one number badly estimated. It
was two very different numbers averaged together.**

| what the lean is up against | SD | n |
|---|---|---|
| intact lines, same cycle as vintage | 7.19 | 1174 |
| intact lines, vintage one cycle old | 7.54 | 387 |
| redrawn lines — decennial (2012 lean vs 2022 map) | 17.01 | 1158 |
| redrawn lines — mid-decade (NC 2019, the 2026 shape) | 22.50 | 12 |

Ageing a lean costs about **a quarter-point of SD per cycle**. Redistricting
under it costs **more than the entire intact sigma over again**. The old 11.0
was therefore too wide for the 310 districts whose 2022 lines still stand and
far too narrow for the 81 in TX/NC/OH/FL that were redrawn in 2025 — and no
single constant can be right for a map containing both.

Adopted, and wired through `monte_carlo_house.simulate` as a third sigma tier:

| constant | was | now | basis |
|---|---|---|---|
| `SIGMA_NATIONAL_MARGIN_HOUSE` | 3.0 | **3.0** | unchanged; n=3 reads 1.94 and cannot move it |
| `SIGMA_TOTAL_MARGIN_HOUSE_POLLED` | 6.0 | **6.0** | unchanged; still untested, see below |
| `SIGMA_TOTAL_MARGIN_HOUSE_LEAN` | 11.0 | **8.5** | local 8.0, intact lines |
| `SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN` | — | **16.3** | local 16.0, redrawn lines |

Effect on the live forecast: P(D majority) 87% → 91.5%, mean D seats 237 → 240.
Most of that is the intact tier tightening, not the redrawn tier widening —
there are nearly four times as many districts in it.

The routing is deliberately coupled to `district_lean_overrides.csv`:
`house_model.lean_geometry_is_stale()` reads `district_lean.csv`'s `source`
column, so hand-sourcing a current-lines lean for a redrawn district narrows its
sigma as a side effect of correcting its margin. That is the right coupling — a
hand-sourced lean on current lines *is* an intact-lines lean — and it means the
81-district wide tier shrinks as the override file fills in.

### Where this plan's §5 was right, and where it was wrong

**Right, again, about the national term.** §5 argued the Senate's
`SIGMA_NATIONAL_MARGIN` could not be improved at n=4. The House version has n=3
and reads 1.94 against the model's 3.0. Left alone, for the third time.

**Wrong about what makes a lean go stale.** §5's framing — and
`calibration.py`'s original comment — treated "2022-vintage lean predicting a
2026 race" as one quantity absorbing "candidate quality, incumbency,
retirements, and boundaries that no longer exist." Measured, the boundary term
dwarfs everything else in that list, and the vintage term is nearly free. The
comment had the right ingredients and the wrong proportions.

**The lean scale-mismatch flagged in `fetch_district_lean.py`'s bias ledger is
visible and small.** 538's lean is presidential-derived while the environment
subtracted from it is a House vote; that shows up as the per-cycle mean residual
(−0.73 / +0.09 / +2.97), i.e. under 3 points, and it lands in the national term
rather than the district term.

### The sigmas are validated, not just estimated

Estimating a sigma from residuals and then reporting the residual spread is
circular. `backtest_house.py` §4 scores every district to a win probability
under the adopted sigma, bins them, and compares to the realized rate. The tails
come back honest at these values — 0.95-1.00 predicts 99.5% and realizes 100.0%
on intact lines. This is the only check in either backtest that could return
"the number is wrong."

### What is still open

- **`SIGMA_TOTAL_MARGIN_HOUSE_POLLED` is still unmeasured** and still 6.0.
  Testing it needs historical *district* polls, which §7 already recorded as
  unobtainable after 538 went dark. Same wall, one level further down. It covers
  44 of 435 districts, so the exposure is bounded.
- **No 2024 cycle**, so nothing observes a lean two cycles stale on intact lines
  — which is the 2026 configuration for 310 districts. The adopted 8.0 sits above
  the measured 7.2-7.5 partly to cover that extrapolation. Re-run this when the
  FEC publishes 2024.
- **Incumbency is now quantified and still uncorrected.** §5 of
  `backtest_house.py`: an incumbent runs ~3 points of margin ahead of their
  district's lean (~4-5 inside the competitive band); open seats sit near zero.
  Removing a per-cycle incumbency offset would take the intact-lines SD from
  ~7.2 to ~6.6. This is the whole of the residual miscalibration in the
  0.40-0.60 probability band, and it is a *centering* error — widening sigma
  would hide it, not fix it. **The blocker is that `house_nominees.csv` names an
  incumbent in only ~83 of 435 districts**, so the correction would apply to a
  fifth of the map and miscentre it against the rest. Sourcing a full incumbency
  roster is now the cheapest remaining accuracy win in the House model, and it
  is a data-collection job rather than a modelling one.
- **Uncontested districts are excluded from every sigma here** (52/36/43 across
  the three cycles) because a ±100 margin is a ballot-access fact, not a
  measurement. The 2026 model projects all 435 as two-way contests and so has no
  representation of this at all.
- **`LEAN_ALPHA_HOUSE` remains unbacktested at 0.80.** The lean-only path this
  backtest measures is the path where alpha does not appear; sweeping it needs
  the district polls that do not exist.
