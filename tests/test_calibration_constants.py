"""Internal consistency of the sigma constants.

These do not check that the numbers are *right* — that is what
test_backtest_sigmas.py does, against the historical record. These check that
the relationships the Monte Carlo relies on still hold, because every one of
them is silently assumed at a call site rather than asserted there.
"""
import math

import pytest

import calibration as c


HOUSE_TIERS = (
    ("polled",  c.SIGMA_TOTAL_MARGIN_HOUSE_POLLED,       c.SIGMA_LOCAL_MARGIN_HOUSE_POLLED),
    ("lean",    c.SIGMA_TOTAL_MARGIN_HOUSE_LEAN,         c.SIGMA_LOCAL_MARGIN_HOUSE_LEAN),
    ("redrawn", c.SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN, c.SIGMA_LOCAL_MARGIN_HOUSE_LEAN_REDRAWN),
)


@pytest.mark.parametrize("name,total,local", HOUSE_TIERS)
def test_house_variance_decomposition(name, total, local):
    """total^2 == national^2 + local^2, per tier.

    monte_carlo_house draws the national error once per simulated election and
    the local error per district. If these three numbers stop satisfying the
    identity, the simulation's effective sigma is no longer the total this
    file advertises, and nothing in the run would say so.
    """
    assert total ** 2 == pytest.approx(c.SIGMA_NATIONAL_MARGIN_HOUSE ** 2 + local ** 2)


def test_senate_variance_decomposition():
    assert c.SIGMA_TOTAL_MARGIN ** 2 == pytest.approx(
        c.SIGMA_NATIONAL_MARGIN ** 2 + c.SIGMA_LOCAL_MARGIN ** 2)


def test_house_tiers_are_strictly_ordered():
    """A polled district must be the most certain and a redrawn lean-only one
    the least. monte_carlo_house's sanity check #5 asserts the same ordering on
    simulated win probabilities; this asserts it on the inputs, so a bad edit
    is caught without running 500k simulations."""
    assert (c.SIGMA_TOTAL_MARGIN_HOUSE_POLLED
            < c.SIGMA_TOTAL_MARGIN_HOUSE_LEAN
            < c.SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN)


@pytest.mark.parametrize("name,total,local", HOUSE_TIERS)
def test_sigmas_are_finite_and_positive(name, total, local):
    """A non-finite sigma makes every draw NaN and every district a coin flip
    that reports as a certainty."""
    for value in (total, local):
        assert math.isfinite(value) and value > 0


def test_national_term_is_smaller_than_every_local_term():
    """The national draw is shared by all 435 districts, so it dominates the
    seat-total variance even though it is the smallest of the sigmas. If it
    ever exceeded a local term the correlated/independent gap that sanity
    check #2 measures would invert."""
    for _name, _total, local in HOUSE_TIERS:
        assert c.SIGMA_NATIONAL_MARGIN_HOUSE < local
