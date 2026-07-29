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
# READ THIS BEFORE TRUSTING ANY HOUSE OUTPUT.
#
# The Senate sigmas above are MEASURED — they trace to published pollster-
# accuracy work. The House sigmas below are REASONED, not measured: they are
# argued from the Senate numbers by analogy, and no House backtest has been run
# against them. They are honest starting values, not calibrated ones. Replace
# them with backtested numbers once load_historical.py covers House results.
# (Recorded 2026-07-28.)
#
# The structural difference from the Senate: only ~36 of 435 districts are
# polled at all. The other ~399 are projected from district_lean.csv, so their
# error is not POLLING error — it is the error of using 2022-vintage partisan
# lean to predict a 2026 race. That is a much larger and differently-shaped
# quantity, which is why the House needs two total-error constants where the
# Senate needs one.

# National (correlated) error — one draw per simulated election, applied to
# every district. Set slightly above the Senate's 2.5 because a House map is
# more exposed to a uniform national swing than a set of 35 state races with
# heavy incumbent-specific variation: the same generic-ballot miss moves all
# 435 districts together.
SIGMA_NATIONAL_MARGIN_HOUSE = 3.0

# Total error for POLLED districts. Above the Senate's 5.2 because district
# polls are sparser, less frequent, more often partisan-sponsored, and rated
# lower than statewide Senate polls — the same reasons house_ingest.py has to
# work harder to dedupe them.
SIGMA_TOTAL_MARGIN_HOUSE_POLLED = 6.0

# Total error for LEAN-ONLY districts. This is the big one and the least
# defensible: it stands in for "how wrong is a 2022-vintage partisan lean about
# a 2026 result", absorbing candidate quality, incumbency, retirements, and —
# for the 95 districts in TX/NC/OH/FL — boundaries that no longer exist. Set to
# roughly double the polled figure. If this number is wrong, every P(control)
# this model prints is wrong, because 399 of 435 districts depend on it.
SIGMA_TOTAL_MARGIN_HOUSE_LEAN = 11.0

# Local (independent) components — DERIVED, do not set by hand. Same identity
# as the Senate: sigma_total^2 = sigma_national^2 + sigma_local^2.
# Current values: sqrt(6^2 - 3^2) ≈ 5.20, sqrt(11^2 - 3^2) ≈ 10.58
SIGMA_LOCAL_MARGIN_HOUSE_POLLED = math.sqrt(
    SIGMA_TOTAL_MARGIN_HOUSE_POLLED**2 - SIGMA_NATIONAL_MARGIN_HOUSE**2)
SIGMA_LOCAL_MARGIN_HOUSE_LEAN = math.sqrt(
    SIGMA_TOTAL_MARGIN_HOUSE_LEAN**2 - SIGMA_NATIONAL_MARGIN_HOUSE**2)

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
N_SIMS = 1_000_000

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

# Margin threshold for calling a race a "toss-up" (i.e., within this many
# percentage points).
# (Samuel, 2026-07-11)
TOSSUP_MARGIN_THRESHOLD = 5.0

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
