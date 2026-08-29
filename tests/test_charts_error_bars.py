"""Regression: a race with no polling must not break the vote-share chart.

_bar_stderr maps a missing stderr to NaN, which is right for matplotlib (it
draws nothing, rather than a visible cap at +/-0.0 that would claim the race is
known exactly) and wrong for arithmetic. Before the fix, a lean-only race
sorted first made max() return NaN, and the axis calls downstream raised.
"""
import math

import pytest

import charts


@pytest.fixture(autouse=True)
def _charts_to_tmp(tmp_path, monkeypatch):
    """Write test figures to tmp, never into the repo's data/charts."""
    monkeypatch.setattr(charts, "CHARTS_DIR", str(tmp_path))


def _cand(name, pct, stderr, incumbent=False, flip=False):
    return {"name": name, "projected": pct, "stderr": stderr,
            "is_incumbent": incumbent, "is_flip": flip}


def _rows(*specs):
    return [(key, "D", _cand(f"D {key}", d_pct, d_err),
             _cand(f"R {key}", r_pct, r_err), d_pct - r_pct)
            for key, d_pct, d_err, r_pct, r_err in specs]


def test_bar_stderr_maps_missing_to_nan():
    assert math.isnan(charts._bar_stderr(None))
    assert charts._bar_stderr(1.5) == 1.5


@pytest.mark.parametrize("label,rows", [
    # The original failure: the NaN row lands first, so max() keeps it.
    ("nan first", _rows(("XX-01", 51.0, None, 49.0, None),
                        ("YY-02", 48.0, 1.2, 52.0, 1.1))),
    # A NaN later is harmless to max() but must still render.
    ("nan last", _rows(("YY-02", 48.0, 1.2, 52.0, 1.1),
                       ("XX-01", 51.0, None, 49.0, None))),
    # Every race lean-only: nothing finite to size the axis from but the bars.
    ("all nan", _rows(("XX-01", 51.0, None, 49.0, None),
                      ("YY-02", 48.0, None, 52.0, None))),
    # One side polled, the other not — the mixed case inside a single race.
    ("half nan", _rows(("XX-01", 51.0, 1.4, 49.0, None),
                       ("YY-02", 48.0, None, 52.0, 1.1))),
])
def test_vote_shares_renders_without_error_bars(label, rows):
    charts._plot_vote_shares(rows, f"regression: {label}", "_test_error_bars.png")


def test_flip_marker_survives_a_missing_error_bar():
    """flip_x is derived from max_extent, so a NaN there also silently moved
    every FLIP label off the canvas."""
    rows = _rows(("XX-01", 51.0, None, 49.0, None))
    rows[0][2]["is_flip"] = True
    charts._plot_vote_shares(rows, "regression: flip with no error bar",
                             "_test_error_bars_flip.png")
