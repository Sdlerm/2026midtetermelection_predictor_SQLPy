"""The measured sigmas must keep reproducing from the tracked historical CSVs.

This is the test that ties calibration.py to its evidence. The seven CSVs under
data/backtest/ are tracked precisely so this can run without re-downloading
from fec.gov, and the whole measurement takes about a tenth of a second, so
there is no reason for CI not to re-derive the numbers on every push.

The ranges below are wide enough to survive a corrected parse and narrow enough
to fail if an input file is truncated, re-sorted into the wrong districts, or
silently loses a cycle.
"""
import pytest

import backtest_house as bh
import calibration as cal


@pytest.fixture(scope="module")
def per_pair():
    return {key: bh.residuals(*key) for key in bh.PAIRS}


def _slices(per_pair, keys, redrawn):
    return [[r for r in per_pair[k][0] if r["redrawn"] is redrawn] for k in keys]


@pytest.fixture(scope="module")
def tiers(per_pair):
    """The same four slices run() reports in section 2."""
    P = bh.PAIRS
    return {
        "intact_fresh": bh.pool(_slices(per_pair, [k for k in P if int(k[0]) == k[1]], False)),
        "intact_stale": bh.pool(_slices(per_pair, [k for k in P if int(k[0]) < k[1]], False)),
        "redrawn_decennial": bh.pool(_slices(
            per_pair,
            [k for k in P if bh.LEAN_MAP_ERA[k[0]] != bh.ELECTION_MAP_ERA[k[1]]], True)),
        "redrawn_middecade": bh.pool(_slices(
            per_pair,
            [k for k in P if bh.LEAN_MAP_ERA[k[0]] == bh.ELECTION_MAP_ERA[k[1]]], True)),
    }


# (tier, expected SD, tolerance, expected n) — the values printed by section 2
# of `python backtest_house.py` at the commit that added this test, after the
# FEC fusion-party fix in 28e5b08. Update them together, from that output,
# whenever an input CSV is legitimately re-derived.
MEASURED = [
    ("intact_fresh",       7.22, 0.25, 1199),
    ("intact_stale",       7.60, 0.25,  395),
    ("redrawn_decennial", 16.98, 0.50, 1173),
    ("redrawn_middecade", 22.50, 1.00,   12),
]


@pytest.mark.parametrize("tier,expected_sd,tol,expected_n", MEASURED)
def test_tier_sd_reproduces(tiers, tier, expected_sd, tol, expected_n):
    got = tiers[tier]
    assert got["n"] == expected_n, f"{tier}: district count moved — an input CSV changed shape"
    assert got["sd"] == pytest.approx(expected_sd, abs=tol)


def test_redistricting_costs_more_than_staleness(tiers):
    """The finding that split one sigma into two: ageing a lean by a cycle is
    cheap, redrawing the lines under it is not. If this ever stops holding, the
    two-tier split in monte_carlo_house has lost its justification."""
    staleness_cost = tiers["intact_stale"]["sd"] - tiers["intact_fresh"]["sd"]
    redistricting_cost = tiers["redrawn_decennial"]["sd"] - tiers["intact_fresh"]["sd"]
    assert staleness_cost < 1.0
    assert redistricting_cost > tiers["intact_fresh"]["sd"]


def test_calibration_matches_the_adopted_values(tiers):
    """calibration.py and backtest_house.py must not drift apart.

    backtest_house keeps its ADOPT_* constants next to the reasoning rather
    than only in its printout, for exactly this reason — but nothing enforced
    the link until this test.
    """
    assert cal.SIGMA_LOCAL_MARGIN_HOUSE_LEAN == pytest.approx(bh.ADOPT_LOCAL_LEAN)
    assert cal.SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN == pytest.approx(bh.ADOPT_LOCAL_LEAN_REDRAWN)
    assert cal.SIGMA_NATIONAL_MARGIN_HOUSE == pytest.approx(bh.ADOPT_NATIONAL)


def test_adopted_intact_sigma_stays_conservative(tiers):
    """ADOPT_LOCAL_LEAN sits at the TOP of the measured range on purpose: 2026
    is two cycles stale, which the sample cannot reach. A future re-measurement
    that pushes the readings above the adopted value invalidates that argument
    and should fail loudly rather than quietly widen the real error."""
    assert tiers["intact_fresh"]["sd"] < bh.ADOPT_LOCAL_LEAN
    assert tiers["intact_stale"]["sd"] < bh.ADOPT_LOCAL_LEAN


def test_adopted_redrawn_sigma_sits_inside_its_bracket(tiers):
    """16.0 was chosen to sit below the decennial readings (which include
    California's wholesale renumbering, more disruptive than a targeted
    mid-decade redraw) and well below North Carolina's mid-decade 22.5."""
    assert bh.ADOPT_LOCAL_LEAN_REDRAWN < tiers["redrawn_decennial"]["sd"]
    assert bh.ADOPT_LOCAL_LEAN_REDRAWN < tiers["redrawn_middecade"]["sd"]


@pytest.mark.parametrize("key", bh.PAIRS)
def test_every_pair_reconciles_to_435_districts(per_pair, key):
    """Scored + uncontested + unmatched must account for the whole chamber. A
    district that falls out of every bucket is a parsing bug that would
    otherwise show up only as a slightly wrong sigma."""
    _rows, meta = per_pair[key]
    assert meta["n"] + meta["uncontested"] + meta["unmatched"] == 435
