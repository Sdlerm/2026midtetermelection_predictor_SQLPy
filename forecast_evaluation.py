"""
Sanity/realism audit of the most recently published forecast.

Reads the last row of forecast_history/history.csv (the latest published run)
plus its Monte Carlo logs, cross-checks them against the live database, CSVs,
and model constants, and writes forecast_history/forecast_assessment_<date>.md.

This does not re-run the Senate/House pipelines. It evaluates whatever the
latest published projection already is, and separately flags when that
projection looks stale relative to the current code/data on disk.
"""
import csv
import os
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime

import numpy as np

from init_db import get_connection
import calibration as cal
from senate_model import load_nominees as load_senate_nominees
from house_ingest import load_house_nominees

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORECAST_HISTORY_DIR = os.path.join(BASE_DIR, "forecast_history")
HISTORY_CSV = os.path.join(FORECAST_HISTORY_DIR, "history.csv")

# Tracked files whose drift from the last published run matters most.
WATCHED_MODEL_FILES = {"calibration.py", "senate_model.py", "house_model.py"}
WATCHED_DATA_FILES = {
    "senate_nominees.csv", "house_nominees.csv", "state_lean.csv",
    "pollster_ratings.csv", "data/president.csv", "data/senate_added.csv",
    "data/house_added.csv",
}

SENATE_ROW_RE = re.compile(
    r'^\s*([A-Z]{2})\s+([\d.]+)%\s+\S*\s+(.+?)\s+vs\s+(.+?)\s*$'
)
HOUSE_ROW_RE = re.compile(
    r'^\s*([A-Z]{2}-\d+)\s+([\d.]+)%\s+\S*\s+\[([^\]]*)\]\s+([+-]?[\d.]+)\s+(.+?)\s+vs\s+(.+?)\s*$'
)
SANITY_LINE_RE = re.compile(r'^\s*\[(PASS|FAIL)\]\s*(.+)$')

# Suffixes stripped before taking a "last name" token, so "John J. McGuire III"
# and "John McGuire" still compare equal on surname.
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _norm(name):
    """Loose name normalization for spotting formatting-only mismatches
    (whitespace, punctuation, accents)."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r'[.\-]', '', name)
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name


def _last_token(name):
    """Returns the surname-like token used for loose person-name matching."""
    parts = _norm(name).split()
    while parts and parts[-1] in NAME_SUFFIXES:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def _find_fuzzy_match(name, candidate_names):
    """Best-effort guess that `name` and one of `candidate_names` are the same
    person filed under different strings. Returns (matched_name, reason) or None."""
    for cand_name in candidate_names:
        if _norm(cand_name) == _norm(name):
            return cand_name, "formatting/whitespace/accent difference"
    target_tok = _last_token(name)
    if target_tok:
        for cand_name in candidate_names:
            if _last_token(cand_name) == target_tok:
                return cand_name, "likely the same person, different name string"
    return None


# ---------------------------------------------------------------------------
# Loaders / parsers
# ---------------------------------------------------------------------------

def load_history():
    """Loads and date-sorts the published forecast history rows."""
    rows = []
    with open(HISTORY_CSV, newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                for k, v in row.items():
                    if k not in ("run_date", "max_poll_date"):
                        row[k] = float(v)
            except (TypeError, ValueError):
                print(f"Skipping malformed history.csv row {i}: {row}", file=sys.stderr)
                continue
            rows.append(row)
    rows.sort(key=lambda r: r["run_date"])
    return rows


def find_projection_logs(run_date):
    """Returns (senate_path, house_path, fallback_used) where fallback_used maps
    chamber -> True if no dated archive for run_date existed and the rolling
    (undated) log was used instead."""
    dated_senate = os.path.join(FORECAST_HISTORY_DIR, f"mc_senate_{run_date}.log")
    dated_house = os.path.join(FORECAST_HISTORY_DIR, f"mc_house_{run_date}.log")
    rolling_senate = os.path.join(FORECAST_HISTORY_DIR, "mc_senate.log")
    rolling_house = os.path.join(FORECAST_HISTORY_DIR, "mc_house.log")
    senate_path = dated_senate if os.path.exists(dated_senate) else rolling_senate
    house_path = dated_house if os.path.exists(dated_house) else rolling_house
    fallback_used = {
        "Senate": not os.path.exists(dated_senate),
        "House": not os.path.exists(dated_house),
    }
    return senate_path, house_path, fallback_used


def check_log_provenance(findings, run_date, senate_log_path, house_log_path, fallback_used):
    """The rolling mc_*.log files carry no run identifier of their own, so a
    fallback to them is only trustworthy if their mtime lines up with the run
    being assessed."""
    for chamber, path, fell_back in [
        ("Senate", senate_log_path, fallback_used.get("Senate")),
        ("House", house_log_path, fallback_used.get("House")),
    ]:
        if not fell_back or not os.path.exists(path):
            continue
        mtime_date = datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()
        if mtime_date != run_date:
            findings.append(("WARN", "Freshness",
                f"No dated archive log exists for the {chamber} run on {run_date}; fell back to the "
                f"rolling {os.path.basename(path)}, whose file mtime ({mtime_date}) doesn't match that "
                f"run date. Its content may belong to a different run than the one being assessed."))


def parse_senate_log(path):
    """Parses itemized Senate race probabilities from a Monte Carlo log."""
    races = []
    if not os.path.exists(path):
        return races
    with open(path) as f:
        for line in f:
            m = SENATE_ROW_RE.match(line)
            if m:
                state, pct, cand_a, cand_b = m.groups()
                races.append({
                    "state": state, "prob": float(pct),
                    "cand_a": cand_a.strip(), "cand_b": cand_b.strip(),
                })
    return races


def parse_house_log(path):
    """Parses itemized House district probabilities and margins from a log."""
    races = []
    if not os.path.exists(path):
        return races
    with open(path) as f:
        for line in f:
            m = HOUSE_ROW_RE.match(line)
            if m:
                label, pct, tier, margin, cand_a, cand_b = m.groups()
                state, district = label.split("-", 1)
                races.append({
                    "label": label, "state": state, "district": district,
                    "prob": float(pct), "tier": tier.strip(), "margin": float(margin),
                    "cand_a": cand_a.strip(), "cand_b": cand_b.strip(),
                })
    return races


def extract_sanity_lines(path):
    """Extracts embedded [PASS]/[FAIL] sanity-check lines from a log."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            m = SANITY_LINE_RE.match(line)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    return out


def extract_warning_lines(path):
    """Extracts pipeline WARNING lines from a log for report surfacing."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            if line.startswith("WARNING:"):
                out.append(line.strip())
    return out


def git_status_lines():
    """Returns porcelain status lines, or None if git status could not be run
    (distinct from an empty list, which means the tree really is clean)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=10, check=True,
        )
        return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return None


def _porcelain_path(line):
    """Path portion of one `git status --porcelain` line. Handles renames
    ('R  old -> new'), which are not plain 'XY path'."""
    rest = line[3:]
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    return rest.strip().strip('"')


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_freshness(findings, last, con):
    """Flags stale published runs and any newer polling already in the database."""
    run_dt = datetime.strptime(last["run_date"], "%Y-%m-%d").date()
    age_days = (date.today() - run_dt).days
    sev = "WARN" if age_days > 7 else "OK"
    findings.append((sev, "Freshness",
        f"Last published forecast run is dated {last['run_date']} ({age_days} day(s) old)."))

    cur = con.cursor()
    cur.execute("SELECT MAX(poll_date) FROM polls")
    db_max = cur.fetchone()[0]
    report_max = last["max_poll_date"]
    if db_max and db_max > report_max:
        findings.append(("ISSUE", "Freshness",
            f"The database has polls through {db_max}, but the last published forecast "
            f"({last['run_date']}) only reflects data through {report_max}. Newer polling "
            f"has already been ingested and is not yet reflected in a published projection."))
    else:
        findings.append(("OK", "Freshness",
            f"Database poll coverage (through {db_max}) does not exceed what the last "
            f"published run already reflects ({report_max})."))


def check_reproducibility(findings):
    """Checks whether watched model/data files drifted since the published run."""
    status = git_status_lines()
    if status is None:
        findings.append(("WARN", "Reproducibility",
            "Could not read `git status` (git unavailable, not a repo, or timed out); working-tree "
            "cleanliness was not verified."))
        return

    changed_paths = [_porcelain_path(l) for l in status if not l.startswith("??")]
    untracked_paths = [_porcelain_path(l) for l in status if l.startswith("??")]

    watched = WATCHED_MODEL_FILES | WATCHED_DATA_FILES
    # A watched file that is brand-new and untracked is just as "not what
    # produced the published run" as one that's merely modified.
    dirty_watched = [p for p in changed_paths + untracked_paths if p in watched]
    if dirty_watched:
        core = [p for p in dirty_watched if os.path.basename(p) in WATCHED_MODEL_FILES]
        sev = "ISSUE" if core else "WARN"
        findings.append((sev, "Reproducibility",
            f"{len(dirty_watched)} file(s) that feed the forecast are modified or newly added but not "
            f"committed: {', '.join(dirty_watched)}. The published projection may not correspond to the "
            f"current code/data on disk."))
    else:
        findings.append(("OK", "Reproducibility",
            "All model code and nominee/lean/rating files that feed the forecast are committed."))

    other_untracked = [p for p in untracked_paths if p not in watched and not p.startswith("forecast_history/")]
    if other_untracked:
        findings.append(("OK", "Reproducibility",
            f"Untracked files present but not yet wired into either model (informational only): "
            f"{', '.join(other_untracked)}."))


def check_calibration(findings, last):
    """Compares live calibration constants against the last published settings."""
    def compare(const_name, live_val, logged_val):
        """Records whether one calibration constant still matches the logged run."""
        if abs(live_val - logged_val) > 0.02:
            findings.append(("ISSUE", "Calibration",
                f"calibration.{const_name} is currently {live_val:g}, but the last published run "
                f"logged {logged_val:g}. This sigma changed after that run without a new run/commit, "
                f"so the published confidence bands no longer match the code that would produce them."))
        else:
            findings.append(("OK", "Calibration",
                f"calibration.{const_name} ({live_val:g}) matches the last published run."))

    compare("SIGMA_TOTAL_MARGIN", cal.SIGMA_TOTAL_MARGIN, last["sigma_total"])
    compare("SIGMA_NATIONAL_MARGIN", cal.SIGMA_NATIONAL_MARGIN, last["sigma_national"])
    compare("SIGMA_TOTAL_MARGIN_HOUSE_POLLED", cal.SIGMA_TOTAL_MARGIN_HOUSE_POLLED, last["sigma_total_house_polled"])
    compare("SIGMA_TOTAL_MARGIN_HOUSE_LEAN", cal.SIGMA_TOTAL_MARGIN_HOUSE_LEAN, last["sigma_total_house_lean"])


def _run_nominee_matching(findings, con, poll_count_by_candidate, roster_by_race,
                           chamber, prob_by_label, lookup_race_id, label_fn):
    """Shared matching logic for both chambers. `roster_by_race` maps an opaque
    race key to {party: {"name": ...}}; `lookup_race_id(cur, key)` resolves that
    key to a DB race id (or None); `label_fn(key)` formats it for display."""
    cur = con.cursor()
    n_checked = 0
    n_matched = 0
    issues = []
    for race_key, parties in sorted(roster_by_race.items()):
        race_id = lookup_race_id(cur, race_key)
        if race_id is None:
            continue  # never polled for this race; not a bug
        cur.execute("SELECT id, name, party FROM candidates WHERE race_id = ?", (race_id,))
        all_cands = cur.fetchall()

        # Gate on whether the RACE has any real polling at all, independent of
        # whether the roster's own nominee names happen to match anything —
        # otherwise a race where BOTH sides are misnamed (neither resolves)
        # looks identical to a race with no polls, and gets silently dropped.
        if sum(poll_count_by_candidate.get(cid, 0) for cid, _n, _p in all_cands) == 0:
            continue

        match_info = {}
        for party, info in parties.items():
            name = info["name"]
            # Party-scoped: a name match under the WRONG party is not this
            # nominee's polling, it's evidence of a mistagged candidate row.
            row = next(((cid, n) for cid, n, p in all_cands if n == name and p == party), None)
            if row:
                cid, _ = row
                match_info[party] = (cid, poll_count_by_candidate.get(cid, 0))
            else:
                match_info[party] = None

        label = label_fn(race_key)
        for party, info in parties.items():
            n_checked += 1
            name = info["name"]
            result = match_info[party]
            if result is not None and result[1] > 0:
                n_matched += 1
                poll_count = result[1]
                if poll_count == 1:
                    prob = prob_by_label.get(label)
                    prob_txt = f" (currently projected at {prob:.1f}%)" if prob is not None else ""
                    findings.append(("WARN", f"Poll concentration — {chamber}",
                        f"{label} {party} nominee {name!r} resolves to exactly 1 matched poll in the "
                        f"database{prob_txt}. A single poll is driving this side of the race."))
                continue

            same_party_names = [n for _cid, n, p in all_cands if p == party]
            best = _find_fuzzy_match(name, same_party_names)
            zero_but_matched = result is not None and result[1] == 0
            if best:
                cand_name, reason = best
                issues.append(("ISSUE", f"Nominee matching — {chamber}",
                    f"{label} {party} nominee is listed as {name!r}, but the database's polls for "
                    f"this race are filed under {cand_name!r} ({reason}). Zero polls are being "
                    f"counted for this nominee — the race falls back to structural lean for this "
                    f"side until the names are reconciled."))
            elif zero_but_matched:
                issues.append(("WARN", f"Nominee matching — {chamber}",
                    f"{label} {party} nominee {name!r} matches a candidate row in the database, but "
                    f"that row has zero linked polls, though another candidate in this race IS polled."))
            else:
                issues.append(("WARN", f"Nominee matching — {chamber}",
                    f"{label} {party} nominee {name!r} has zero matched polls, though another "
                    f"candidate in this race IS matched to polls. Confirm the nominee name/spelling is correct."))

    findings.append(("OK", f"Nominee matching — {chamber}",
        f"{n_matched}/{n_checked} contested {chamber} nominees (in races where at least one side has "
        f"real polling) resolve to at least one matched poll."))
    findings.extend(issues)


def check_senate_nominee_matching(findings, con, poll_count_by_candidate, senate_prob_by_state):
    """Audits whether Senate nominees resolve to the polled database candidates."""
    nominees = load_senate_nominees()
    by_race = defaultdict(dict)
    for (state, party), info in nominees.items():
        by_race[state][party] = info

    def lookup(cur, state):
        """Resolves a Senate state code to its database race id."""
        cur.execute("SELECT id FROM races WHERE year=2026 AND district='' AND state=?", (state,))
        row = cur.fetchone()
        return row[0] if row else None

    _run_nominee_matching(findings, con, poll_count_by_candidate, by_race, "Senate",
                           senate_prob_by_state, lookup, label_fn=lambda state: state)


def check_house_nominee_matching(findings, con, poll_count_by_candidate, house_prob_by_label):
    """Audits whether House nominees resolve to the polled database candidates."""
    roster, _skipped = load_house_nominees()
    by_race = defaultdict(dict)
    for (state, district, party), info in roster.items():
        if info.get("same_party_general"):
            continue  # display-only race; not a projected matchup
        by_race[(state, district)][party] = info

    def lookup(cur, key):
        """Resolves a House state/district pair to its database race id."""
        state, district = key
        cur.execute("SELECT id FROM races WHERE year=2026 AND district=? AND state=?", (district, state))
        row = cur.fetchone()
        return row[0] if row else None

    _run_nominee_matching(findings, con, poll_count_by_candidate, by_race, "House",
                           house_prob_by_label, lookup, label_fn=lambda key: f"{key[0]}-{key[1]}")


def check_duplicate_pollsters(findings, con):
    """Detects pollster rows that differ only by formatting and split weights."""
    cur = con.cursor()
    cur.execute("SELECT id, name FROM pollsters")
    groups = defaultdict(list)
    for pid, name in cur.fetchall():
        groups[_norm(name)].append((pid, name))
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        findings.append(("OK", "Duplicate pollsters", "No case/whitespace-variant duplicate pollster names found."))
        return
    for variants in dups.values():
        detail = []
        for pid, name in variants:
            cur.execute("SELECT COUNT(*) FROM polls WHERE pollster_id = ?", (pid,))
            cnt = cur.fetchone()[0]
            detail.append(f"{name!r} ({cnt} poll rows)")
        findings.append(("ISSUE", "Duplicate pollsters",
            f"Pollster name recorded as {' and '.join(detail)} — same shop, split across two "
            f"pollster_id rows. Any race polled by both variants double-counts that pollster in "
            f"its weighted average."))


def check_probability_bounds(findings, senate_races, house_races):
    """Verifies every parsed race win probability stays within 0 to 100."""
    bad = [r for r in senate_races if not (0 <= r["prob"] <= 100)]
    bad += [r for r in house_races if not (0 <= r["prob"] <= 100)]
    if bad:
        for r in bad:
            label = r.get("label", r.get("state"))
            findings.append(("ISSUE", "Bounds", f"{label} win probability {r['prob']} is outside [0, 100]."))
    else:
        findings.append(("OK", "Bounds",
            f"All {len(senate_races)} Senate and {len(house_races)} itemized House win "
            f"probabilities fall within [0, 100]%."))


def check_margin_probability_realism(findings, house_races):
    """Checks that House margins and win probabilities move together sensibly."""
    if len(house_races) < 5:
        findings.append(("WARN", "Realism",
            "Fewer than 5 itemized House districts available; skipping margin-vs-probability correlation check."))
        return
    margins = np.array([r["margin"] for r in house_races])
    probs = np.array([r["prob"] for r in house_races])
    corr = float(np.corrcoef(margins, probs)[0, 1])
    if np.isnan(corr):
        findings.append(("WARN", "Realism",
            f"Margin-vs-probability correlation across the {len(house_races)} itemized districts "
            f"is undefined (no variance in margin or probability) — could not check monotonicity."))
    elif corr < 0.8:
        findings.append(("ISSUE", "Realism",
            f"Correlation between House district margin and P(D win) across the {len(house_races)} "
            f"itemized districts is only {corr:.3f}. A sane model should show near-perfect monotonic "
            f"agreement between margin and win probability."))
    else:
        findings.append(("OK", "Realism",
            f"House district margin vs. win-probability correlation is {corr:.3f} across "
            f"{len(house_races)} itemized districts — consistent with a monotonic, sane model."))

    extreme = [r for r in house_races if r["prob"] >= 99 or r["prob"] <= 1]
    if extreme:
        findings.append(("WARN", "Realism",
            f"{len(extreme)} of the {len(house_races)} itemized ('competitive band') House districts "
            f"are nonetheless called at ≥99% or ≤1% — double check these belong in the competitive band."))


def check_trends(findings, history):
    """Checks recent topline movement for internal consistency and swing size."""
    last = history[-1]
    if len(history) < 2:
        findings.append(("OK", "Trend", "Only one history.csv row exists; no week-over-week comparison possible."))
        return
    prev = history[-2]

    d_sen = last["senate_p_dem_control"] - prev["senate_p_dem_control"]
    d_house = last["house_p_dem_control"] - prev["house_p_dem_control"]
    d_sen_seats = last["senate_expected_d_seats"] - prev["senate_expected_d_seats"]
    d_house_seats = last["house_expected_d_seats"] - prev["house_expected_d_seats"]

    def trend_check(label, dprob, dseats):
        """Evaluates one chamber's probability and seat movement together."""
        sev = "OK"
        msg = (f"{label}: P(D) moved {dprob:+.1f}pp and expected D seats moved {dseats:+.2f} "
               f"from {prev['run_date']} to {last['run_date']}.")
        if dprob != 0 and dseats != 0 and (dprob > 0) != (dseats > 0):
            sev = "ISSUE"
            msg += " Probability and expected seats moved in OPPOSITE directions — internally inconsistent for the same run."
        elif abs(dprob) > 15:
            sev = "ISSUE"
            msg += " Large single-period swing — confirm this is driven by new polling data and not an uncommitted model/data change (see Reproducibility/Calibration above)."
        elif abs(dprob) > 7:
            sev = "WARN"
            msg += " Sizeable swing — worth a quick check of what changed in the underlying polls this period."
        findings.append((sev, "Trend", msg))

    trend_check("Senate", d_sen, d_sen_seats)
    trend_check("House", d_house, d_house_seats)

    if last["n_polls"] < prev["n_polls"]:
        findings.append(("ISSUE", "Trend",
            f"n_polls dropped from {prev['n_polls']:.0f} to {last['n_polls']:.0f} between runs — poll count should not decrease."))
    else:
        findings.append(("OK", "Trend",
            f"n_polls increased from {prev['n_polls']:.0f} to {last['n_polls']:.0f}, as expected."))

    if (d_sen > 5 and d_house < -5) or (d_sen < -5 and d_house > 5):
        findings.append(("WARN", "Cross-chamber",
            f"Senate P(D) moved {d_sen:+.1f}pp while House P(D) moved {d_house:+.1f}pp in the "
            f"opposite direction over the same period. Both chambers share the same national-"
            f"environment inputs (approval, economy), so a sharp divergence deserves a specific "
            f"explanation rather than being read as two independent signals."))
    else:
        findings.append(("OK", "Cross-chamber", "Senate and House toplines moved in a broadly consistent direction this period."))


def check_embedded_sanity(findings, senate_log_path, house_log_path):
    """Surfaces built-in pipeline sanity checks and warnings from both logs."""
    for chamber, path in [("Senate", senate_log_path), ("House", house_log_path)]:
        lines = extract_sanity_lines(path)
        if not lines:
            findings.append(("WARN", f"Built-in sanity checks — {chamber}",
                "No embedded [PASS]/[FAIL] sanity-check block found in the log for this chamber."))
        else:
            for status, text in lines:
                findings.append(("OK" if status == "PASS" else "ISSUE",
                    f"Built-in sanity checks — {chamber}", text))
        # Independent of whether the sanity-check footer exists: a truncated
        # or interrupted run can still carry real WARNING: lines, and those
        # matter most exactly when the footer is missing.
        for w in extract_warning_lines(path):
            findings.append(("WARN", f"Pipeline warning — {chamber}", w))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def write_report(findings, today, last, prev, senate_log_path, house_log_path):
    """Writes the markdown assessment report summarizing all findings."""
    out_path = os.path.join(FORECAST_HISTORY_DIR, f"forecast_assessment_{today}.md")
    n_issue = sum(1 for f in findings if f[0] == "ISSUE")
    n_warn = sum(1 for f in findings if f[0] == "WARN")
    n_ok = sum(1 for f in findings if f[0] == "OK")

    lines = []
    lines.append(f"# Forecast Assessment — {today}")
    lines.append("")
    lines.append("Automated sanity/realism audit of the latest published forecast, generated by "
                  "`forecast_evaluation.py`. This does not re-run the pipeline; it checks the "
                  "projection already on disk against the live database, CSVs, and model constants.")
    lines.append("")
    lines.append(f"**Forecast run assessed:** {last['run_date']} (poll data through {last['max_poll_date']})")
    if prev:
        lines.append(f"**Compared against previous run:** {prev['run_date']}")
    lines.append(f"**Sources read:** `{os.path.relpath(senate_log_path, BASE_DIR)}`, "
                 f"`{os.path.relpath(house_log_path, BASE_DIR)}`, `{os.path.relpath(HISTORY_CSV, BASE_DIR)}`")
    lines.append("")
    lines.append(f"## Summary: {n_issue} issue(s), {n_warn} warning(s), {n_ok} check(s) passed")
    lines.append("")

    if n_issue:
        lines.append("### Issues requiring attention")
        for sev, cat, msg in findings:
            if sev == "ISSUE":
                lines.append(f"- **[{cat}]** {msg}")
        lines.append("")

    if n_warn:
        lines.append("### Warnings")
        for sev, cat, msg in findings:
            if sev == "WARN":
                lines.append(f"- **[{cat}]** {msg}")
        lines.append("")

    lines.append("### Checks passed")
    for sev, cat, msg in findings:
        if sev == "OK":
            lines.append(f"- [{cat}] {msg}")
    lines.append("")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def main():
    """Runs the full published-forecast audit and prints the report location."""
    today = date.today().isoformat()
    findings = []

    history = load_history()
    if not history:
        print("No rows in forecast_history/history.csv; nothing to assess.", file=sys.stderr)
        sys.exit(1)
    last = history[-1]
    prev = history[-2] if len(history) > 1 else None

    senate_log_path, house_log_path, fallback_used = find_projection_logs(last["run_date"])
    check_log_provenance(findings, last["run_date"], senate_log_path, house_log_path, fallback_used)
    senate_races = parse_senate_log(senate_log_path)
    house_races = parse_house_log(house_log_path)
    senate_prob_by_state = {r["state"]: r["prob"] for r in senate_races}
    house_prob_by_label = {r["label"]: r["prob"] for r in house_races}

    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute("SELECT candidate_id, COUNT(*) FROM polls GROUP BY candidate_id")
        poll_count_by_candidate = dict(cur.fetchall())

        check_freshness(findings, last, con)
        check_reproducibility(findings)
        check_calibration(findings, last)
        check_senate_nominee_matching(findings, con, poll_count_by_candidate, senate_prob_by_state)
        check_house_nominee_matching(findings, con, poll_count_by_candidate, house_prob_by_label)
        check_duplicate_pollsters(findings, con)
    finally:
        con.close()

    check_probability_bounds(findings, senate_races, house_races)
    check_margin_probability_realism(findings, house_races)
    check_trends(findings, history)
    check_embedded_sanity(findings, senate_log_path, house_log_path)

    out_path = write_report(findings, today, last, prev, senate_log_path, house_log_path)

    n_issue = sum(1 for f in findings if f[0] == "ISSUE")
    n_warn = sum(1 for f in findings if f[0] == "WARN")
    n_ok = sum(1 for f in findings if f[0] == "OK")
    print(f"Wrote {os.path.relpath(out_path, BASE_DIR)}")
    print(f"{n_issue} issue(s), {n_warn} warning(s), {n_ok} check(s) passed")


if __name__ == "__main__":
    main()
