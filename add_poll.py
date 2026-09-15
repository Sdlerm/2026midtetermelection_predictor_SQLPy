"""add_poll.py — append a manually-sourced poll to data/house_added.csv or
data/senate_added.csv, in the schema house_ingest.py / senate_ingest.py
expect.

Manually-added polls are kept in their own *_added.csv file rather than
appended directly to house.csv / senate.csv, because those files are
periodically overwritten wholesale by a fresh NYT/538 feed download — writing
directly into them would silently lose manually-added polls the next time
that happens. house_ingest.py / senate_ingest.py each load their normal feed
file and then their *_added.csv on every run, so entries in the added file
always make it into the database regardless of how many times the feed file
gets redownloaded.

This does NOT touch the database — after appending, rerun the matching
ingest script to actually load it (interactive mode offers to do this for
you).

Run with no arguments (or -i/--interactive) to be prompted for each field:
    python add_poll.py

Or pass everything as flags for scripting/repeat use:
    python add_poll.py house --state CA --district 14 --pollster "Data Insights" \\
        --end-date 2026-08-30 --sample-size 500 --population lv \\
        --candidate "Eric Swalwell:D:58" --candidate "Some Republican:R:38"

    python add_poll.py senate --state OH --pollster "Suffolk" --end-date 2026-09-01 \\
        --candidate "Sherrod Brown:D:47" --candidate "Jon Husted:R:49" --dry-run
"""
import argparse
import csv
import datetime as dt
import os
import subprocess
import sys
import uuid

import pandas as pd

_ROOT           = os.path.dirname(__file__)
DATA_DIR        = os.path.join(_ROOT, "data")
HOUSE_CSV       = os.path.join(DATA_DIR, "house.csv")
SENATE_CSV      = os.path.join(DATA_DIR, "senate.csv")
HOUSE_ADDED_CSV  = os.path.join(DATA_DIR, "house_added.csv")
SENATE_ADDED_CSV = os.path.join(DATA_DIR, "senate_added.csv")
HOUSE_NOMINEES  = os.path.join(_ROOT, "house_nominees.csv")
SENATE_NOMINEES = os.path.join(_ROOT, "senate_nominees.csv")

_PARTY_MAP = {"D": "DEM", "DEM": "DEM", "R": "REP", "REP": "REP"}


def _pad(district):
    return f"{int(district):02d}"


def parse_candidate(spec):
    """'Name:PARTY:PCT' -> (name, 'DEM'/'REP', pct)."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"--candidate must be NAME:PARTY:PCT, got {spec!r}")
    name, party_raw, pct_raw = (p.strip() for p in parts)
    party = _PARTY_MAP.get(party_raw.upper())
    if party is None:
        raise argparse.ArgumentTypeError(f"party must be D/DEM or R/REP, got {party_raw!r}")
    try:
        pct = float(pct_raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"pct must be a number, got {pct_raw!r}")
    if not name:
        raise argparse.ArgumentTypeError(f"candidate name is empty in {spec!r}")
    return name, party, pct


def load_house_roster():
    """(state, zero-padded district) -> {'D': dem_nominee, 'R': rep_nominee}, rostered rows only."""
    df = pd.read_csv(HOUSE_NOMINEES)
    roster = {}
    for _, row in df.iterrows():
        if pd.isna(row["dem_nominee"]) or pd.isna(row["rep_nominee"]):
            continue
        state = str(row["state"]).strip().upper()
        district = _pad(row["district"])
        roster[(state, district)] = {
            "D": str(row["dem_nominee"]).strip(),
            "R": str(row["rep_nominee"]).strip(),
        }
    return roster


def load_senate_roster():
    """state -> {'D': name, 'R': name}."""
    df = pd.read_csv(SENATE_NOMINEES)
    roster = {}
    for _, row in df.iterrows():
        state = str(row["state"]).strip().upper()
        party = str(row["party"]).strip().upper()
        roster.setdefault(state, {})[party] = str(row["name"]).strip()
    return roster


def warn_if_unrostered(race, state, district, candidates):
    """Best-effort sanity check — mirrors what house_ingest.py / senate_ingest.py
    will do at load time, so a bad state/district/candidate shows up now
    instead of silently vanishing from the "Loaded N poll rows" count later."""
    if race == "house":
        roster = load_house_roster()
        key = (state, district)
        if key not in roster:
            print(f"WARNING: {state}-{district} is not rostered in house_nominees.csv "
                  f"(needs both a dem_nominee and rep_nominee) — house_ingest.py will "
                  f"discard these rows.", file=sys.stderr)
            return
        for name, party, _pct in candidates:
            expected = roster[key]["D" if party == "DEM" else "R"]
            if expected.split()[-1].lower() not in name.lower():
                print(f"WARNING: candidate {name!r} doesn't obviously match the rostered "
                      f"{party} nominee {expected!r} for {state}-{district}.", file=sys.stderr)
    else:
        roster = load_senate_roster()
        if state not in roster:
            print(f"WARNING: {state} has no rows in senate_nominees.csv.", file=sys.stderr)
            return
        for name, party, _pct in candidates:
            expected = roster[state].get("D" if party == "DEM" else "R")
            if not expected:
                print(f"WARNING: no rostered {party} nominee for {state} in senate_nominees.csv.",
                      file=sys.stderr)
            elif expected.split()[-1].lower() not in name.lower():
                print(f"WARNING: candidate {name!r} doesn't obviously match the rostered "
                      f"{party} nominee {expected!r} for {state}.", file=sys.stderr)


def build_rows(csv_columns, race, state, district, args, candidates):
    poll_id = str(uuid.uuid4())
    start_date = args.start_date or args.end_date

    shared = {
        "poll_id":        poll_id,
        "pollster":       args.pollster,
        "state":          state,
        "start_date":     start_date,
        "end_date":       args.end_date,
        "sample_size":    "" if args.sample_size is None else args.sample_size,
        "population":     args.population,
        "numeric_grade":  "" if args.numeric_grade is None else args.numeric_grade,
        "url":            args.url or "",
        "partisan":       args.partisan or "",
        "cycle":          2026,
        "office_type":    "U.S. House" if race == "house" else "U.S. Senate",
        "seat_number":    args.district if race == "house" else "",
        "election_date":  args.election_date,
        "stage":          "general",
    }

    rows = []
    for name, party, pct in candidates:
        row = {col: "" for col in csv_columns}
        row.update(shared)
        row.update({
            "party":          party,
            "pct":            pct,
            "candidate_name": name,
            "answer":         name,
        })
        rows.append(row)
    return rows, poll_id


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def _prompt(text, default=None, required=True, choices=None, cast=str):
    """One question, one retry loop. Blank input takes `default` if set,
    otherwise re-asks (or returns None if not required)."""
    choice_hint = f" ({'/'.join(choices)})" if choices else ""
    default_hint = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = input(f"{text}{choice_hint}{default_hint}: ").strip()
        if not raw:
            if default is not None:
                return default
            if not required:
                return None
            print("  Required.")
            continue
        if choices and raw.lower() not in [c.lower() for c in choices]:
            print(f"  Enter one of: {', '.join(choices)}")
            continue
        try:
            return cast(raw)
        except ValueError:
            print("  Not a valid value, try again.")


def _prompt_candidates():
    print("\nCandidates — need at least 2 (a Dem and a Rep). Blank name to finish.")
    candidates = []
    while True:
        n = len(candidates) + 1
        name = input(f"  Candidate {n} name (blank to finish): ").strip()
        if not name:
            if len(candidates) >= 2:
                break
            print("  Need at least 2 candidates before finishing.")
            continue
        party_raw = _prompt("    Party", choices=["D", "R"])
        party = _PARTY_MAP[party_raw.upper()]
        pct = _prompt("    Poll result (%)", cast=float)
        candidates.append((name, party, pct))
    return candidates


def prompt_for_args():
    print("=== add_poll.py — interactive mode ===")
    print("(Enter accepts the [default]; Ctrl+C cancels any time)\n")

    race = _prompt("Race", choices=["house", "senate"]).lower()
    state = _prompt("State (2-letter code, e.g. CA)").upper()
    district = _prompt("District number (e.g. 14)", cast=int) if race == "house" else None

    pollster = _prompt("Pollster name")
    today = dt.date.today().isoformat()
    end_date = _prompt("Poll end date (YYYY-MM-DD)", default=today)
    start_date = _prompt("Poll start date (YYYY-MM-DD)", default=end_date)
    election_date = _prompt("Election date (YYYY-MM-DD)", default="2026-11-03")
    population = _prompt("Population sampled", default="lv", choices=["lv", "rv", "a"]).lower()
    sample_size = _prompt("Sample size (optional)", default="", required=False)
    numeric_grade = _prompt("Pollster numeric grade (optional)", default="", required=False)
    partisan = _prompt("Sponsoring party, if a partisan/internal poll (optional)", default="", required=False)
    url = _prompt("Source URL (optional)", default="", required=False)

    candidates = _prompt_candidates()

    return argparse.Namespace(
        race=race,
        state=state,
        district=district,
        pollster=pollster,
        end_date=end_date,
        start_date=start_date,
        election_date=election_date,
        population=population,
        sample_size=int(sample_size) if sample_size else None,
        numeric_grade=float(numeric_grade) if numeric_grade else None,
        partisan=partisan or None,
        url=url or None,
        candidate=[f"{name}:{party}:{pct}" for name, party, pct in candidates],
        dry_run=False,
    )


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Append a manually-sourced poll to data/house.csv or data/senate.csv.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("race", nargs="?", choices=["house", "senate"])
    ap.add_argument("-i", "--interactive", action="store_true",
                     help="prompt for each field (default when run with no other arguments)")
    ap.add_argument("--state", help="2-letter state code, e.g. CA")
    ap.add_argument("--district", type=int, help="required for house, omit for senate")
    ap.add_argument("--pollster")
    ap.add_argument("--end-date", help="YYYY-MM-DD")
    ap.add_argument("--start-date", help="defaults to --end-date")
    ap.add_argument("--election-date", default="2026-11-03")
    ap.add_argument("--population", default="lv", choices=["lv", "rv", "a"],
                     help="likely voters / registered voters / all adults (default: lv)")
    ap.add_argument("--sample-size", type=int)
    ap.add_argument("--numeric-grade", type=float, help="pollster quality score, if you have one")
    ap.add_argument("--partisan", help="sponsoring party if this is a partisan/internal poll, e.g. DEM")
    ap.add_argument("--url", help="source link, for your own records")
    ap.add_argument("--candidate", action="append", metavar="NAME:PARTY:PCT",
                     help="repeatable, e.g. --candidate 'Jane Smith:D:48' --candidate 'John Doe:R:44'")
    ap.add_argument("--dry-run", action="store_true", help="print the row(s) without writing")
    args = ap.parse_args()

    interactive = args.interactive or len(sys.argv) == 1
    if interactive:
        args = prompt_for_args()
    else:
        missing = [flag for flag, val in [
            ("race", args.race), ("--state", args.state),
            ("--pollster", args.pollster), ("--end-date", args.end_date),
        ] if not val]
        if not args.candidate:
            missing.append("--candidate")
        if missing:
            ap.error(f"the following arguments are required: {', '.join(missing)}")

    state = args.state.strip().upper()
    if len(state) != 2:
        sys.exit(f"state must be a 2-letter code, got {args.state!r}")

    # Write to *_added.csv (not house.csv/senate.csv) — the feed files are
    # periodically overwritten wholesale by a fresh download, which would
    # silently discard manually-added rows. house_ingest.py / senate_ingest.py
    # load the *_added.csv on top of the feed file on every run.
    if args.race == "house":
        if args.district is None:
            sys.exit("--district is required for house polls")
        district = _pad(args.district)
        csv_path = HOUSE_ADDED_CSV
        schema_path = HOUSE_CSV
    else:
        if args.district is not None:
            sys.exit("--district is not used for senate polls")
        district = ""
        csv_path = SENATE_ADDED_CSV
        schema_path = SENATE_CSV

    try:
        candidates = [parse_candidate(c) for c in args.candidate]
    except argparse.ArgumentTypeError as e:
        sys.exit(str(e))
    if len(candidates) < 2:
        print("WARNING: fewer than 2 candidates given — house_model.py/senate_model.py "
              "expect a D and an R to build a margin.", file=sys.stderr)

    warn_if_unrostered(args.race, state, district, candidates)

    if not os.path.exists(schema_path):
        sys.exit(f"{schema_path} not found — run the normal NYT-feed download first "
                  f"(see README Setup step 3) so the column schema exists to append to.")

    columns = pd.read_csv(schema_path, dtype=str, keep_default_na=False, nrows=0).columns.tolist()
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        existing = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    else:
        existing = pd.DataFrame(columns=columns)
    rows, poll_id = build_rows(columns, args.race, state, district, args, candidates)
    new_df = pd.DataFrame(rows, columns=columns)

    preview_cols = ["poll_id", "pollster", "state", "seat_number", "end_date",
                    "population", "sample_size", "party", "candidate_name", "pct"]

    if args.dry_run:
        print(new_df[preview_cols].to_string(index=False))
        return

    if interactive:
        print("\n" + new_df[preview_cols].to_string(index=False))
        confirm = input(f"\nWrite these {len(rows)} row(s) to {csv_path}? [Y/n] ").strip().lower()
        if confirm not in ("", "y", "yes"):
            print("Aborted — nothing written.")
            return

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv(csv_path, index=False, quoting=csv.QUOTE_ALL)

    label = f"{state}-{district}" if args.race == "house" else state
    print(f"Appended {len(rows)} row(s) (poll_id={poll_id}) for {label} to {csv_path}")

    ingest_script = f"{args.race}_ingest.py"
    if interactive:
        run_now = input(f"Run {ingest_script} now to load it into the database? [Y/n] ").strip().lower()
        if run_now in ("", "y", "yes"):
            subprocess.run([sys.executable, os.path.join(_ROOT, ingest_script)], check=False)
        else:
            print(f"Run `.venv/bin/python {ingest_script}` when ready to load it into the database.")
    else:
        print(f"Run `.venv/bin/python {ingest_script}` to load it into the database.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled — nothing written.")
        sys.exit(1)
