"""
house_sensitivity.py — derive the "+1pp shift" caveat instead of hardcoding it.

The dashboard line "a +Xpp shift moves P(D majority) from A to B" is a DERIVED
statistic. Hardcoding it means it silently goes stale the next time the model
is re-run (fresh leans, new scores, a fresh seed) — which is exactly how the
box came to read 38% while the live headline read 44.4%. Nothing in
monte_carlo_house's results dict carries a sensitivity, so that number could
only ever have been pasted by hand. This computes it from the same simulate()
that produces the headline, so the two cannot drift apart again.

SCALE — read once, this is the whole point.
calibration.py keeps every sigma on the MARGIN scale, and
    margin = D_share - R_share = 2*dem_share - 100
so ONE point of two-party VOTE SHARE == TWO points of margin. A "one-point
shift in the national environment" — the generic-ballot number people actually
quote — is a +2.0 MARGIN nudge, not +1.0. monte_carlo_house's sanity check #3
nudges +1.0 margin, i.e. HALF a point of vote share: half of what "+1pp toward
D" communicates to anyone not holding the margin convention in their head.
Pick the meaning, state it in the label, make the nudge match. Default below:
1 point of vote share -> +2.0 margin, because that is what "+1pp" reads as.
"""

import numpy as np
from monte_carlo_house import simulate

# A sensitivity only needs 2-3 significant figures, so it runs far cheaper than
# the 1M-sim headline. Fixed seed so the caveat is reproducible and does not
# jitter between page loads.
SENS_N_SIMS = 200_000
SENS_SEED = 20260720
VOTE_SHARE_TO_MARGIN = 2.0   # margin = 2 x share deviation, two-party


def majority_sensitivity(races, base_d, base_r, shift_share_pts=1.0,
                         n_sims=SENS_N_SIMS, seed=SENS_SEED):
    """
    P(D majority) at the base margins and at a uniform national shift of
    +/- shift_share_pts POINTS OF VOTE SHARE.

    Returns the base probability, the up/down endpoints, and the local
    elasticity in majority-probability points per point of national vote —
    the stable, interpretable number, unlike the raw endpoints, and the one
    worth putting on the dashboard.
    """
    shift_margin = shift_share_pts * VOTE_SHARE_TO_MARGIN

    def p_majority(delta_margin):
        # Uniform shift = same delta added to every district's margin. Re-run,
        # because the House sim discards its per-sim matrix by design; there is
        # no stored array to perturb.
        nudged = [dict(r, margin=r["margin"] + delta_margin) for r in races]
        out = simulate(nudged, base_d, base_r, n_sims=n_sims, seed=seed)
        return out["p_d_majority"]

    base = simulate(races, base_d, base_r, n_sims=n_sims, seed=seed)
    p_base = base["p_d_majority"]
    p_up   = p_majority(+shift_margin)   # toward D
    p_down = p_majority(-shift_margin)   # toward R

    # Central difference around the base — the least-biased local slope.
    elasticity = (p_up - p_down) / (2.0 * shift_share_pts)

    return {
        "shift_share_pts": shift_share_pts,
        "p_base": p_base,
        "p_up": p_up,
        "p_down": p_down,
        "elasticity_per_share_pt": elasticity,
        "mean_d_seats": base["mean_d_seats"],
    }


def format_caveat(sens):
    """Build the dashboard line from the derived numbers — no string literals.

    Including the seat mean's position relative to 218: the sentence explaining
    WHY the slope is steep used to assert the distribution "sits just below 218",
    which was true when it was typed and false the moment the national
    environment term moved the mean to ~235. Read the side off the numbers."""
    s = sens
    gap = s["mean_d_seats"] - 218
    side = (f"{abs(gap):.0f} seats {'above' if gap >= 0 else 'below'} the "
            f"218 threshold")
    return (
        f"A +{s['shift_share_pts']:.0f}pp uniform shift in the national vote toward D "
        f"moves P(D majority) from {s['p_base']:.0%} to {s['p_up']:.0%}, and the same "
        f"shift toward R moves it to {s['p_down']:.0%} "
        f"(~{s['elasticity_per_share_pt'] * 100:.0f} points of majority probability "
        f"per point of national vote, near the current margin). The seat mean sits "
        f"{side}, so that slope is what a correlated national miss costs — not "
        f"model fragility."
    )


if __name__ == "__main__":
    from house_model import predict_house_races
    from monte_carlo_house import build_races, baseline_seats

    predictions, _climate, _approval = predict_house_races()
    races = build_races(predictions)
    base_d, base_r = baseline_seats(races)

    sens = majority_sensitivity(races, base_d, base_r, shift_share_pts=1.0)
    print(format_caveat(sens))
    print(f"  raw endpoints: base {sens['p_base']:.1%}, "
          f"+1pt {sens['p_up']:.1%}, -1pt {sens['p_down']:.1%}")