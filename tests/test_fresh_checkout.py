"""Every module must import on a clean checkout.

Two separate P1 reviews on PR #3 were the same bug in different words: the
dashboard imported a module that did not exist, so `streamlit run dashboard.py`
died at import time and even the Senate half could not render. Nothing caught
it because nobody imports the whole tree in one go.

This runs in a subprocess against a throwaway database, because importing
dashboard has side effects (it queries at import time and warms Streamlit's
cache) that must not leak into the rest of the suite.
"""
import os
import pathlib
import subprocess
import sys

import pytest


MODULES = [
    "backtest_house", "backtest_senate", "calibration", "charts", "dashboard",
    "export_map_csv", "fetch_district_lean", "fetch_economics",
    "fetch_house_backtest_data", "house_ingest", "house_model",
    "house_sensitivity", "init_db", "load_historical", "load_historical_polls",
    "load_pollster_ratings", "make_district_geojson", "monte_carlo_house",
    "monte_carlo_senate", "senate_ingest", "senate_model",
]

PROBE = """
import importlib, sys, matplotlib
matplotlib.use("Agg")
import init_db
init_db.init_db()
failed = []
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed.append(f"{{name}}: {{type(exc).__name__}}: {{exc}}")
print("FAILED=" + "|".join(failed))
sys.exit(1 if failed else 0)
"""


@pytest.fixture
def repo_root():
    return pathlib.Path(__file__).resolve().parent.parent


def test_all_modules_import(tmp_path, repo_root):
    env = dict(os.environ, ELECTIONS_DB=str(tmp_path / "elections.db"),
               MPLBACKEND="Agg")
    proc = subprocess.run(
        [sys.executable, "-c", PROBE.format(modules=MODULES)],
        cwd=str(repo_root), env=env, capture_output=True, text=True)
    detail = next((line for line in proc.stdout.splitlines()
                   if line.startswith("FAILED=")), "FAILED=")
    assert proc.returncode == 0, detail.replace("|", "\n  ")


def test_no_module_was_forgotten(repo_root):
    """The list above is hand-maintained, so a new top-level module would
    otherwise never be covered."""
    on_disk = {p.stem for p in repo_root.glob("*.py")}
    missing = on_disk - set(MODULES)
    assert not missing, f"add these to MODULES in {__file__}: {sorted(missing)}"
