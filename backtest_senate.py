"""
backtest_senate.py — measure the poll/lean blend weight against real outcomes.

THE QUESTION
------------
senate_model.LEAN_ALPHA = 0.80 says "trust the weighted poll average 80%, trust
the structural lean 20%." That number was chosen, not measured. This script
rebuilds what the model would have projected on the eve of the 2018 and 2020
Senate elections, sweeps alpha from 0 to 1, and reports which value minimizes
error against what actually happened.

WHAT IS BEING SCORED
--------------------
Margins, on the D-minus-R scale calibration.py defines:

    predicted_margin(a) = a * poll_margin + (1 - a) * lean_margin
    error(a)            = predicted_margin(a) - actual_margin

Both legs are margins built from the same per-candidate quantities the live
model uses, which is what makes the sweep a statement about the model rather
than about a reimplementation of it: poll_margin comes from
senate_model.weighted_average_and_stderr (credibility x recency decay, per-race
F-grade exclusion) evaluated with as_of = election day, and lean_margin comes
from senate_model.lean_baseline reading the same data/state_lean.csv.

WHAT IS DELIBERATELY LEFT OUT
-----------------------------
The econ and approval adjustments. climate_factors holds 2026 only, so
reproducing them for 2018/2020 would mean backfilling FRED and approval history
— and they are ADDITIVE terms, identical across every candidate of a party, so
they shift every alpha's error by the same constant and cannot move the argmin.
They would change the reported bias, which is why bias is also reported with a
fitted intercept: that column is what the diagnostics would look like if a
national term had been free to absorb the mean miss.

THREE HONEST LIMITS — read before quoting any number here
---------------------------------------------------------
1. TWO CYCLES, NOT FOUR. 538's polls-page CSVs died with 538 in 2025 and now
   serve HTML; the surviving git-scraped mirror stops in April 2021. 2018 and
   2020 are complete, 2022 and 2024 are not recoverable from any mirror found.
   See load_historical_polls.py. Sixty races is a usable sample for a per-race
   blend weight and a useless one for per-cycle error (n=2).

2. THE LEAN LEG IS FLATTERED BY LOOKAHEAD, and this cuts directly at the
   question. data/state_lean.csv is a single undated snapshot in the working
   tree today; whatever elections it was built from, they include the ones being
   predicted here. A lean that already knows 2018 and 2020 outcomes performs
   better in this backtest than a genuine 2018-vintage lean would have, which
   pushes the measured optimum DOWNWARD (toward the lean). So the alpha reported
   below is a LOWER BOUND on the alpha that would have been right at the time.
   That asymmetry is usable: if the optimum lands at or above 0.80, the
   conclusion "0.80 is not too high" survives the bias. If it lands below, the
   result is confounded and cannot settle the question on its own.

3. POLLSTER GRADES LEAK TOO, in the other direction. The grades attached to
   historical rows are the mirror's April 2021 vintage, so they encode 2018 and
   2020 pollster performance and flatter the POLL leg. The equal-credibility run
   below is the lookahead-free floor for that leg; the gap between the two runs
   is the size of the leak.
"""

import argparse
import statistics
from collections import defaultdict

from init_db import get_connection
from senate_model import (
    LEAN_ALPHA,
    lean_baseline,
    load_state_lean,
    weighted_average_and_stderr,
)

# Election day per cycle — the as_of date the whole backtest hangs on. A poll
# after this date is future information and recency_weight zeroes it.
ELECTION_DAY = {2018: "2018-11-06", 2020: "2020-11-03"}

ALPHA_GRID = [i / 100 for i in range(0, 101)]


def load_actuals(year):
    """
    {state: (d_share, r_share, actual_margin)} from historical_results.

    An independent who is the only non-Republican in the race takes the D slot:
    Al Gross (AK-2020) and Ricky Harrington (AR-2020) are the two cases, and
    both were the sole opposition to a Republican incumbent, so scoring them on
    the D-minus-R axis is the only reading that preserves the sign convention.
    Races that cannot be reduced to two sides are skipped and reported.
    """
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT r.state, c.party, h.vote_share
        FROM races r
        JOIN candidates c ON c.race_id = r.id
        JOIN historical_results h ON h.candidate_id = c.id
        WHERE r.year = ? AND r.district = ''
        """,
        (year,),
    )
    rows = cur.fetchall()
    con.close()

    by_state = defaultdict(dict)
    for state, party, share in rows:
        # Keep the strongest candidate per party — CA-2018 was D-vs-D, so the
        # weaker D is the de facto opposition and must not overwrite the winner.
        by_state[state][party] = max(share, by_state[state].get(party, 0.0))

    actuals, unusable = {}, []
    for state, shares in by_state.items():
        left = shares.get("D", shares.get("I"))
        right = shares.get("R")
        if left is None or right is None:
            unusable.append(state)
            continue
        actuals[state] = (left, right, left - right)
    return actuals, unusable


def race_rows(year):
    """(state, race_id, {party: candidate_id}) for each year's races, general-
    election candidates only (those carrying a historical_results row)."""
    con = get_connection()
    cur = con.cursor()
    cur.execute(
        """
        SELECT r.state, r.id, c.party, c.id, h.vote_share
        FROM races r
        JOIN candidates c ON c.race_id = r.id
        JOIN historical_results h ON h.candidate_id = c.id
        WHERE r.year = ? AND r.district = ''
        """,
        (year,),
    )
    rows = cur.fetchall()
    con.close()

    grouped = defaultdict(lambda: [None, {}])
    for state, race_id, party, cand_id, share in rows:
        slot = grouped[state]
        slot[0] = race_id
        # Same strongest-per-party rule as load_actuals, so the candidate whose
        # polls are averaged is the same one whose result is scored.
        if party not in slot[1] or share > slot[1][party][1]:
            slot[1][party] = (cand_id, share)
    return {s: (rid, {p: cid for p, (cid, _sh) in cands.items()})
            for s, (rid, cands) in grouped.items()}


class flattened_credibility:
    """
    Context manager: every pollster's credibility is 1.0 inside the block, and
    exactly what it was outside it.

    This has to COMMIT rather than hold an open transaction.
    weighted_average_and_stderr opens its own connection per call, and SQLite
    gives a second connection no view of the first's uncommitted writes — an
    earlier version of this used BEGIN/ROLLBACK and silently measured nothing,
    producing an equal-credibility run numerically identical to the weighted one.
    Identical output was the only symptom.

    Because it commits, restoration is verified rather than assumed: the exit
    path re-reads the table and raises if the checksum does not match what went
    in, so a failed restore can never masquerade as a working database. If that
    ever fires, `python load_pollster_ratings.py` rebuilds the graded rows from
    data/pollster_ratings.csv.
    """

    def __init__(self, active=True):
        self.active = active

    def _snapshot(self):
        con = get_connection()
        rows = con.execute("SELECT id, credibility FROM pollsters").fetchall()
        con.close()
        return rows

    def __enter__(self):
        if not self.active:
            return self
        self.saved = self._snapshot()
        con = get_connection()
        con.execute("UPDATE pollsters SET credibility = 1.0")
        con.commit()
        con.close()
        return self

    def __exit__(self, *exc):
        if not self.active:
            return False
        con = get_connection()
        con.executemany("UPDATE pollsters SET credibility = ? WHERE id = ?",
                        [(c, i) for i, c in self.saved])
        con.commit()
        con.close()
        if self._snapshot() != self.saved:
            raise RuntimeError(
                "Pollster credibilities were NOT restored after the "
                "equal-credibility run. The live 2026 forecast reads this "
                "column. Run `python load_pollster_ratings.py` to rebuild it."
            )
        return False


def build_observations(equal_credibility=False):
    """
    One row per backtestable race: poll margin, lean margin, actual margin.

    equal_credibility flattens every pollster to 1.0 for the duration, which is
    the lookahead-free version of the poll leg (see limit 3 in the module
    docstring). See flattened_credibility for why that needs a committed write
    and a verified restore.
    """
    state_lean = load_state_lean()
    observations, skipped = [], []

    with flattened_credibility(equal_credibility):
        for year, as_of in sorted(ELECTION_DAY.items()):
            actuals, unusable = load_actuals(year)
            for state in unusable:
                skipped.append((year, state, "no two-sided result"))

            for state, (race_id, cands) in sorted(race_rows(year).items()):
                if state not in actuals:
                    continue

                left_party = "D" if "D" in cands else ("I" if "I" in cands else None)
                if left_party is None or "R" not in cands:
                    skipped.append((year, state, "no two-sided candidate pair"))
                    continue

                left_avg, _se = weighted_average_and_stderr(
                    race_id, cands[left_party], as_of=as_of)
                right_avg, _se = weighted_average_and_stderr(
                    race_id, cands["R"], as_of=as_of)
                if left_avg is None or right_avg is None:
                    skipped.append((year, state, "no polls for one or both sides"))
                    continue

                # lean_baseline maps 'I' through INDIE_CAUCUS; for these two
                # races that resolves to the D side, matching load_actuals.
                lean_left = lean_baseline(state, left_party, state_lean)
                lean_right = lean_baseline(state, "R", state_lean)

                observations.append({
                    "year": year, "state": state,
                    "poll_margin": left_avg - right_avg,
                    "lean_margin": lean_left - lean_right,
                    "actual_margin": actuals[state][2],
                })

    return observations, skipped


def errors_at(observations, alpha):
    out = []
    for o in observations:
        pred = alpha * o["poll_margin"] + (1 - alpha) * o["lean_margin"]
        out.append(pred - o["actual_margin"])
    return out


def score(observations, alpha):
    errs = errors_at(observations, alpha)
    n = len(errs)
    mean = sum(errs) / n
    rmse = (sum(e * e for e in errs) / n) ** 0.5
    mae = sum(abs(e) for e in errs) / n
    # RMSE after removing the mean error: what the spread would be if a national
    # term (econ/approval, or a generic-ballot shift) had absorbed the bias.
    centered = (sum((e - mean) ** 2 for e in errs) / n) ** 0.5
    return {"alpha": alpha, "n": n, "bias": mean, "rmse": rmse, "mae": mae,
            "rmse_debiased": centered}


def sweep(observations):
    return [score(observations, a) for a in ALPHA_GRID]


def best(results, key="rmse"):
    return min(results, key=lambda r: r[key])


def plateau(results, key="rmse", tolerance=0.01):
    """
    The range of alpha within `tolerance` (relative) of the best score.

    Reported because the argmin alone oversells the result: with 60 races the
    RMSE curve in alpha is shallow, and "0.62 is optimal" means much less than
    "anything from 0.55 to 0.78 is statistically indistinguishable." A tuning
    decision should be made against the plateau, not the point.
    """
    floor = best(results, key)[key]
    inside = [r["alpha"] for r in results if r[key] <= floor * (1 + tolerance)]
    return min(inside), max(inside)


def bootstrap_best_alpha(observations, n_boot=2000, seed=20260810):
    """
    Resample races with replacement, re-find the argmin, and return the spread.

    This is the honest uncertainty on the headline number: it answers "if the
    2018/2020 Senate map had shuffled slightly, would the optimum still land
    near here?" Uses random.Random so numpy's global state stays untouched.
    """
    import random
    rng = random.Random(seed)
    n = len(observations)
    alphas = []
    for _ in range(n_boot):
        sample = [observations[rng.randrange(n)] for _ in range(n)]
        alphas.append(best(sweep(sample))["alpha"])
    alphas.sort()
    return {
        "median": alphas[n_boot // 2],
        "p05": alphas[int(0.05 * n_boot)],
        "p95": alphas[int(0.95 * n_boot)],
        "share_above_current": sum(1 for a in alphas if a >= LEAN_ALPHA) / n_boot,
    }


def lean_vintage_check():
    """
    How well data/state_lean.csv predicts each cycle's actual margins.

    This is not a side note, it decides how the sweep can be read. The file is a
    single undated snapshot, so the only way to learn what era it describes is to
    score it against every cycle and see where it fits. If it fits the RECENT
    cycles best, then it is lookahead-flattered for the years being backtested
    and any alpha measured against it is biased downward. If it misfits an early
    cycle badly, the lean leg is being handicapped there instead and alpha is
    biased upward. The two can happen in the SAME sweep, in opposite directions,
    which is exactly the case here.
    """
    state_lean = load_state_lean()
    con = get_connection()
    cur = con.cursor()
    out = []
    for year in (2018, 2020, 2022, 2024):
        cur.execute(
            """
            SELECT r.state, c.party, h.vote_share
            FROM races r JOIN candidates c ON c.race_id = r.id
            JOIN historical_results h ON h.candidate_id = c.id
            WHERE r.year = ? AND r.district = ''
            """, (year,))
        by_state = defaultdict(dict)
        for state, party, share in cur.fetchall():
            by_state[state][party] = max(share, by_state[state].get(party, 0.0))

        pairs = []
        for state, shares in by_state.items():
            left = shares.get("D", shares.get("I"))
            right = shares.get("R")
            if left is None or right is None or state not in state_lean:
                continue
            pairs.append((state_lean[state], left - right))
        if not pairs:
            continue
        n = len(pairs)
        offset = sum(y - x for x, y in pairs) / n
        rmse = (sum((y - x) ** 2 for x, y in pairs) / n) ** 0.5
        out.append({"year": year, "n": n, "offset": offset, "rmse": rmse})
    con.close()
    return out


def _fmt_row(r):
    return (f"  a={r['alpha']:.2f}   RMSE {r['rmse']:5.2f}   MAE {r['mae']:5.2f}   "
            f"bias {r['bias']:+5.2f}   RMSE(debiased) {r['rmse_debiased']:5.2f}")


def report(equal_credibility=False, n_boot=2000):
    label = "EQUAL CREDIBILITY (lookahead-free poll leg)" if equal_credibility \
        else "MODEL WEIGHTING (credibility x recency)"

    observations, skipped = build_observations(equal_credibility)
    if not observations:
        raise SystemExit(
            "No backtestable races. Run load_historical_polls.py first — the "
            "polls table has 2026 rows only until it has been loaded."
        )

    results = sweep(observations)
    b_rmse, b_mae = best(results, "rmse"), best(results, "mae")
    lo, hi = plateau(results, "rmse")
    current = score(observations, LEAN_ALPHA)
    poll_only, lean_only = score(observations, 1.0), score(observations, 0.0)

    print(f"\n{'='*74}\nSENATE POLL-WEIGHT BACKTEST — {label}\n{'='*74}")
    by_year = defaultdict(int)
    for o in observations:
        by_year[o["year"]] += 1
    print(f"  Races scored: {len(observations)}  "
          f"({', '.join(f'{y}: {n}' for y, n in sorted(by_year.items()))})")
    if skipped:
        print(f"  Skipped {len(skipped)}: "
              + ", ".join(f"{y} {s} ({why})" for y, s, why in skipped[:6])
              + (" ..." if len(skipped) > 6 else ""))

    print(f"\n  {'-'*70}\n  ENDPOINTS AND THE CURRENT SETTING\n  {'-'*70}")
    print("  lean only  " + _fmt_row(lean_only)[2:])
    print("  polls only " + _fmt_row(poll_only)[2:])
    print(f"  CURRENT    " + _fmt_row(current)[2:] + f"   <- LEAN_ALPHA")

    print(f"\n  {'-'*70}\n  OPTIMUM\n  {'-'*70}")
    print("  best RMSE  " + _fmt_row(b_rmse)[2:])
    print("  best MAE   " + _fmt_row(b_mae)[2:])
    print(f"  RMSE plateau (within 1% of best): alpha {lo:.2f} - {hi:.2f}")
    print(f"  RMSE at current {LEAN_ALPHA:.2f}: {current['rmse']:.3f}  vs  best "
          f"{b_rmse['rmse']:.3f}  (gap {current['rmse'] - b_rmse['rmse']:+.3f} pts of margin)")

    # The distinction that decides the tuning question. Raw RMSE lets a low alpha
    # win by CANCELLING two unrelated biases (see the per-cycle block below);
    # debiased RMSE asks the question the blend weight actually controls, which is
    # "which mix has the least scatter once any national miss is removed". Bias is
    # what SIGMA_NATIONAL_MARGIN and the econ/approval terms exist to handle — a
    # blend weight tuned to absorb it is the wrong knob doing the wrong job.
    b_var = best(results, "rmse_debiased")
    var_lo, var_hi = plateau(results, "rmse_debiased")
    print(f"\n  VARIANCE-OPTIMAL alpha (bias removed): {b_var['alpha']:.2f}  "
          f"RMSE(debiased) {b_var['rmse_debiased']:.2f}   plateau "
          f"{var_lo:.2f} - {var_hi:.2f}")
    print(f"  Current {LEAN_ALPHA:.2f} debiased RMSE {current['rmse_debiased']:.2f} "
          f"(gap {current['rmse_debiased'] - b_var['rmse_debiased']:+.3f})"
          + ("  <- current setting is inside the variance-optimal plateau"
             if var_lo <= LEAN_ALPHA <= var_hi else
             "  <- current setting is OUTSIDE the variance-optimal plateau"))

    boot = bootstrap_best_alpha(observations, n_boot=n_boot)
    print(f"\n  {'-'*70}\n  UNCERTAINTY ON THE OPTIMUM ({n_boot} race-level bootstraps)\n  {'-'*70}")
    print(f"  best alpha: median {boot['median']:.2f}, "
          f"90% interval {boot['p05']:.2f} - {boot['p95']:.2f}")
    print(f"  P(optimal alpha >= current {LEAN_ALPHA:.2f}) = {boot['share_above_current']:.0%}")

    print(f"\n  {'-'*70}\n  CURVE\n  {'-'*70}")
    for r in results:
        if round(r["alpha"] * 100) % 10 == 0:
            mark = "  <-- best" if abs(r["alpha"] - b_rmse["alpha"]) < 1e-9 else ""
            print(_fmt_row(r) + mark)

    print(f"\n  {'-'*70}\n  PER-CYCLE (sign of bias = model favored D)\n  {'-'*70}")
    for year in sorted(by_year):
        subset = [o for o in observations if o["year"] == year]
        s_cur = score(subset, LEAN_ALPHA)
        s_best = score(subset, b_rmse["alpha"])
        own = best(sweep(subset), "rmse")
        print(f"  {year}: n={s_cur['n']:>2}  at a={LEAN_ALPHA:.2f} bias {s_cur['bias']:+5.2f} "
              f"RMSE {s_cur['rmse']:5.2f}   |   at a={b_rmse['alpha']:.2f} bias "
              f"{s_best['bias']:+5.2f} RMSE {s_best['rmse']:5.2f}   |   own best "
              f"a={own['alpha']:.2f} (RMSE {own['rmse']:.2f})")
    print(f"  If the two cycles' own optima disagree, the pooled optimum is a "
          f"compromise between two different years, not a property of polling.")

    print(f"\n  {'-'*70}\n  LEAN VINTAGE — which era does state_lean.csv describe?\n  {'-'*70}")
    for v in lean_vintage_check():
        tag = "  <- backtested here" if v["year"] in ELECTION_DAY else ""
        print(f"  {v['year']}: n={v['n']:>2}  lean-as-prediction RMSE {v['rmse']:5.2f}  "
              f"mean(actual - lean) {v['offset']:+6.2f}{tag}")
    print(f"  A near-zero offset and low RMSE means the file already knows that "
          f"cycle. Where the offset is large, the lean is stale in that direction "
          f"and the lean leg is being handicapped, not flattered.")

    print(f"\n  {'-'*70}\n  IMPLIED SIGMA (margin scale), for calibration.py\n  {'-'*70}")
    errs_cur = errors_at(observations, LEAN_ALPHA)
    print(f"  SD of residuals at a={LEAN_ALPHA:.2f}: {statistics.stdev(errs_cur):.2f}   "
          f"(SIGMA_TOTAL_MARGIN is currently 5.2, sourced from the literature)")
    print(f"  Only two cycles are present, so this does NOT license a change to "
          f"SIGMA_NATIONAL_MARGIN — that constant is the SD of per-cycle bias and "
          f"needs one observation per election. See BACKTEST_SCOPE.md section 5.")
    print(f"{'='*74}\n")

    return {"observations": observations, "results": results,
            "best_rmse": b_rmse, "current": current, "bootstrap": boot,
            "plateau": (lo, hi)}


def worst_misses(observations, alpha, n=10):
    scored = sorted(
        ((abs(alpha * o["poll_margin"] + (1 - alpha) * o["lean_margin"]
              - o["actual_margin"]), o) for o in observations),
        reverse=True)
    return scored[:n]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--equal-credibility", action="store_true",
                    help="flatten pollster credibility to 1.0 (rolled back after)")
    ap.add_argument("--both", action="store_true",
                    help="run weighted and equal-credibility, then compare")
    ap.add_argument("--misses", type=int, default=8,
                    help="how many worst-miss races to list (0 to skip)")
    ap.add_argument("--bootstraps", type=int, default=2000)
    args = ap.parse_args()

    if args.both:
        weighted = report(False, args.bootstraps)
        equal = report(True, args.bootstraps)
        print(f"{'='*74}\nLOOKAHEAD CHECK — what the 2021-vintage grades are worth\n{'='*74}")
        print(f"  best alpha:  weighted {weighted['best_rmse']['alpha']:.2f}   "
              f"equal {equal['best_rmse']['alpha']:.2f}")
        print(f"  best RMSE:   weighted {weighted['best_rmse']['rmse']:.2f}   "
              f"equal {equal['best_rmse']['rmse']:.2f}   "
              f"(delta {equal['best_rmse']['rmse'] - weighted['best_rmse']['rmse']:+.2f})")
        print(f"  A large positive delta would mean the grades are doing real work"
              f" — some of it earned, some of it hindsight.\n{'='*74}\n")
        main_run = weighted
    else:
        main_run = report(args.equal_credibility, args.bootstraps)

    if args.misses:
        print(f"{'='*74}\nWORST MISSES at alpha={LEAN_ALPHA:.2f}\n{'='*74}")
        for err, o in worst_misses(main_run["observations"], LEAN_ALPHA, args.misses):
            pred = LEAN_ALPHA * o["poll_margin"] + (1 - LEAN_ALPHA) * o["lean_margin"]
            print(f"  {o['year']} {o['state']}  predicted {pred:+6.1f}  "
                  f"actual {o['actual_margin']:+6.1f}  miss {err:5.1f}   "
                  f"(polls {o['poll_margin']:+6.1f}, lean {o['lean_margin']:+6.1f})")
        print(f"{'='*74}\n")
