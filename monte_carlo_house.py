"""
monte_carlo_house.py — turn house_model.py's point estimates into probabilities.

Same core idea as monte_carlo_senate.py, and deliberately the same shape:

    simulated_margin = adjusted_margin + national_error + local_error

  * national_error: drawn ONCE per simulated election, added to every district
    with the same sign. The "all the polls missed the same direction" mode. In
    a 435-seat chamber this term dominates — it is what makes 54 toss-ups fall
    together instead of behaving like 54 independent coin flips.
  * local_error: drawn once per district per simulation. Independent noise.

Sign convention (see calibration.py): margin = D - R, positive = D leads.

TWO DELIBERATE DEPARTURES FROM THE SENATE VERSION
-------------------------------------------------
1. PER-DISTRICT LOCAL SIGMA. The Senate uses one scalar sigma_local for every
   race because every race is polled. Only ~36 of 435 districts here are. The
   other ~399 rest on district_lean.csv, whose error is not polling error but
   "how wrong is 2022-vintage partisan lean about 2026" — roughly double, per
   calibration.py. Feeding those districts the polled sigma would manufacture
   confidence the data cannot support, so sigma_local is a VECTOR, one entry
   per district, selected by has_polls.

2. CHUNKED SIMULATION. The Senate allocates the full (n_sims, n_races) error
   matrix at once. At 1,000,000 x 435 that array is 3.5 GB and the calculation
   needs two live simultaneously — 7 GB, on a 16 GB machine, to hold values
   that get reduced to a boolean immediately. This file streams the same
   computation in chunks of SIM_CHUNK_HOUSE and accumulates. The arithmetic is
   identical; only the memory profile differs.

WHAT THIS FILE DOES NOT FIX
---------------------------
Running a simulation does not make the inputs good. 399 of 435 districts are
lean-only, on 2022-vintage lean, 95 of them on boundaries that no longer exist.
The Monte Carlo propagates that uncertainty honestly — it cannot remove it. A
wide seat distribution here is the correct output, not a defect. And the House
sigmas in calibration.py are reasoned rather than backtested; treat every
probability below as order-of-magnitude until they are measured.
"""

import numpy as np

from house_model import predict_house_races
from calibration import (
    SIGMA_NATIONAL_MARGIN_HOUSE,
    SIGMA_LOCAL_MARGIN_HOUSE_POLLED,
    SIGMA_LOCAL_MARGIN_HOUSE_LEAN,
    N_SIMS,
    N_HOUSE_SEATS,
    HOUSE_MAJORITY,
    SIM_CHUNK_HOUSE,
)

# Set to an int (e.g. 42) for reproducible runs; None = fresh randomness.
RANDOM_SEED = None


# ---------------------------------------------------------------------
# Step 1: reshape per-candidate predictions into per-district margins
# ---------------------------------------------------------------------
def build_races(predictions):
    """
    Group the per-candidate rows from predict_house_races() by district and
    compute one margin per district.

    Returns a list of dicts:
        {"race", "state", "district", "margin", "dem_name", "rep_name", "has_polls"}
    has_polls rides along because it selects which sigma the district gets in
    simulate() — it is a modeling input here, not just a display flag.
    Districts where we can't form a two-sided margin are skipped and reported.
    """
    by_race = {}
    for row in predictions:
        by_race.setdefault(row["race"], []).append(row)

    races, skipped = [], []
    for race in sorted(by_race):
        rows = by_race[race]
        dem = next((r for r in rows if r["party"] == "D"), None)
        rep = next((r for r in rows if r["party"] == "R"), None)

        if dem is None or rep is None or dem["projected"] is None or rep["projected"] is None:
            skipped.append(race)
            continue

        races.append({
            "race":      race,
            "state":     dem["state"],
            "district":  dem["district"],
            "margin":    dem["projected"] - rep["projected"],
            "dem_name":  dem["name"],
            "rep_name":  rep["name"],
            # Both sides of a district share a basis; either row answers this.
            "has_polls": bool(dem.get("has_polls")),
        })

    if skipped:
        print(f"⚠ Skipped (couldn't form a D-vs-R margin): {', '.join(skipped)}")
    return races


# ---------------------------------------------------------------------
# Step 2: baseline seats
# ---------------------------------------------------------------------
def baseline_seats(races):
    """
    Seats held REGARDLESS of the simulated districts.

    For the House this is always (0, 0): every seat is up every cycle and
    house_model.py projects all 435, so there is no "not up" or "unmodeled"
    remainder for a baseline to carry. The Senate needs this function because
    only ~35 of its 100 seats are on the ballot.

    The function exists anyway — for the seat-math check. If the district count
    ever drifts from 435 (a roster gap, a skipped district, a duplicate race
    key), the totals would silently stop summing to a chamber and every
    probability would be quietly wrong. This is where that gets caught.

    It RAISES rather than warns, matching monte_carlo_senate.baseline_seats: a
    P(majority) computed over the wrong number of seats is not a degraded
    forecast, it is a false one, and 218 means nothing against a chamber that
    isn't 435. Refusing to return beats returning something unusable.
    """
    n = len(races)
    if n != N_HOUSE_SEATS:
        raise ValueError(
            f"House seat identity violated: {n} simulated districts, expected "
            f"{N_HOUSE_SEATS}.\n"
            f"P(majority) is measured against the {HOUSE_MAJORITY}-seat "
            f"threshold, which is only meaningful for a full {N_HOUSE_SEATS}-"
            f"seat chamber.\n"
            f"Usual causes: districts dropped by build_races() for lacking a "
            f"two-sided margin (it prints them); a gap in district_lean.csv, "
            f"which defines the district universe (re-run "
            f"fetch_district_lean.py); or duplicate race keys collapsing rows."
        )
    return 0, 0


# ---------------------------------------------------------------------
# Step 3: the simulation core (vectorized, chunked)
# ---------------------------------------------------------------------
def simulate(races, base_d, base_r, n_sims=N_SIMS, seed=RANDOM_SEED,
             sigma_national=SIGMA_NATIONAL_MARGIN_HOUSE,
             sigma_local_polled=SIGMA_LOCAL_MARGIN_HOUSE_POLLED,
             sigma_local_lean=SIGMA_LOCAL_MARGIN_HOUSE_LEAN,
             chunk_size=SIM_CHUNK_HOUSE):
    """
    Run n_sims simulated elections. Returns a results dict of plain data
    structures (no plotting here — the dashboard consumes this).

    Reproducibility note: a given (seed, chunk_size) pair reproduces exactly,
    but changing chunk_size changes how draws are partitioned across calls and
    therefore changes the stream. Chunk size is a memory knob that is visible in
    the output — hence it lives in calibration.py rather than being picked here.
    """
    rng = np.random.default_rng(seed)
    margins = np.array([r["margin"] for r in races], dtype=np.float64)
    n_races = len(races)

    # Per-district local sigma — the vector that replaces the Senate's scalar.
    has_polls = np.array([r["has_polls"] for r in races], dtype=bool)
    sigma_local = np.where(has_polls, sigma_local_polled, sigma_local_lean)

    win_counts = np.zeros(n_races, dtype=np.int64)
    d_seats = np.empty(n_sims, dtype=np.int32)

    done = 0
    while done < n_sims:
        k = min(chunk_size, n_sims - done)

        # One national error per SIMULATION — shape (k, 1). The trailing 1 is
        # deliberate: broadcasting against (k, n_races) repeats the value across
        # every district, so each simulated election gets the SAME national
        # error everywhere. That single broadcast is what encodes correlation.
        national = rng.normal(0.0, sigma_national, size=(k, 1))

        # Standard normals scaled by the per-district sigma vector. Drawing
        # standard and scaling (rather than passing sigma= directly) is what
        # lets each column carry its own sigma in one call.
        sim = rng.standard_normal(size=(k, n_races))
        sim *= sigma_local          # in-place: (n_races,) broadcasts across rows
        sim += national             # in-place: (k, 1) broadcasts across columns
        sim += margins              # in-place: the point estimates

        # From here only the sign matters, so collapse to bool immediately —
        # this is why the float buffer never needs to outlive its chunk.
        dem_wins = sim > 0
        win_counts += dem_wins.sum(axis=0)
        d_seats[done:done + k] = base_d + dem_wins.sum(axis=1)

        done += k

    win_prob = win_counts / float(n_sims)

    # R takes every district D loses. 435 is odd, so d + r = 435 always and
    # exactly one party clears 218 — no tie, no VP tiebreaker, and P(R) is the
    # exact complement of P(D) rather than a separately estimated quantity.
    r_seats = base_r + (N_HOUSE_SEATS - base_d - base_r) - (d_seats - base_d)

    p_d_majority = float((d_seats >= HOUSE_MAJORITY).mean())

    seat_values, seat_counts = np.unique(d_seats, return_counts=True)

    return {
        "races": [
            {**{k: r[k] for k in ("race", "state", "district", "margin",
                                  "dem_name", "rep_name", "has_polls")},
             "dem_win_prob": float(p)}
            for r, p in zip(races, win_prob)
        ],
        "p_d_majority": p_d_majority,
        "p_r_majority": 1.0 - p_d_majority,
        "seat_distribution": dict(zip(seat_values.tolist(), seat_counts.tolist())),
        "mean_d_seats": float(d_seats.mean()),
        "median_d_seats": float(np.median(d_seats)),
        "d_seats_p05": float(np.percentile(d_seats, 5)),
        "d_seats_p95": float(np.percentile(d_seats, 95)),
        "mean_r_seats": float(r_seats.mean()),
        "n_polled": int(has_polls.sum()),
        "n_lean_only": int((~has_polls).sum()),
        "n_sims": n_sims,
    }


# ---------------------------------------------------------------------
# Sanity checks — run every time, per the build procedure
# ---------------------------------------------------------------------
def run_sanity_checks(races, base_d, base_r):
    print("\nSanity checks:")

    # 1. A margin-0 district must land ≈ 50%. 200k sims puts the MC standard
    #    error (~0.11pp) far inside the ±0.5pp tolerance, so a passing check
    #    means the math is right rather than the seed being lucky.
    fake = [{"race": "ZZ-01", "state": "ZZ", "district": "01", "margin": 0.0,
             "dem_name": "x", "rep_name": "y", "has_polls": True}]
    p = simulate(fake, 0, 0, n_sims=200_000, seed=1)["races"][0]["dem_win_prob"]
    print(f"  [{'PASS' if abs(p - 0.5) < 0.005 else 'FAIL'}] margin-0 district → {p:.1%} (want ≈ 50%)")

    # 2. Killing the national term must NARROW the seat distribution (same
    #    total sigma per district, but no correlation → thinner tails). This is
    #    the check that would catch the national error silently not applying,
    #    which is the failure mode that would make every probability too sharp.
    n_sims = 100_000
    tot_polled = np.sqrt(SIGMA_NATIONAL_MARGIN_HOUSE**2 + SIGMA_LOCAL_MARGIN_HOUSE_POLLED**2)
    tot_lean   = np.sqrt(SIGMA_NATIONAL_MARGIN_HOUSE**2 + SIGMA_LOCAL_MARGIN_HOUSE_LEAN**2)
    split = simulate(races, base_d, base_r, n_sims=n_sims, seed=2)
    indep = simulate(races, base_d, base_r, n_sims=n_sims, seed=2, sigma_national=0.0,
                     sigma_local_polled=tot_polled, sigma_local_lean=tot_lean)
    sd_split = _seat_sd(split["seat_distribution"])
    sd_indep = _seat_sd(indep["seat_distribution"])
    ok = sd_split > sd_indep
    print(f"  [{'PASS' if ok else 'FAIL'}] seat-count SD: correlated {sd_split:.2f} > independent {sd_indep:.2f}")

    # 3. Nudging every margin +1 toward D must raise P(D majority).
    nudged = [dict(r, margin=r["margin"] + 1.0) for r in races]
    p_base = simulate(races, base_d, base_r, n_sims=n_sims, seed=3)["p_d_majority"]
    p_nudged = simulate(nudged, base_d, base_r, n_sims=n_sims, seed=3)["p_d_majority"]
    print(f"  [{'PASS' if p_nudged > p_base else 'FAIL'}] +1pp D nudge: P(D maj) {p_base:.1%} → {p_nudged:.1%}")

    # 4. House-specific: chunking must not change the answer. Same seed, two
    #    chunk sizes — the streams differ, so this asserts agreement within MC
    #    error, not equality. Without it, a chunk-boundary bug (double-counting
    #    a chunk, dropping the remainder) would look like a modeling result.
    a = simulate(races, base_d, base_r, n_sims=n_sims, seed=4, chunk_size=7_000)
    b = simulate(races, base_d, base_r, n_sims=n_sims, seed=4, chunk_size=50_000)
    delta = abs(a["mean_d_seats"] - b["mean_d_seats"])
    print(f"  [{'PASS' if delta < 0.5 else 'FAIL'}] chunk invariance: mean D seats "
          f"{a['mean_d_seats']:.2f} vs {b['mean_d_seats']:.2f} (Δ {delta:.3f})")

    # 5. House-specific: lean-only districts must be less certain than polled
    #    ones at the same margin. Guards the vector-sigma wiring — if has_polls
    #    ever stopped selecting the sigma, this is what would notice.
    pair = [
        {"race": "ZZ-01", "state": "ZZ", "district": "01", "margin": 6.0,
         "dem_name": "p", "rep_name": "q", "has_polls": True},
        {"race": "ZZ-02", "state": "ZZ", "district": "02", "margin": 6.0,
         "dem_name": "r", "rep_name": "s", "has_polls": False},
    ]
    out = simulate(pair, 0, 0, n_sims=200_000, seed=5)["races"]
    p_polled, p_lean = out[0]["dem_win_prob"], out[1]["dem_win_prob"]
    print(f"  [{'PASS' if p_polled > p_lean else 'FAIL'}] same +6.0 margin: polled "
          f"{p_polled:.1%} > lean-only {p_lean:.1%}")


def _seat_sd(dist):
    seats = np.array(list(dist.keys()), dtype=float)
    counts = np.array(list(dist.values()), dtype=float)
    mean = (seats * counts).sum() / counts.sum()
    return float(np.sqrt(((seats - mean) ** 2 * counts).sum() / counts.sum()))


# ---------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------
if __name__ == "__main__":
    predictions, climate, approval = predict_house_races()
    races = build_races(predictions)
    base_d, base_r = baseline_seats(races)

    results = simulate(races, base_d, base_r)

    print(f"\nMonte Carlo — {results['n_sims']:,} simulated elections")
    print(f"Districts: {len(races)}  "
          f"({results['n_polled']} polled, {results['n_lean_only']} lean-only)\n")

    # Itemize only the competitive districts. Printing 435 win-probability bars
    # would bury the ~40 that decide the chamber under 390 that read 0% or 100%.
    competitive = [r for r in results["races"] if 0.05 < r["dem_win_prob"] < 0.95]
    print(f"{'═'*72}\nCOMPETITIVE DISTRICTS — {len(competitive)} with P(D) between 5% and 95%\n{'═'*72}")
    for r in sorted(competitive, key=lambda x: x["dem_win_prob"], reverse=True):
        bar = "█" * round(r["dem_win_prob"] * 20)
        basis = "polls" if r["has_polls"] else "lean "
        print(f"  {r['race']}  {r['dem_win_prob']:6.1%}  {bar:<20}  [{basis}] "
              f"{r['margin']:+.1f}  {r['dem_name']} vs {r['rep_name']}")

    safe_d = sum(1 for r in results["races"] if r["dem_win_prob"] >= 0.95)
    safe_r = sum(1 for r in results["races"] if r["dem_win_prob"] <= 0.05)
    print(f"\n  {safe_d} district(s) at P(D) ≥ 95%, {safe_r} at ≤ 5% — not itemized.")

    print(f"\n{'─'*72}")
    print(f"  Mean D seats:   {results['mean_d_seats']:.1f}   "
          f"(median {results['median_d_seats']:.0f}, "
          f"90% range {results['d_seats_p05']:.0f}–{results['d_seats_p95']:.0f})")
    print(f"  Mean R seats:   {results['mean_r_seats']:.1f}")
    print(f"  P(D majority, ≥{HOUSE_MAJORITY}):  {results['p_d_majority']:.1%}")
    print(f"  P(R majority, ≥{HOUSE_MAJORITY}):  {results['p_r_majority']:.1%}")
    print(f"\n  {results['n_lean_only']} of {len(races)} districts are lean-only, on")
    print(f"  2022-vintage lean. The House sigmas are reasoned, not backtested —")
    print(f"  see the House section of calibration.py before quoting these.")
    print(f"{'─'*72}")

    run_sanity_checks(races, base_d, base_r)
