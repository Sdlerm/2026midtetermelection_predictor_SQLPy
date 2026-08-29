"""Regression: a bad or missing house.csv must not destroy the poll data.

Before the fix, house_ingest's __main__ deleted every 2026 House race and
COMMITTED, then called load_house_polls(), which is where the CSV is first
read. data/house.csv is gitignored and absent from a fresh checkout, so the
documented command emptied the table and then died on the read.

The fix moves the wipe inside load_house_polls, after every read and filter and
sharing one transaction with the inserts. These tests pin both halves: the
destructive step must not run early, and it must still run.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

import init_db
import house_ingest


HEADER = ("poll_id,question_id,pollster,numeric_grade,partisan,state,seat_number,"
          "stage,party,candidate_name,end_date,election_date,sample_size,population,pct")
# AK-01 and AL-02 are both in house_nominees.csv, so these rows survive the
# roster filter and actually land.
VALID_ROWS = (
    "900001,1,Test Pollster,2.5,,AK,1,general,DEM,John Williams,2026-08-01,2026-11-03,800,lv,47.0",
    "900001,1,Test Pollster,2.5,,AK,1,general,REP,Nick Begich,2026-08-01,2026-11-03,800,lv,49.0",
    "900002,1,Test Pollster,2.5,,AL,2,general,DEM,Shomari Figures,2026-08-02,2026-11-03,700,lv,51.0",
    "900002,1,Test Pollster,2.5,,AL,2,general,REP,Rhett Marques,2026-08-02,2026-11-03,700,lv,45.0",
)


@pytest.fixture
def repo_root():
    return pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway DB seeded with one 2026 House race, plus a Senate race that
    the House refresh must never touch."""
    path = tmp_path / "elections.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(path))
    init_db.init_db()

    con = sqlite3.connect(path)
    cur = con.cursor()
    for district, name in (("07", "Pre-existing House"), ("", "Pre-existing Senate")):
        cur.execute("INSERT INTO races (year, state, district) VALUES (2026, 'ZZ', ?)", (district,))
        race_id = cur.lastrowid
        cur.execute("INSERT INTO candidates (race_id, name, party) VALUES (?, ?, 'D')",
                    (race_id, name))
    con.commit()
    con.close()
    return path


def counts(path):
    con = sqlite3.connect(path)
    house = con.execute(
        "SELECT COUNT(*) FROM races WHERE year = 2026 AND district != ''").fetchone()[0]
    senate = con.execute(
        "SELECT COUNT(*) FROM races WHERE year = 2026 AND district = ''").fetchone()[0]
    con.close()
    return house, senate


def test_missing_csv_leaves_the_data_alone(db, tmp_path):
    before = counts(db)
    with pytest.raises((FileNotFoundError, OSError)):
        house_ingest.load_house_polls(filepath=str(tmp_path / "does_not_exist.csv"))
    assert counts(db) == before


def test_malformed_csv_leaves_the_data_alone(db, tmp_path):
    """The case a bare os.path.exists guard would still lose data on: the file
    is there and parses as CSV, but has none of the columns the loader needs."""
    bad = tmp_path / "house.csv"
    bad.write_text("this,is,not,a,house,poll,file\n1,2,3,4,5,6,7\n")
    before = counts(db)
    with pytest.raises(KeyError):
        house_ingest.load_house_polls(filepath=str(bad))
    assert counts(db) == before


def test_empty_csv_leaves_the_data_alone(db, tmp_path):
    empty = tmp_path / "house.csv"
    empty.write_text("")
    before = counts(db)
    with pytest.raises(Exception):
        house_ingest.load_house_polls(filepath=str(empty))
    assert counts(db) == before


def test_valid_csv_still_replaces_the_house_rows(db, tmp_path, capsys):
    """The wipe must still fire — a fix that quietly stopped refreshing would
    be worse than the bug it replaced."""
    good = tmp_path / "house.csv"
    good.write_text(HEADER + "\n" + "\n".join(VALID_ROWS) + "\n")

    house_before, senate_before = counts(db)
    assert house_before == 1

    house_ingest.load_house_polls(filepath=str(good))

    house_after, senate_after = counts(db)
    assert house_after == 2, "the two polled districts replaced the seeded one"
    assert senate_after == senate_before, "the Senate half of the partition was touched"
    assert "Cleared 1 2026 House race" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The entry point itself. The tests above call load_house_polls directly, which
# is where the wipe lives NOW — but the original bug was in __main__, so only
# running the script the way the README tells you to can reproduce it.
# ---------------------------------------------------------------------------

def _run_script(db_path, repo_root, csv_path):
    """`python house_ingest.py` against a throwaway DB and a named input.

    csv_path is always set explicitly, never left to the default. A developer
    who has actually run the pipeline HAS data/house.csv, and letting the
    script fall back to it would quietly load 2.9 MB of real polls into the
    temp DB and fail the assertion for the wrong reason.
    """
    env = dict(os.environ,
               ELECTIONS_DB=str(db_path),
               HOUSE_POLLS_CSV=str(csv_path))
    return subprocess.run([sys.executable, "house_ingest.py"],
                          cwd=str(repo_root), env=env,
                          capture_output=True, text=True)


def test_script_with_no_csv_does_not_wipe(db, repo_root, tmp_path):
    """The documented command, on a fresh checkout where data/house.csv has
    never been downloaded. This is the exact scenario that emptied the table."""
    before = counts(db)
    proc = _run_script(db, repo_root, tmp_path / "never_downloaded.csv")
    assert counts(db) == before, "running the script with no input destroyed the data"
    assert "not found" in (proc.stdout + proc.stderr)


def test_script_with_malformed_csv_does_not_wipe(db, repo_root, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("this,is,not,a,house,poll,file\n1,2,3,4,5,6,7\n")
    before = counts(db)
    _run_script(db, repo_root, bad)
    assert counts(db) == before
