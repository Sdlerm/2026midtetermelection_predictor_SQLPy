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
1. PER-DISTRICT LOCAL SIGMA, IN THREE TIERS. The Senate uses one scalar
   sigma_local for every race because every race is polled. Only ~44 of 435
   districts here are. The rest sit on district_lean.csv, whose error is not
   polling error but "how wrong is 2022-vintage partisan lean about 2026."
   backtest_house.py measured that question in 2026-08 and found it is really
   two questions with very different answers: a lean that has merely AGED misses
   with SD ~8, while a lean whose district has been REDRAWN underneath it misses
   with SD ~16. So sigma_local is a VECTOR selected by has_polls AND
   lean_redrawn, and the third tier is not a refinement — it is twice the
   second, and it covers 81 districts.

2. CHUNKED SIMULATION. The Senate allocates the full (n_sims, n_races) error
   matrix at once. At 1,000,000 x 435 that array is 3.5 GB and the calculation
   needs two live simultaneously — 7 GB, on a 16 GB machine, to hold values
   that get reduced to a boolean immediately. This file streams the same
   computation in chunks of SIM_CHUNK_HOUSE and accumulates. The arithmetic is
   identical; only the memory profile differs.

WHAT THIS FILE DOES NOT FIX
---------------------------
Running a simulation does not make the inputs good. 391 of 435 districts are
lean-only, on 2022-vintage lean, 81 of them on boundaries that no longer exist.
The Monte Carlo propagates that uncertainty honestly — it cannot remove it. A
wide seat distribution here is the correct output, not a defect.

What CHANGED in 2026-08 is that the propagation is no longer guesswork: the lean
sigmas are measured against 2018/2020/2022 rather than argued from the Senate by
analogy, and validated bin by bin (backtest_house.py §4). Two things are still
not measured, and they bound how far these probabilities can be trusted —
SIGMA_TOTAL_MARGIN_HOUSE_POLLED, which has no district-poll archive to test
against, and the error on the national environment. That environment is now
read off the generic ballot (GENERIC_BALLOT_D, fetch_economics.py) when it is
stored, but SIGMA_NATIONAL_MARGIN_HOUSE was sized for the approval regression
it replaced and has not been re-measured. See calibration.py.
"""

import numpy as np

from house_model import predict_house_races, national_environment_margin
from calibration import (
    SIGMA_NATIONAL_MARGIN_HOUSE,
    SIGMA_LOCAL_MARGIN_HOUSE_POLLED,
    SIGMA_LOCAL_MARGIN_HOUSE_LEAN,
    SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN,
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
        {"race", "state", "district", "margin", "dem_name", "rep_name",
         "has_polls", "lean_redrawn"}
    has_polls and lean_redrawn ride along because between them they select which
    of THREE sigmas the district gets in simulate() — they are modeling inputs
    here, not just display flags.
    Districts where we can't form a two-sided margin are skipped and reported.
    """
    locked = locked_districts(predictions)

    by_race = {}
    for row in predictions:
        by_race.setdefault(row["race"], []).append(row)

    races, skipped = [], []
    for race in sorted(by_race):
        if race in locked:
            # Decided, not missing. It has no D-vs-R margin and never will, so
            # it is not simulated and must not be reported as a data gap — it
            # goes to baseline_seats() instead.
            continue
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
            # Whether this district's LEAN describes boundaries that no longer
            # exist. Only consulted for lean-only districts — see simulate().
            "lean_redrawn": bool(dem.get("lean_redrawn")),
        })

    if skipped:
        print(f"⚠ Skipped (couldn't form a D-vs-R margin): {', '.join(skipped)}")
    return races


# ---------------------------------------------------------------------
# Step 2: baseline seats
# ---------------------------------------------------------------------
def locked_districts(predictions):
    """
    Districts decided before a single simulation runs: {race: party}.

    A top-two state can send two candidates of the SAME party to the general
    (CA-07: Matsui vs. Vang, both Democrats). house_model.py flags those rows
    same_party_general and gives them no margin, because a D-minus-R margin is
    not a meaningful quantity in a race with no R. The seat is certain for the
    party; only the person is open.
    """
    return {row["race"]: row["party"]
            for row in predictions if row.get("same_party_general")}


def baseline_seats(predictions, races):
    """
    Seats held REGARDLESS of the simulated districts.

    Usually (0, 0): every House seat is up every cycle and house_model.py
    projects all 435, so there is normally no "not up" remainder for a baseline
    to carry. The Senate needs this function because only ~35 of its 100 seats
    are on the ballot.

    Same-party generals are the exception, and they are exactly what a baseline
    is for — a seat whose party is settled before the simulation, carried into
    every simulated election unchanged rather than drawn. Simulating one instead
    would be the real error: it would put error bars on a decided seat, and it
    could only do so by inventing an opponent for it to be uncertain against.
    That is what happened to CA-07 before same_party_general existed — the
    district fell through to the lean-only path and was simulated as a generic
    Democrat against a generic Republican who is not on the ballot.

    The signature now matches monte_carlo_senate.baseline_seats(predictions,
    races): both chambers derive their baseline from the predictions and check
    it against what is being simulated.

    It RAISES rather than warns, matching the Senate: a P(majority) computed
    over the wrong number of seats is not a degraded forecast, it is a false
    one, and 218 means nothing against a chamber that isn't 435. Refusing to
    return beats returning something unusable.
    """
    locked = locked_districts(predictions)
    base_d = sum(1 for p in locked.values() if p == "D")
    base_r = sum(1 for p in locked.values() if p == "R")

    n = len(races) + len(locked)
    if n != N_HOUSE_SEATS:
        raise ValueError(
            f"House seat identity violated: {len(races)} simulated districts + "
            f"{len(locked)} decided (same-party general) = {n}, expected "
            f"{N_HOUSE_SEATS}.\n"
            f"P(majority) is measured against the {HOUSE_MAJORITY}-seat "
            f"threshold, which is only meaningful for a full {N_HOUSE_SEATS}-"
            f"seat chamber.\n"
            f"Usual causes: districts dropped by build_races() for lacking a "
            f"two-sided margin (it prints them); a gap in district_lean.csv, "
            f"which defines the district universe (re-run "
            f"fetch_district_lean.py); or duplicate race keys collapsing rows."
        )
    return base_d, base_r


# ---------------------------------------------------------------------
# Step 3: the simulation core (vectorized, chunked)
# ---------------------------------------------------------------------
def simulate(races, base_d, base_r, n_sims=N_SIMS, seed=RANDOM_SEED,
             sigma_national=SIGMA_NATIONAL_MARGIN_HOUSE,
             sigma_local_polled=SIGMA_LOCAL_MARGIN_HOUSE_POLLED,
             sigma_local_lean=SIGMA_LOCAL_MARGIN_HOUSE_LEAN,
             sigma_local_lean_redrawn=SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN,
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
    # THREE tiers, not two, since backtest_house.py measured them apart:
    #
    #   polled                        5.20   poll error (still unbacktested)
    #   lean-only, lines intact       8.00   measured, 2018/2020/2022
    #   lean-only, lines redrawn     16.00   measured; roughly double
    #
    # A polled district in a redrawn state keeps the POLLED sigma. Its lean is
    # just as stale, but it reaches the projection at only (1-LEAN_ALPHA_HOUSE)
    # = 20% weight behind polls that were taken on the current boundaries, so
    # the stale geometry is a fifth of a fifth of the answer. Giving it the
    # redrawn sigma would price a whole error the polls have already displaced.
    has_polls = np.array([r["has_polls"] for r in races], dtype=bool)
    redrawn = np.array([r.get("lean_redrawn", False) for r in races], dtype=bool)
    sigma_local = np.where(
        has_polls,
        sigma_local_polled,
        np.where(redrawn, sigma_local_lean_redrawn, sigma_local_lean),
    )

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
                                  "dem_name", "rep_name", "has_polls",
                                  "lean_redrawn")},
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
        # The lean-only districts split again, and the split is the point: these
        # are the ones on boundaries that no longer exist, drawing double the
        # sigma of the rest. Reported so the dashboard can say how much of the
        # map is on the wide tier instead of implying one lean-only population.
        "n_lean_redrawn": int((~has_polls & redrawn).sum()),
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
             "dem_name": "x", "rep_name": "y", "has_polls": True,
             "lean_redrawn": False}]
    p = simulate(fake, 0, 0, n_sims=200_000, seed=1)["races"][0]["dem_win_prob"]
    print(f"  [{'PASS' if abs(p - 0.5) < 0.005 else 'FAIL'}] margin-0 district → {p:.1%} (want ≈ 50%)")

    # 2. Killing the national term must NARROW the seat distribution (same
    #    total sigma per district, but no correlation → thinner tails). This is
    #    the check that would catch the national error silently not applying,
    #    which is the failure mode that would make every probability too sharp.
    n_sims = 100_000
    tot_polled = np.sqrt(SIGMA_NATIONAL_MARGIN_HOUSE**2 + SIGMA_LOCAL_MARGIN_HOUSE_POLLED**2)
    tot_lean   = np.sqrt(SIGMA_NATIONAL_MARGIN_HOUSE**2 + SIGMA_LOCAL_MARGIN_HOUSE_LEAN**2)
    tot_redrawn = np.sqrt(SIGMA_NATIONAL_MARGIN_HOUSE**2
                          + SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN**2)
    split = simulate(races, base_d, base_r, n_sims=n_sims, seed=2)
    indep = simulate(races, base_d, base_r, n_sims=n_sims, seed=2, sigma_national=0.0,
                     sigma_local_polled=tot_polled, sigma_local_lean=tot_lean,
                     sigma_local_lean_redrawn=tot_redrawn)
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

    # 5. House-specific: at one margin, the three tiers must order strictly —
    #    polled more certain than lean-only, lean-only more certain than a lean
    #    on redrawn lines. Guards the vector-sigma wiring: if either has_polls or
    #    lean_redrawn ever stopped selecting a sigma, the order would collapse
    #    and this is what would notice. Ordering rather than exact values,
    #    because the values move whenever calibration.py is re-measured.
    pair = [
        {"race": "ZZ-01", "state": "ZZ", "district": "01", "margin": 6.0,
         "dem_name": "p", "rep_name": "q", "has_polls": True,
         "lean_redrawn": False},
        {"race": "ZZ-02", "state": "ZZ", "district": "02", "margin": 6.0,
         "dem_name": "r", "rep_name": "s", "has_polls": False,
         "lean_redrawn": False},
        {"race": "ZZ-03", "state": "ZZ", "district": "03", "margin": 6.0,
         "dem_name": "t", "rep_name": "u", "has_polls": False,
         "lean_redrawn": True},
    ]
    out = simulate(pair, 0, 0, n_sims=200_000, seed=5)["races"]
    p_polled, p_lean, p_redrawn = (r["dem_win_prob"] for r in out)
    ok = p_polled > p_lean > p_redrawn
    print(f"  [{'PASS' if ok else 'FAIL'}] same +6.0 margin, three tiers: polled "
          f"{p_polled:.1%} > lean {p_lean:.1%} > redrawn lean {p_redrawn:.1%}")


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
    base_d, base_r = baseline_seats(predictions, races)

    results = simulate(races, base_d, base_r)

    locked = locked_districts(predictions)
    print(f"\nMonte Carlo — {results['n_sims']:,} simulated elections")
    print(f"Districts: {len(races)} simulated  "
          f"({results['n_polled']} polled, {results['n_lean_only']} lean-only, "
          f"of which {results['n_lean_redrawn']} on redrawn lines — marked "
          f"'lean*' below and drawing double sigma)")
    if locked:
        print(f"           {len(locked)} decided, not simulated "
              f"(same-party general: {', '.join(f'{k} {v}' for k, v in sorted(locked.items()))})")
    print()

    # Itemize only the competitive districts. Printing 435 win-probability bars
    # would bury the ~40 that decide the chamber under 390 that read 0% or 100%.
    competitive = [r for r in results["races"] if 0.05 < r["dem_win_prob"] < 0.95]
    print(f"{'═'*72}\nCOMPETITIVE DISTRICTS — {len(competitive)} with P(D) between 5% and 95%\n{'═'*72}")
    for r in sorted(competitive, key=lambda x: x["dem_win_prob"], reverse=True):
        bar = "█" * round(r["dem_win_prob"] * 20)
        basis = ("polls" if r["has_polls"]
                 else "lean*" if r["lean_redrawn"] else "lean ")
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
    print(f"\n  {results['n_lean_only']} of {len(races)} districts are lean-only "
          f"({results['n_lean_redrawn']} of those on 2025-redrawn lines, drawing")
    print(f"  double sigma). Those sigmas are MEASURED — backtest_house.py, three")
    _env, _env_source = national_environment_margin()
    print(f"  cycles. National environment: D{_env:+.1f} from {_env_source}.")
    print(f"  See the House section of calibration.py.")
    print(f"{'─'*72}")

    run_sanity_checks(races, base_d, base_r)
