"""
monte_carlo.py — turn senate_model.py's point estimates into probabilities.

Core idea: the adjusted margins are our best guess, but polls miss. We
simulate N_SIMS alternate election nights, each time perturbing every
race's margin with two kinds of error:

    simulated_margin = adjusted_margin + national_error + local_error

  * national_error: drawn ONCE per simulated election, added to every
    race with the same sign. This is the "all the polls missed the same
    direction" mode (2016, 2020). It's why five projected flips are NOT
    five independent coin flips — on a bad night, they fall together.
  * local_error: drawn once per race per simulation. Independent noise —
    bad samples, weird candidates, state-specific effects.

Sign convention (see calibration.py): margin = D - R, positive = D leads.
Nebraska: margin = Osborn(I) - Ricketts(R). Osborn's seat is counted
separately — the sim does NOT assume who he caucuses with.
"""

import numpy as np

from senate_model import predict_all_races, project_senate_control
from calibration import (
    SIGMA_NATIONAL_MARGIN,
    SIGMA_LOCAL_MARGIN,
    N_SIMS,
)

# Set to an int (e.g. 42) for reproducible runs; None = fresh randomness.
RANDOM_SEED = None


# ---------------------------------------------------------------------
# Step 1: reshape per-candidate predictions into per-race margins
# ---------------------------------------------------------------------
def build_races(predictions):
    """
    Group the per-candidate rows from predict_all_races() by state and
    compute one margin per race.

    Returns a list of dicts:
        {"state", "margin", "dem_name", "rep_name", "dem_party"}
    dem_party is 'D' normally, 'I' for Nebraska — kept so seat math can
    treat Osborn's seat as its own category.
    Races where we can't form a two-sided margin are skipped and reported.
    """
    by_state = {}
    for row in predictions:
        by_state.setdefault(row["state"], []).append(row)

    races, skipped = [], []
    for state, rows in by_state.items():
        rep = next((r for r in rows if r["party"] == "R"), None)
        # "Dem side" = the D candidate, or the I candidate if no D ran (NE).
        dem = next((r for r in rows if r["party"] == "D"), None)
        if dem is None:
            dem = next((r for r in rows if r["party"] == "I"), None)

        if dem is None or rep is None or dem["projected"] is None or rep["projected"] is None:
            skipped.append(state)
            continue

        races.append({
            "state": state,
            "margin": dem["projected"] - rep["projected"],
            "dem_name": dem["name"],
            "rep_name": rep["name"],
            "dem_party": dem["party"],   # 'D', or 'I' for Osborn
        })

    if skipped:
        print(f"⚠ Skipped (couldn't form a D-vs-R margin): {', '.join(skipped)}")
    return races


# ---------------------------------------------------------------------
# Step 2: derive the baseline seat counts from the model itself
# ---------------------------------------------------------------------
def baseline_seats(predictions, races):
    """
    Seats each party holds REGARDLESS of the simulated races: seats not
    up in 2026 plus unmodeled safe seats. Derived by taking the model's
    own deterministic control projection and subtracting the modeled
    races' deterministic winners — this keeps the sim consistent with
    whatever assumptions project_senate_control() already makes.
    """
    control = project_senate_control(predictions)

    modeled_states = {r["state"] for r in races}
    det_d = det_r = 0
    for row in predictions:
        if row["state"] in modeled_states and row.get("winner"):
            if row["party"] == "D":
                det_d += 1
            elif row["party"] == "R":
                det_r += 1
            # an 'I' deterministic winner belongs to neither baseline

    base_d = control["D"] - det_d
    base_r = control["R"] - det_r

    total = base_d + base_r + len(races)
    if total != 100:
        print(f"⚠ Seat math check: baseline D={base_d} + R={base_r} + "
              f"{len(races)} simulated races = {total}, expected 100. "
              f"(not_called={control['not_called']}) — verify before trusting output.")
    return base_d, base_r


# ---------------------------------------------------------------------
# Step 3: the simulation core (vectorized)
# ---------------------------------------------------------------------
def simulate(races, base_d, base_r, n_sims=N_SIMS, seed=RANDOM_SEED,
             sigma_national=SIGMA_NATIONAL_MARGIN, sigma_local=SIGMA_LOCAL_MARGIN):
    """
    Run n_sims simulated elections. Returns a results dict of plain
    data structures (no plotting here — the dashboard consumes this).
    """
    rng = np.random.default_rng(seed)
    margins = np.array([r["margin"] for r in races])      # shape: (n_races,)
    n_races = len(races)

    # One national error per SIMULATION — shape (n_sims, 1). The trailing
    # 1 is deliberate: when numpy adds a (n_sims, 1) array to a
    # (n_sims, n_races) array, it BROADCASTS — stretches the size-1 axis
    # by repeating the value across all n_races columns. So each row
    # (one simulated election) gets the SAME national error in every
    # race. That single broadcasting trick is what encodes correlation.
    national = rng.normal(0.0, sigma_national, size=(n_sims, 1))

    # One local error per race per simulation — shape (n_sims, n_races).
    local = rng.normal(0.0, sigma_local, size=(n_sims, n_races))

    # margins is (n_races,) and broadcasts across rows the same way.
    sim_margins = margins + national + local               # (n_sims, n_races)

    dem_side_wins = sim_margins > 0                        # bool, same shape

    # --- Per-race win probability: mean of the win indicator ----------
    win_prob = dem_side_wins.mean(axis=0)                  # (n_races,)

    # --- Seat math -----------------------------------------------------
    # Osborn's column (dem_party == 'I') counts toward neither party.
    is_true_d = np.array([r["dem_party"] == "D" for r in races])
    osborn_cols = ~is_true_d

    d_seats = base_d + dem_side_wins[:, is_true_d].sum(axis=1)   # (n_sims,)
    r_seats = base_r + (~dem_side_wins).sum(axis=1)              # R wins any race the dem side loses
    if osborn_cols.any():
        osborn_wins = dem_side_wins[:, osborn_cols].any(axis=1)  # (n_sims,) bool
    else:
        osborn_wins = np.zeros(n_sims, dtype=bool)

    # Control: R holds at 50 (Vance tiebreaker), so D needs 51 outright.
    p_d_majority = float((d_seats >= 51).mean())
    p_r_control = float((r_seats >= 50).mean())
    p_osborn_pivotal = float(((d_seats <= 50) & (r_seats <= 49)).mean())

    seat_values, seat_counts = np.unique(d_seats, return_counts=True)

    return {
        "races": [
            {**{k: r[k] for k in ("state", "margin", "dem_name", "rep_name", "dem_party")},
             "dem_side_win_prob": float(p)}
            for r, p in zip(races, win_prob)
        ],
        "p_d_majority": p_d_majority,
        "p_r_control": p_r_control,
        "p_osborn_pivotal": p_osborn_pivotal,
        "p_osborn_wins": float(osborn_wins.mean()),
        "seat_distribution": dict(zip(seat_values.tolist(), seat_counts.tolist())),
        "mean_d_seats": float(d_seats.mean()),
        "n_sims": n_sims,
    }


# ---------------------------------------------------------------------
# Sanity checks — run every time, per the build procedure
# ---------------------------------------------------------------------
def run_sanity_checks(races, base_d, base_r):
    print("\nSanity checks:")

    # 1. A margin-0 race must land ≈ 50%. Run with 200k sims so the MC
    #    standard error (~0.11pp) is far inside the ±0.5pp tolerance —
    #    otherwise ~1 in 20 seeds fails by pure luck, not by bug.
    fake = [{"state": "ZZ", "margin": 0.0, "dem_name": "x", "rep_name": "y", "dem_party": "D"}]
    p = simulate(fake, 0, 0, n_sims=200_000, seed=1)["races"][0]["dem_side_win_prob"]
    print(f"  [{'PASS' if abs(p - 0.5) < 0.005 else 'FAIL'}] margin-0 race → {p:.1%} (want ≈ 50%)")

    # 2. Killing the national term must NARROW the seat distribution
    #    (same total sigma, but no correlation → thinner tails).
    total = np.sqrt(SIGMA_NATIONAL_MARGIN**2 + SIGMA_LOCAL_MARGIN**2)
    split = simulate(races, base_d, base_r, seed=2)
    indep = simulate(races, base_d, base_r, seed=2, sigma_national=0.0, sigma_local=total)
    sd_split = _seat_sd(split["seat_distribution"])
    sd_indep = _seat_sd(indep["seat_distribution"])
    ok = sd_split > sd_indep
    print(f"  [{'PASS' if ok else 'FAIL'}] seat-count SD: correlated {sd_split:.2f} > independent {sd_indep:.2f}")

    # 3. Nudging every margin +1 toward D must raise P(D majority).
    nudged = [dict(r, margin=r["margin"] + 1.0) for r in races]
    p_base = simulate(races, base_d, base_r, seed=3)["p_d_majority"]
    p_nudged = simulate(nudged, base_d, base_r, seed=3)["p_d_majority"]
    print(f"  [{'PASS' if p_nudged > p_base else 'FAIL'}] +1pp D nudge: P(D maj) {p_base:.1%} → {p_nudged:.1%}")


def _seat_sd(dist):
    seats = np.array(list(dist.keys()), dtype=float)
    counts = np.array(list(dist.values()), dtype=float)
    mean = (seats * counts).sum() / counts.sum()
    return float(np.sqrt(((seats - mean) ** 2 * counts).sum() / counts.sum()))


# ---------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------
if __name__ == "__main__":
    predictions, climate, nominees_count = predict_all_races()
    races = build_races(predictions)
    base_d, base_r = baseline_seats(predictions, races)

    results = simulate(races, base_d, base_r)

    print(f"\nMonte Carlo — {results['n_sims']:,} simulated elections")
    print(f"Baseline seats (not up / unmodeled): D {base_d}, R {base_r}\n")

    for r in sorted(results["races"], key=lambda x: x["dem_side_win_prob"], reverse=True):
        who = r["dem_name"] if r["dem_party"] == "D" else f"{r['dem_name']} (I)"
        bar = "█" * round(r["dem_side_win_prob"] * 20)
        print(f"  {r['state']}  {r['dem_side_win_prob']:6.1%}  {bar:<20}  {who} vs {r['rep_name']}")

    print(f"\n  Mean D seats: {results['mean_d_seats']:.1f}")
    print(f"  P(D majority, ≥51):      {results['p_d_majority']:.1%}")
    print(f"  P(R control, ≥50 + VP):  {results['p_r_control']:.1%}")
    print(f"  P(Osborn wins NE):       {results['p_osborn_wins']:.1%}")
    print(f"  P(Osborn is the pivot):  {results['p_osborn_pivotal']:.1%}")

    run_sanity_checks(races, base_d, base_r)
