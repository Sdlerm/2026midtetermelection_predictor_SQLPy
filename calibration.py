"""
calibration.py — Monte Carlo error constants for the 2026 Senate forecast.

This file is the provenance trail: every number has a source, a date,
and a one-line reason. If a constant changes, keep the old value in a
comment so the audit trail survives.

SIGN CONVENTION (used everywhere downstream):
    margin = D_final_share - R_final_share
    positive margin  -> Democrat leads
    negative margin  -> Republican leads
    Nebraska exception: no Democrat is running, so
    margin = Osborn(I) - Ricketts(R). A positive NE margin means
    "Ricketts loses," NOT "Democrats gain a caucus member." The
    simulation reports Osborn's seat separately rather than assuming
    who he'd caucus with.

SCALE: all sigmas below are on the MARGIN scale (percentage points of
D-minus-R), not the vote-share scale. senate_model.py outputs per-
candidate shares; monte_carlo_senate.py converts to margins BEFORE adding
error draws. If you ever perturb shares directly instead, halve these.
"""

import math

# ---------------------------------------------------------------------
# Total polling error
# ---------------------------------------------------------------------
# Source: FiveThirtyEight pollster-accuracy analyses — long-run weighted
# average error of Senate polls in the final 3 weeks, 1998-present.
# Per-cycle range roughly 4.2-5.4 points; long-run average ~5.2.
# (Recorded July 2026.)
#
# Honesty note: 5.2 is an AVERAGE ABSOLUTE error. For a normal
# distribution, sigma = avg_abs_error * sqrt(pi/2) ≈ 6.5. Using 5.2
# directly as sigma is the conservative-toward-CERTAINTY choice: races
# look slightly more decided than the strict conversion implies.
# Chosen deliberately (Samuel, 2026-07-11) to stay consistent with how
# the source itself deploys the number. Revisit after the 2026
# retrospectives publish.
SIGMA_TOTAL_MARGIN = 5.2

# ---------------------------------------------------------------------
# National (correlated) error
# ---------------------------------------------------------------------
# Source: Shirani-Mehr, Rothschild, Goel & Gelman (2018), "Disentangling
# Bias and Variance in Election Polls" — election-level correlated bias
# has SD ≈ 2 points on vote share, which is ~2-3 points on margin when
# the miss is a shared national swing. 2022's ~3.5-point late uniform
# shift is one realized draw of exactly this term.
#
# This is drawn ONCE per simulation and applied to EVERY race with the
# same sign. It is the reason toss-ups are correlated, not independent
# coin flips.
SIGMA_NATIONAL_MARGIN = 2.5

# ---------------------------------------------------------------------
# Local (independent, per-race) error — DERIVED, do not set by hand
# ---------------------------------------------------------------------
# Independent errors add in variance:
#     sigma_total^2 = sigma_national^2 + sigma_local^2
# so local is whatever variance is "left over" after the national
# component. Keeping it derived means changing either input above
# keeps all three internally consistent.
# Current value: sqrt(5.2^2 - 2.5^2) ≈ 4.56
SIGMA_LOCAL_MARGIN = math.sqrt(SIGMA_TOTAL_MARGIN**2 - SIGMA_NATIONAL_MARGIN**2)

# =====================================================================
# HOUSE constants (monte_carlo_house.py)
# =====================================================================
# These were REASONED until 2026-08-27. They are now MEASURED, by
# backtest_house.py, against 2018/2020/2022 — the same standard the Senate
# sigmas are held to. The superseded block read:
#
#     SIGMA_NATIONAL_MARGIN_HOUSE     = 3.0    (kept, see below)
#     SIGMA_TOTAL_MARGIN_HOUSE_POLLED = 6.0    (kept, still untested)
#     SIGMA_TOTAL_MARGIN_HOUSE_LEAN   = 11.0   (replaced by TWO constants)
#
# and carried the note "no House backtest has been run against them ... Replace
# them with backtested numbers once load_historical.py covers House results."
# It never did cover them; fetch_house_backtest_data.py does, from 538's pinned
# lean vintages and the FEC's certified returns. Run:
#
#     python fetch_house_backtest_data.py && python backtest_house.py
#
# WHAT THE BACKTEST CHANGED, AND WHY IT IS TWO CONSTANTS NOW
# ----------------------------------------------------------
# The single 11.0 was standing in for two different failures at once: a lean
# going stale, and a lean describing a district that has since been redrawn.
# Measured, they are not the same size and not close:
#
#     lean on INTACT lines, same cycle          SD 7.2   (n=1174)
#     lean on INTACT lines, one cycle stale     SD 7.5   (n=387)
#     lean on REDRAWN lines, decennial          SD 17.0  (n=1158)
#     lean on REDRAWN lines, mid-decade (NC'19) SD 22.5  (n=12)
#
# Ageing costs about a quarter-point of SD per cycle. Redistricting costs more
# than the entire intact sigma over again. A single 11.0 was therefore wrong in
# both directions — too wide for the districts whose lines still stand, far too
# narrow for the ones redrawn underneath it — and no single value can be right
# for a map that contains both. Hence the split.
#
# All three sigmas are validated, not just estimated: backtest_house.py §4 turns
# each district into a win probability and checks the realized rate bin by bin.
# The tails come back honest at these values. The 0.4-0.6 band does not, and §5
# names the cause — an incumbent runs ~3 points of margin ahead of the lean, and
# house_model.py has no incumbency term. That is a CENTERING error, not a width
# error, and widening these sigmas would hide it rather than fix it.

# National (correlated) error — one draw per simulated election, applied to
# every district. UNCHANGED at 3.0.
#
# The backtest reads 1.94 as the SD of the three per-cycle mean misses, i.e. it
# says 3.0 is conservative. Three observations cannot move a constant, exactly
# as BACKTEST_SCOPE.md §5 argued for the Senate at n=4 and §7 confirmed at n=2;
# and this is the term that decides how correlated the toss-ups are, so the
# wider of two defensible values is the right one. Left where it was.
SIGMA_NATIONAL_MARGIN_HOUSE = 3.0

# Total error for POLLED districts. UNCHANGED at 6.0, and still UNMEASURED:
# the backtest covers the lean-only path only. Testing this one needs an archive
# of historical DISTRICT polls, which BACKTEST_SCOPE.md §7 records as
# unobtainable after 538 went dark — the same wall the Senate work hit, one
# level further down. The original reasoning stands: district polls are sparser,
# less frequent, more often partisan-sponsored and rated lower than the
# statewide Senate polls behind SIGMA_TOTAL_MARGIN (5.2).
SIGMA_TOTAL_MARGIN_HOUSE_POLLED = 6.0

# Total error for LEAN-ONLY districts whose LINES STILL STAND. Was 11.0.
# Measured: local SD 6.8-7.6 overall and 7.2-8.6 inside the competitive band,
# across three same-cycle pairs and one one-cycle-stale pair. 8.0 is adopted
# rather than the ~7.4 midpoint for two stated reasons: 2026 is TWO cycles
# stale and the sample only reaches one, and the competitive band — the only
# place a sigma can change a seat — reads consistently above the pooled figure.
#
# RE-MEASURED 2026-08-29 after fetch_house_backtest_data.py's party classifier
# was fixed to split fusion labels ("D/WF", "R/CON", "GOP"). That bug had been
# scoring 24 genuinely contested districts as uncontested and dropping them —
# every Oregon seat in all three cycles among them. The corrected sample adds
# 47 district-cycles (intact 1561 -> 1593, redrawn 1170 -> 1185) and leaves
# BOTH adopted sigmas where they were: intact SD still 7.19 same-cycle and
# 7.60 one-cycle-stale, redrawn still ~16.9. The recovered seats are mostly
# safe ones whose residuals sit in the bulk of the distribution, so the bug
# was real but not load-bearing for these constants.
SIGMA_TOTAL_MARGIN_HOUSE_LEAN = math.sqrt(8.0**2 + SIGMA_NATIONAL_MARGIN_HOUSE**2)

# Total error for LEAN-ONLY districts whose LINES HAVE BEEN REDRAWN since the
# lean vintage. NEW. In 2026 this is the 95 districts in TX/NC/OH/FL that
# data/district_lean_overrides.csv has not yet given a hand-sourced current
# figure — the ones fetch_district_lean.py prints a warning about on every run.
#
# Measured at local SD 16.0, from four readings that bracket it: three decennial
# redraws at 16.2/17.3/17.5 (13.3/14.4/14.6 once California's wholesale
# renumbering is removed, which is more disruptive than a targeted mid-decade
# redraw), and North Carolina's 2019 MID-DECADE redraw — the right kind of event,
# and the 2026 shape exactly — at 22.5 on n=12.
#
# Read this as the honest width of "we do not know what is in this district any
# more", not as a refined estimate. It is roughly double the intact figure, and
# it is the number that should fall the moment an override lands: a district
# with a hand-sourced lean on current lines is an INTACT district, and
# house_model.py routes it accordingly off district_lean.csv's source column.
SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN = math.sqrt(
    16.0**2 + SIGMA_NATIONAL_MARGIN_HOUSE**2)

# Local (independent) components — DERIVED, do not set by hand. Same identity
# as the Senate: sigma_total^2 = sigma_national^2 + sigma_local^2. Because the
# totals above are now themselves built from a local term plus the national one,
# these invert cleanly back to 6.0 / 8.0 / 16.0.
SIGMA_LOCAL_MARGIN_HOUSE_POLLED = math.sqrt(
    SIGMA_TOTAL_MARGIN_HOUSE_POLLED**2 - SIGMA_NATIONAL_MARGIN_HOUSE**2)
SIGMA_LOCAL_MARGIN_HOUSE_LEAN = math.sqrt(
    SIGMA_TOTAL_MARGIN_HOUSE_LEAN**2 - SIGMA_NATIONAL_MARGIN_HOUSE**2)
SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN = math.sqrt(
    SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN**2 - SIGMA_NATIONAL_MARGIN_HOUSE**2)

# =====================================================================
# HOUSE NATIONAL ENVIRONMENT (house_model.py)
# =====================================================================
# WHY THIS EXISTS (Samuel, 2026-08-10)
# ------------------------------------
# 399 of 435 districts have no 2026 polling. Before this section existed, the
# only 2026 information reaching them was climate_adjustment + approval_
# adjustment: ±0.32 and ±0.33 points of VOTE SHARE per candidate, i.e. a
# national environment of D+1.3 on the MARGIN scale. That is not a midterm
# environment, it is a rounding error, and it left the House forecast pinned to
# a 2022-vintage PRESIDENTIAL map while the Senate forecast — 80% poll weight,
# every race polled — absorbed the actual 2026 environment. The two chambers
# were being asked different questions, which is how the model came to be more
# confident in Democratic Senate control (a net-4 map through TX/AK/OH/IA) than
# in a Democratic House (a net-3 map with the president at 39.7% approval).
#
# THE RELATIONSHIP
# ----------------
# Midterm House national popular vote margin for the PRESIDENT'S party,
# regressed on Gallup presidential approval at the midterm. One row per midterm
# since 1994; margins are D_share - R_share signed toward the president's party
# (negative = president's party lost the national House vote).
#
# Sources: House national popular vote from the Clerk of the House official
# vote statistics; approval from Gallup's final pre-election reading of each
# cycle. (Compiled 2026-08-10.)
#
#   (year, president's party, approval, president's-party House NPV margin)
MIDTERM_APPROVAL_HISTORY = (
    (1994, "D", 46.0, -6.8),   # Clinton; R 51.5 - D 44.7
    (1998, "D", 65.0, -0.9),   # Clinton; post-impeachment backlash, pres party overperformed
    (2002, "R", 63.0, +4.6),   # Bush; post-9/11, the other pres-party gain
    (2006, "R", 38.0, -8.0),   # Bush; D 52.3 - R 44.3
    (2010, "D", 45.0, -6.8),   # Obama; R 51.7 - D 44.9
    (2014, "D", 42.0, -5.7),   # Obama; R 51.2 - D 45.5
    (2018, "R", 40.0, -8.6),   # Trump; D 53.4 - R 44.8
    (2022, "D", 42.0, -2.8),   # Biden; R 50.6 - D 47.8 — the weakest penalty of the set
)

# Ordinary least squares on the table above, computed rather than pasted so the
# fit can never drift from the data it claims to come from.
#   pres_party_margin ≈ MIDTERM_SLOPE * (approval - 50) + MIDTERM_INTERCEPT
# Current fit: slope 0.360, intercept -3.520, residual SD 2.61.
#
# Read the intercept: at 50% approval the president's party still loses the
# national House vote by ~3.5 points. That is the midterm penalty itself, and
# it is the term the old econ+approval channel had no way to express.
def _fit_midterm_approval():
    xs = [a - 50.0 for (_y, _p, a, _m) in MIDTERM_APPROVAL_HISTORY]
    ys = [m for (_y, _p, _a, m) in MIDTERM_APPROVAL_HISTORY]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
             / sum((x - mx) ** 2 for x in xs))
    intercept = my - slope * mx
    resid = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    resid_sd = math.sqrt(sum(r * r for r in resid) / (n - 2))
    return slope, intercept, resid_sd


MIDTERM_SLOPE, MIDTERM_INTERCEPT, MIDTERM_RESIDUAL_SD = _fit_midterm_approval()

# Which party holds the White House in the 2026 midterm. The regression is
# expressed in the president's-party direction; this flips it to the D-margin
# convention everything downstream uses.
PRESIDENT_PARTY = "R"

# n=8 with a 2.6-point residual SD is a thin fit, and 1998/2002 are genuine
# outliers rather than noise. The forecast does NOT treat this term as known:
# SIGMA_NATIONAL_MARGIN_HOUSE (3.0) is the same order as MIDTERM_RESIDUAL_SD,
# so the Monte Carlo's correlated national draw already spans the range this
# regression could plausibly be wrong by. Widening one without the other would
# double-count the same uncertainty.

# Housekeeping
N_HOUSE_SEATS = 435
HOUSE_MAJORITY = 218          # 435 is odd, so there is no tie and no tiebreaker

# Simulations per chunk in monte_carlo_house.py. The Senate allocates one
# (n_sims, n_races) array outright; at 1M x 435 that would be 3.5 GB per array
# and the sim needs two live at once, so the House streams in chunks instead.
# 25,000 x 435 float64 ≈ 87 MB per array — comfortable, and large enough that
# per-chunk overhead stays negligible.
SIM_CHUNK_HOUSE = 25_000

# ---------------------------------------------------------------------
# Simulation count
# ---------------------------------------------------------------------
# Monte Carlo standard error of an estimated win probability is
# sqrt(p * (1-p) / N), worst case at p = 0.5:
#     N = 10,000  ->  ±0.5 percentage points
# Plenty of precision given the sigmas above carry far more real-world
# uncertainty than that. Bump to 100_000 only if runtime stays trivial.
N_SIMS = 500_000

# ---------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------
# The number of seats in the Senate.
N_SENATE_SEATS = 100

# ---------------------------------------------------------------------
# Forecast date
# ---------------------------------------------------------------------
# The date of the forecast. Used for display purposes.
# (Samuel, 2026-07-11)
FORECAST_DATE = "July 20, 2026"

# ---------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------
# Number of decimal places to display for probabilities.
PROB_DECIMAL_PLACES = 1

# Number of decimal places to display for margins.
MARGIN_DECIMAL_PLACES = 1

# Number of decimal places to display for seat counts.
SEAT_DECIMAL_PLACES = 0

# ---------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------
# Probability threshold for calling a race "safe" (i.e., not a toss-up).
# (Samuel, 2026-07-11)
SAFE_PROBABILITY_THRESHOLD = 0.95

# Margin threshold for the closest rating band, "Tilt" (i.e., within this many
# percentage points). This band still names a leader — the House rating scale
# has no neutral bin.
# (Samuel, 2026-07-11; renamed from TOSSUP_MARGIN_THRESHOLD 2026-08-10 when the
# House map dropped the neutral "Toss Up" bin and started siding every district
# with whichever candidate holds the greater projected vote share. Value
# unchanged at 5.0 — only the label and the color changed.)
TILT_MARGIN_THRESHOLD = 5.0

# Margin threshold for calling a race "lean" (i.e., within this many
# percentage points). A "lean" race is MORE competitive than a "likely"
# race, so its threshold must sit below LIKELY's.
# (Samuel, 2026-07-11; was 15.0 — swapped with LIKELY 2026-07-20, the two
# values were inverted and dashboard.py's rate() mislabeled 10-15pt races)
LEAN_MARGIN_THRESHOLD = 10.0

# Margin threshold for calling a race "likely" (i.e., within this many
# percentage points).
# (Samuel, 2026-07-11; was 10.0 — swapped with LEAN 2026-07-20, see above)
LIKELY_MARGIN_THRESHOLD = 15.0

# Margin threshold for calling a race "safe" (i.e., within this many
# percentage points).
# (Samuel, 2026-07-11)
SAFE_MARGIN_THRESHOLD = 20.0
