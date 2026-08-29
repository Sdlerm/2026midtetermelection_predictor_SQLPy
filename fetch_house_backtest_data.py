"""
fetch_house_backtest_data.py — assemble the two inputs a House backtest needs.

BACKTEST_SCOPE.md §5 said a House equivalent of backtest_senate.py "needs
historical district lean vintages, plus handling districts whose boundaries
changed mid-decade", and called that data problem the whole job. This file is
that job. It writes two families of normalized CSVs into data/backtest/:

    district_lean_<vintage>.csv   the lean the model WOULD have had, per cycle
    house_results_<year>.csv      what actually happened, per district

Nothing here touches data/district_lean.csv or the live forecast. The 2026
pipeline reads fetch_district_lean.py's output; this file writes a parallel,
clearly-named set that only backtest_house.py consumes.

WHERE THE LEAN VINTAGES COME FROM
---------------------------------
fetch_district_lean.py pulls 538's partisan-lean file from master, which today
holds one column ("2022"). But the file has been rewritten in place four times,
and git remembers every version. Pinning each commit recovers the vintages:

    046f0cd (2018-11-19)  column pvi_538, "R+15.21" text format, 2012-cycle lines
    de6000e (2020-10-19)  columns 2018 + 2020, same text format, 2012-cycle lines
    646ceb3 (2021-05-26)  column 2021, signed numeric, 2012-cycle lines
    698baa1 (2022-09-09)  column 2022, signed numeric, 2022-cycle lines

Every one of those commits predates the November election it is used to predict,
which is the property that makes this a backtest rather than a fit. The 2018 and
2020 vintages are published in "R+15.21" / "D+3.02" text; 2021 and 2022 are
signed floats already in the D-positive convention calibration.py uses. Both
shapes are parsed to the signed convention here so downstream sees one format.

WHERE THE RESULTS COME FROM
---------------------------
The FEC's "Federal Elections" compilations — the official certified returns,
one workbook per cycle, sheet "US House Results by State". This is deliberately
NOT the MEDSL 1976-2024 file that load_historical.py used for the Senate:
MEDSL's Dataverse copy now sits behind a guestbook that a script cannot answer
on the user's behalf, so it is not scriptable. The FEC files are the same
underlying certifications and download without a gate.

  * 2018, 2020, 2022 are published.  2024 IS NOT — the FEC had not posted a
    federalelections2024.xlsx as of 2026-08-27, and the Clerk's equivalent is a
    PDF. So the backtest is THREE cycles, and the most recent one is 2022.
    That gap matters and backtest_house.py reports it rather than hiding it:
    see its "what this cannot tell you" section.

FOUR PARSING RULES, ALL OF THEM LOAD-BEARING
--------------------------------------------
1. Fusion voting. In NY and CT a candidate appears on several party lines with
   separate vote counts (Lawler 2022: R 125,738 + CRV 17,812). The workbook
   carries the sum in a "Combined Parties:" row whose GENERAL VOTES cell is
   EMPTY, so summing the per-line rows reproduces the combined total exactly
   and does not double count. Votes are therefore summed per FEC candidate ID,
   and the candidate is assigned to the major party among their lines.
2. Summary rows. "Party Votes:", "District Votes:", "Total State Votes:" are
   aggregates interleaved with the candidate rows, and "Scattered" write-in
   rows are not candidates. All of them carry FEC ID "n/a", so that one test
   drops every aggregate without needing to enumerate their labels.
3. Unexpired-term elections. A district holding a concurrent special election
   files it as "02-Unexpired Term" alongside the regular "02". Only the
   full-term contest is the race the lean is trying to predict.
4. At-large districts. The FEC writes them "00"; 538 writes them "-1". They are
   normalized to "01" here, matching house_ingest._pad.
"""

import csv
import os
import re
import ssl
import urllib.request

DATA = os.path.join(os.path.dirname(__file__), "data")
OUT_DIR = os.path.join(DATA, "backtest")

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
# (vintage label, commit sha, column name). The sha pins the file as it stood
# BEFORE that cycle's election — the whole point. Do not "update" these to
# master; master is the 2022 vintage and would leak four years of hindsight
# into the 2018 prediction.
LEAN_VINTAGES = (
    ("2018", "046f0cd3e76c1deb638f7290dbb9aec589de5b55", "pvi_538"),
    ("2020", "de6000e5aa1911696d7d85c27f9d1e8e7115bd18", "2020"),
    ("2021", "646ceb324d1a7b31c6c63879914b20803153a7dc", "2021"),
    ("2022", "698baa119ef3e28bb103f11689c636c0edd38357", "2022"),
)

LEAN_URL = ("https://raw.githubusercontent.com/fivethirtyeight/data/"
            "{sha}/partisan-lean/fivethirtyeight_partisan_lean_DISTRICTS.csv")

# (year, FEC document id, sheet name). Sheet names are inconsistent across
# cycles — 2018 has no numeric prefix, later years do — so they are recorded
# per year rather than guessed.
FEC_CYCLES = (
    (2018, 2706, "2018 US House Results by State"),
    (2020, 4228, "13. US House Results by State"),
    (2022, 5676, "8. US House Results by State"),
)

FEC_URL = "https://www.fec.gov/documents/{doc}/federalelections{year}.xlsx"

# Party lines that count toward each major party's district total, as TOKENS
# rather than whole labels — see _party_tokens for why the distinction matters.
# DFL and DNL are the Democratic party under its own name in MN and ND; GOP is
# the Republican party under its own name where a state files it that way.
DEM_LINES = {"D", "DEM", "DFL", "DNL"}
REP_LINES = {"R", "REP", "GOP"}

# A write-in appears as W(<line>) — W(D), W(R), W(DNL). A candidate carrying
# ONLY a write-in line is a write-in and does not make a race contested; the
# same wrapper alongside a real line (W(R)/R, W(D)/D) is the same candidate on
# the ballot and folds in with rule 1.
#
# This distinction exists to keep the "/" split below SAFE rather than to fix
# a standing bug. Whole-label matching happened to handle write-ins correctly
# by accident — "W(D)/W" is not literally in DEM_LINES, so it fell through to
# other_votes. Splitting on "/" without this guard would strip the wrapper and
# promote that same candidate to a full Democrat, which would newly corrupt
# WI-08 2022 (3,160 write-in D votes against a real Republican) and WI-07 2018
# (3 votes). Both still land in other_votes, which is correct.
_WRITE_IN = re.compile(r"^W\((?P<line>[^)]*)\)$")


def _party_tokens(labels):
    """Split a candidate's raw PARTY labels into (real_lines, write_in_lines).

    The FEC PARTY column is not a controlled vocabulary of single lines: it
    carries fusion tickets as one combined string ("D/WF", "D/IP/PRO/WF",
    "R/CON"), state party names ("DFL", "DNL", "GOP"), footnote asterisks
    ("D*", "R*"), and write-in wrappers ("W(D)", "W(DNL)", "N(D)/D"). Across
    2018/2020/2022 the sheets use 88, 78 and 81 distinct labels respectively.

    Matching whole labels against a set of bare lines therefore silently
    misses every fusion candidate. Oregon writes every fusion ticket as one
    combined label, which is why EVERY Oregon district fell out of all three
    cycles before this: the district read as uncontested, two_party_margin
    returned None, and the row was written contested=0. Splitting the labels
    recovers 24 genuinely contested districts — 2018 {CA-19, CA-32, ND-01,
    NY-02, OR-01..05, WA-08}, 2020 {AK-01, CT-02, ND-01, NJ-06, OH-07, OR-01,
    OR-03, OR-04}, 2022 {MI-04, OR-01, OR-04, OR-05, OR-06, PA-15} — and drops
    the uncontested counts from 52/36/43 to 42/28/37.

    Splitting on "/" and stripping the wrappers turns the label into the set
    of lines it actually represents, which is what the caller wants to ask
    about. Returns two sets so the caller can distinguish "on the ballot as a
    Democrat" from "a write-in on the Democratic line".
    """
    real, write_in = set(), set()
    for label in labels:
        for part in str(label).split("/"):
            part = part.strip().upper().rstrip("*")
            if not part:
                continue
            match = _WRITE_IN.match(part)
            if match:
                write_in.add(match.group("line").strip().rstrip("*"))
            else:
                # N(D) and similar designations wrap a line the same way a
                # write-in does but are a real ballot line, so the wrapper is
                # stripped and the contents kept.
                inner = re.match(r"^[A-Z]+\((?P<line>[^)]*)\)$", part)
                real.add(inner.group("line").strip() if inner else part)
    return real, write_in

# The FEC sheets carry the five territorial delegates and Puerto Rico's resident
# commissioner alongside the 435 voting seats. They elect no voting member, 538
# assigns them no partisan lean, and including them would add rows the backtest
# would then have to drop on the join. Dropped at parse time instead, where the
# reason can be stated once.
NON_VOTING = {"AS", "DC", "GU", "MP", "PR", "VI"}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _urlopen(url, timeout=180):
    """urlopen with the certifi retry fetch_district_lean._urlopen documents —
    same Python install, same missing system CA store, same reasoning."""
    try:
        return urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=timeout)
    except urllib.error.URLError as err:
        if not isinstance(getattr(err, "reason", None), ssl.SSLCertVerificationError):
            raise
        import certifi
        print("NOTE: system CA store unusable — verifying via certifi instead.")
        return urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=timeout,
            context=ssl.create_default_context(cafile=certifi.where()))


def _pad(district):
    """FEC/538 district text -> '01'. At-large is '00' at the FEC and '-1' at
    538; both mean the state's single seat, which everything else in this repo
    calls '01'."""
    n = int(float(district))
    return f"{max(n, 1):02d}"


# ---------------------------------------------------------------------------
# Lean vintages
# ---------------------------------------------------------------------------
_PVI_TEXT = re.compile(r"^\s*([DR])\s*\+\s*([0-9.]+)\s*$")


def parse_lean_value(raw):
    """
    Return a signed D-positive margin from either published shape.

    538 changed formats between the 2020 and 2021 vintages: "R+15.21" text
    became -15.21 numeric. Both are accepted, and anything that is neither
    raises — a silently-zero lean would read downstream as a perfectly balanced
    district, which is the one wrong answer that looks plausible.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _PVI_TEXT.match(raw)
    if m:
        party, value = m.group(1), float(m.group(2))
        return value if party == "D" else -value
    return float(raw)


def fetch_lean_vintages(out_dir=OUT_DIR):
    """Download each pinned vintage and write data/backtest/district_lean_<v>.csv."""
    written = {}
    for label, sha, column in LEAN_VINTAGES:
        text = _urlopen(LEAN_URL.format(sha=sha)).read().decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        if column not in (reader.fieldnames or []):
            raise ValueError(
                f"vintage {label} (sha {sha[:7]}) has no {column!r} column "
                f"(found {reader.fieldnames}) — the pin may be wrong."
            )

        rows = []
        for row in reader:
            key = (row.get("district") or "").strip().upper()
            if not key:
                continue
            state, _, district = key.partition("-")
            margin = parse_lean_value(row.get(column))
            if margin is None:
                continue
            rows.append({"district": f"{state}-{_pad(district)}",
                         "dem_margin": f"{margin:.4f}"})

        path = os.path.join(out_dir, f"district_lean_{label}.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["district", "dem_margin"])
            w.writeheader()
            w.writerows(rows)
        written[label] = len(rows)
        print(f"  lean vintage {label}: {len(rows)} districts -> {os.path.basename(path)}")
    return written


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def _download_fec(year, doc, out_dir=OUT_DIR):
    path = os.path.join(out_dir, f"federalelections{year}.xlsx")
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    blob = _urlopen(FEC_URL.format(doc=doc, year=year)).read()
    if not blob.startswith(b"PK"):
        raise ValueError(f"{year}: FEC returned {len(blob)} bytes that are not an "
                         f"xlsx — the document id may have moved.")
    with open(path, "wb") as f:
        f.write(blob)
    return path


def parse_fec_house(path, sheet):
    """
    Parse one FEC House sheet into {(state, district): {...vote totals...}}.

    Implements the four rules in the module docstring. Returns per-district
    dicts carrying dem/rep/other general-election votes, so the caller can
    decide what to do about uncontested seats rather than having that decision
    baked in here.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    col = {h: i for i, h in enumerate(header)}
    i_state = col["STATE ABBREVIATION"]
    i_dist = col["DISTRICT"]
    i_party = col["PARTY"]
    i_fec = col.get("FEC ID#", col.get("FEC ID"))
    i_gen = next(i for i, h in enumerate(header) if h.startswith("GENERAL VOTES"))
    # The sheets spell this header three different ways across the three cycles
    # ("(I)", "(I) Incumbent Indicator", "(I) INCUMBENT INDICATOR"), so it is
    # matched on the prefix they share rather than enumerated.
    i_inc = next(i for i, h in enumerate(header) if h.startswith("(I)"))

    # (state, district) -> fec_id -> {"votes": int, "lines": set(party)}
    per_candidate = {}
    # Every district the sheet mentions, whether or not a vote count came with
    # it. Florida does not print totals for unopposed races (the candidate never
    # appears on a ballot), and a Louisiana seat won outright in the all-party
    # primary holds no general at all — in both the GENERAL VOTES cell reads
    # "Unopposed" or is empty. Those districts are uncontested, not missing, and
    # dropping them silently would understate the uncontested count that the
    # backtest's coverage caveat rests on.
    seen = set()
    for row in rows[1:]:
        state = str(row[i_state] or "").strip().upper()
        district_raw = str(row[i_dist] or "").strip()
        fec_id = str(row[i_fec] or "").strip()
        if not state or not district_raw or not fec_id or fec_id.lower() == "n/a":
            continue                       # rule 2: aggregates and write-in scatter
        if state in NON_VOTING:
            continue

        # rule 3: keep the regular general, drop concurrent unexpired-term races
        upper = district_raw.upper()
        if "UNEXPIRED" in upper:
            continue
        number = re.match(r"\s*(\d+)", district_raw)
        if not number:
            continue
        district = _pad(number.group(1))   # rule 4

        seen.add((state, district))

        votes = row[i_gen]
        if votes in (None, ""):
            continue                       # rule 1: "Combined Parties:" carries no count
        try:
            votes = int(float(votes))
        except (TypeError, ValueError):
            continue                       # "Unopposed" and friends — see `seen`

        slot = per_candidate.setdefault((state, district), {}).setdefault(
            fec_id, {"votes": 0, "lines": set(), "incumbent": False})
        slot["votes"] += votes             # rule 1: sum fusion lines per candidate
        slot["lines"].add(str(row[i_party] or "").strip())
        if str(row[i_inc] or "").strip().upper().startswith("(I)"):
            slot["incumbent"] = True

    out = {key: {"dem_votes": 0, "rep_votes": 0, "other_votes": 0,
                 "incumbent_party": ""} for key in seen}
    for key, candidates in per_candidate.items():
        dem = rep = other = 0
        incumbent = ""
        for info in candidates.values():
            real, write_in = _party_tokens(info["lines"])
            # A real ballot line decides the candidate. Only if there is none
            # does the write-in wrapper get consulted, and then only to name
            # the party for the incumbent field — the votes still go to other,
            # because a write-in opponent does not make a race contested.
            if real & DEM_LINES:
                dem += info["votes"]
                party = "D"
            elif real & REP_LINES:
                rep += info["votes"]
                party = "R"
            else:
                other += info["votes"]
                if write_in & DEM_LINES:
                    party = "D"
                elif write_in & REP_LINES:
                    party = "R"
                else:
                    party = "I"
            # An open seat leaves this blank, which is the third state the
            # backtest needs — "no incumbent ran", not "the incumbent lost".
            if info["incumbent"]:
                incumbent = party
        out[key] = {"dem_votes": dem, "rep_votes": rep, "other_votes": other,
                    "incumbent_party": incumbent}
    return out


def two_party_margin(dem_votes, rep_votes):
    """D-minus-R as a percentage of the two-party vote, or None if uncontested.

    Two-party rather than all-party because that is the scale the model's own
    arithmetic assumes: district_lean_baseline turns a margin into shares with
    dem_share = 50 + margin/2, which only closes if the two shares sum to 100.
    """
    total = dem_votes + rep_votes
    if total == 0 or dem_votes == 0 or rep_votes == 0:
        return None
    return 100.0 * (dem_votes - rep_votes) / total


def fetch_results(out_dir=OUT_DIR):
    """Download and normalize every published FEC cycle."""
    written = {}
    for year, doc, sheet in FEC_CYCLES:
        path = _download_fec(year, doc, out_dir)
        districts = parse_fec_house(path, sheet)

        rows, uncontested = [], 0
        for (state, district) in sorted(districts):
            v = districts[(state, district)]
            margin = two_party_margin(v["dem_votes"], v["rep_votes"])
            if margin is None:
                uncontested += 1
            rows.append({
                "district":   f"{state}-{district}",
                "dem_votes":  v["dem_votes"],
                "rep_votes":  v["rep_votes"],
                "other_votes": v["other_votes"],
                # Blank, not 0.0 — an uncontested seat has no two-party margin,
                # and writing one would put a fake -100 into every variance the
                # backtest computes.
                "two_party_margin": "" if margin is None else f"{margin:.4f}",
                "contested":  "1" if margin is not None else "0",
                "incumbent_party": v["incumbent_party"],
            })

        out_path = os.path.join(out_dir, f"house_results_{year}.csv")
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["district", "dem_votes", "rep_votes",
                                              "other_votes", "two_party_margin",
                                              "contested", "incumbent_party"])
            w.writeheader()
            w.writerows(rows)
        written[year] = (len(rows), uncontested)
        print(f"  {year} results: {len(rows)} districts "
              f"({uncontested} uncontested) -> {os.path.basename(out_path)}")
    return written


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Writing to {OUT_DIR}\n")
    print("Lean vintages (538, pinned commits):")
    leans = fetch_lean_vintages()
    print("\nCertified results (FEC federalelections workbooks):")
    results = fetch_results()

    print(f"\n{len(leans)} lean vintage(s), {len(results)} result cycle(s).")
    print("2024 is absent: the FEC has not published federalelections2024.xlsx. "
          "backtest_house.py reports the consequence.")
    return leans, results


if __name__ == "__main__":
    build()
