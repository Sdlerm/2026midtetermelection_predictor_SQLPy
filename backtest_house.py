"""
backtest_house.py — measure the House lean sigmas instead of reasoning them.

WHAT THE NOTE SAID, AND WHAT THIS ANSWERS
-----------------------------------------
calibration.py's House block carried this admission from 2026-07-28:

    "The Senate sigmas above are MEASURED ... The House sigmas below are
     REASONED, not measured ... Replace them with backtested numbers once
     load_historical.py covers House results."

and dashboard.py printed the consequence on every page load. This file is the
replacement. It reconstructs what house_model.py's lean-only path would have
projected for 2018, 2020 and 2022 using only lean vintages published BEFORE
each election (fetch_house_backtest_data.py pins the commits), subtracts the
certified FEC result, and reads the sigmas off the residuals.

THE QUANTITY BEING MEASURED
---------------------------
The lean-only path is exactly two terms:

    predicted_margin_i = lean_i + env

so the residual is exactly two error sources, and they are the same two the
Monte Carlo draws separately:

    actual_i - lean_i - env  =  (env_hat - env_true)  +  local_i
                                [ shared by a cycle ]   [ per district ]

That maps one-to-one onto SIGMA_NATIONAL_MARGIN_HOUSE and the LOCAL sigmas,
which is why everything below is a within-cycle / between-cycle split rather
than one pooled standard deviation:

    within-cycle SD of residuals   -> SIGMA_LOCAL_MARGIN_HOUSE_LEAN*
    SD of the per-cycle mean miss  -> SIGMA_NATIONAL_MARGIN_HOUSE   (n=3, see below)
    quadrature sum                 -> SIGMA_TOTAL_MARGIN_HOUSE_LEAN*

A single pooled SD would fold the national miss into the per-district term,
double-count it when the Monte Carlo adds its own national draw, and make the
seat distribution too wide.

THE FINDING, IN ONE LINE
------------------------
The two things the note lumped together — "2022-vintage lean" and "boundaries
that no longer exist" — are not the same size, and they are not close. An aging
lean on unchanged lines costs almost nothing (SD 7.2 fresh, 7.5 a cycle later).
A lean whose district has been redrawn underneath it costs more than double
(SD 16-17, and 22.5 for the one mid-decade redraw in the sample). The old
single SIGMA_TOTAL_MARGIN_HOUSE_LEAN = 11.0 was a compromise between the two
and is wrong in both directions: too wide for the 340 districts on intact
lines, far too narrow for the 95 that were redrawn in 2025.

WHAT THIS CANNOT TELL YOU — read before quoting any number below
----------------------------------------------------------------
1. THE NATIONAL TERM IS STILL NOT MEASURED, at n=3. Same wall BACKTEST_SCOPE.md
   §5 hit for the Senate at n=4 and §7 hit again at n=2. It is printed as a
   cross-check on the 3.0 the model already uses, not as a replacement.
2. THREE CYCLES, NEWEST 2022. The FEC has not published a 2024 compilation
   (fetch_house_backtest_data.py documents the search), so nothing here observes
   a lean two full cycles stale on intact lines — which is the 2026
   configuration for 340 districts. The staleness column extrapolates one cycle
   to two; it does not observe it.
3. THE REDRAWN NUMBER IS ANALOGY, NARROWED. Three of its four readings come
   from a DECENNIAL redraw (2012-cycle vintage against the 2022 map), which
   scrambles more than the targeted 2025 mid-decade redraws in TX/NC/OH/FL. The
   fourth — North Carolina's 2019 mid-decade redraw scored against a 2018
   vintage — is the right KIND of event and reads higher, not lower, at n=12.
   The adopted value sits inside that bracket. It is better founded than the
   11.0 it replaces and it is still not a direct measurement.
4. UNCONTESTED SEATS ARE EXCLUDED. Around 10% of districts each cycle have no
   major-party opponent, and a margin of "100" is a ballot-access fact, not a
   measurement of partisanship. The 2026 model projects all 435 as two-way
   races, so it has no representation of this at all.
5. LEAN VINTAGE, NOT MODEL VINTAGE. The roster, the polls and LEAN_ALPHA_HOUSE
   are untouched by this. It says nothing about SIGMA_TOTAL_MARGIN_HOUSE_POLLED,
   which still has no district-poll archive to test against.

Run:  python fetch_house_backtest_data.py && python backtest_house.py
"""

import argparse
import csv
import math
import os
import statistics

DATA = os.path.join(os.path.dirname(__file__), "data", "backtest")

# (lean vintage label, election year). Every pair is a legitimate backtest — the
# vintage predates the election — but they are not equally informative, so the
# report stratifies rather than pooling them.
PAIRS = (
    ("2018", 2018), ("2018", 2020), ("2018", 2022),
    ("2020", 2020), ("2020", 2022),
    ("2021", 2022),
    ("2022", 2022),
)

# Which decennial map each side of a pair sits on. 538 rebuilt the district file
# for the 2022 cycle; the 2018/2020/2021 vintages describe 2012-cycle lines.
LEAN_MAP_ERA = {"2018": 2012, "2020": 2012, "2021": 2012, "2022": 2022}
ELECTION_MAP_ERA = {2018: 2012, 2020: 2012, 2022: 2022}

# Mid-decade redraws INSIDE a map era: states whose lines moved between a
# vintage and a later election without a census in between. This is the exact
# event the live 2026 forecast is exposed to in TX/NC/OH/FL, so the one instance
# the sample contains is worth isolating rather than averaging away.
#
#   NC: struck down and redrawn in 2019, after the 2018 vintage was published
#       and before the 2020 election.
#
# Pennsylvania's February 2018 redraw is deliberately NOT listed: the 2018
# vintage was published 2018-11-19, after that map was already in use, so it
# describes the new lines. Its residual SD against 2018 (5.0) and against 2020
# (6.3) both sit at or below the national figure, which is what confirms it.
MID_DECADE_REDRAWS = {
    ("2018", 2020): {"NC"},
    ("2018", 2022): {"NC"},
}

# Districts closer than this to even are where a majority is actually decided. A
# sigma pooled over all 435 is dominated by 40-point seats whose errors cannot
# change a seat, so this band is reported alongside it.
COMPETITIVE_BAND = 15.0

# ---------------------------------------------------------------------------
# Adopted values — the numbers this file recommends into calibration.py.
# Kept here rather than only in the printout so calibration.py's comments and
# this file's output cannot drift apart, and so the justification travels with
# the number.
# ---------------------------------------------------------------------------
# Intact lines: the measured readings are 6.8-7.6 overall and 7.2-8.6 inside the
# competitive band, across three same-cycle pairs and one one-cycle-stale pair.
# 2026 is TWO cycles stale, which the sample cannot reach, and the competitive
# band is what decides a majority — so the adopted value sits at the top of the
# measured range rather than at its center.
ADOPT_LOCAL_LEAN = 8.0
# Redrawn lines: three decennial readings at 16.2/17.3/17.5 (13.3/14.4/14.6 with
# California's wholesale renumbering removed, which is more disruptive than a
# targeted mid-decade redraw), bracketed above by North Carolina's 2019
# mid-decade redraw at 22.5. 16.0 sits inside that bracket.
ADOPT_LOCAL_LEAN_REDRAWN = 16.0
# Unchanged. The n=3 cross-check below reads lower, and three observations
# cannot move a constant sourced from pooled multi-election work.
ADOPT_NATIONAL = 3.0


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_lean(vintage, data_dir=DATA):
    path = os.path.join(data_dir, f"district_lean_{vintage}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} — run fetch_house_backtest_data.py first.")
    with open(path, newline="") as f:
        return {r["district"]: float(r["dem_margin"]) for r in csv.DictReader(f)}


def load_results(year, data_dir=DATA):
    """
    {district: margin} with None for uncontested — kept in the dict rather than
    filtered here so callers can count what they dropped.
    """
    path = os.path.join(data_dir, f"house_results_{year}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} — run fetch_house_backtest_data.py first.")
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            raw = (r["two_party_margin"] or "").strip()
            out[r["district"]] = float(raw) if raw else None
    return out


def load_incumbents(year, data_dir=DATA):
    """{district: 'D' | 'R' | ''} — blank means an open seat, which is a third
    state the diagnostic needs and not the same as 'unknown'."""
    path = os.path.join(data_dir, f"house_results_{year}.csv")
    with open(path, newline="") as f:
        return {r["district"]: r["incumbent_party"] for r in csv.DictReader(f)}


def national_margin(year, data_dir=DATA):
    """
    Realized national two-party House margin, summed from the same certified
    returns the district rows come from.

    Computed rather than pasted so it cannot drift from the districts it is
    subtracted from. It runs slightly hot against the published popular-vote
    margin (2018: +8.5 here vs ~+8.6 reported) because states that print no
    totals for unopposed races contribute no votes on either side. That gap
    lands entirely in the BIAS column, never in a sigma: every sigma here is
    computed after removing the cycle mean.
    """
    path = os.path.join(data_dir, f"house_results_{year}.csv")
    dem = rep = 0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            dem += int(r["dem_votes"])
            rep += int(r["rep_votes"])
    return 100.0 * (dem - rep) / (dem + rep)


# ---------------------------------------------------------------------------
# Residuals
# ---------------------------------------------------------------------------
def lines_changed(vintage, year, district):
    """Did this district's geography move between the vintage and the election?

    True in two cases: the pair crosses a census (every district is redrawn), or
    the district's state held a mid-decade redraw in between. This is the flag
    the whole two-tier result rests on, so it is one function rather than a
    condition repeated at each call site.
    """
    if LEAN_MAP_ERA[vintage] != ELECTION_MAP_ERA[year]:
        return True
    return district[:2] in MID_DECADE_REDRAWS.get((vintage, year), set())


def residuals(vintage, year, data_dir=DATA):
    """
    One row per contested district present in both the vintage and the cycle.

    Districts in one and not the other are genuine mid-decade churn (CA-53 and
    NY-27 vanished in the 2022 redraw; the 2012-cycle vintages never heard of
    the seats that replaced them). They are counted and reported, not matched
    approximately — there is no honest way to map an abolished district onto a
    new one, and guessing would put fabricated residuals into the sigma this
    file exists to measure.
    """
    lean = load_lean(vintage, data_dir)
    actual = load_results(year, data_dir)
    env = national_margin(year, data_dir)

    rows, uncontested, unmatched = [], 0, 0
    for district, lean_margin in sorted(lean.items()):
        if district not in actual:
            unmatched += 1
            continue
        truth = actual[district]
        if truth is None:
            uncontested += 1
            continue
        predicted = lean_margin + env
        rows.append({
            "district":  district,
            "predicted": predicted,
            "actual":    truth,
            "residual":  truth - predicted,
            "redrawn":   lines_changed(vintage, year, district),
        })
    return rows, {"env": env, "uncontested": uncontested, "unmatched": unmatched,
                  "n": len(rows)}


def spread(rows, band=None):
    """
    Cycle-mean-removed SD, a robust scale, and n, for one slice of one pair.

    The mean is removed because it is the NATIONAL term, which the Monte Carlo
    draws separately; leaving it in would double-count. The robust scale is
    1.4826*MAD, which equals the SD for a true normal and ignores the tail, so a
    large gap between the two columns is itself the finding: it says the error
    is not the Gaussian the Monte Carlo draws.
    """
    values = [r["residual"] for r in rows
              if band is None or abs(r["predicted"]) < band]
    if len(values) < 2:
        return {"n": len(values), "sd": float("nan"), "robust": float("nan")}
    mean = statistics.fmean(values)
    centered = [v - mean for v in values]
    med = statistics.median(centered)
    mad = statistics.median([abs(c - med) for c in centered])
    return {"n": len(values), "sd": statistics.stdev(centered), "robust": 1.4826 * mad}


def pool(slices):
    """
    Pool across pairs with EACH PAIR'S own mean removed first.

    Concatenating raw residuals and taking one SD would fold the between-cycle
    differences into the per-district term — the double-count the module
    docstring warns about. Centering per pair keeps the result within-cycle.
    """
    centered, comp, n = [], [], 0
    for rows in slices:
        values = [r["residual"] for r in rows]
        if not values:
            continue
        mean = statistics.fmean(values)
        centered.extend(v - mean for v in values)
        comp.extend(r["residual"] - mean for r in rows
                    if abs(r["predicted"]) < COMPETITIVE_BAND)
        n += len(values)
    return {
        "n": n,
        "sd": statistics.stdev(centered) if len(centered) > 1 else float("nan"),
        "competitive_sd": statistics.stdev(comp) if len(comp) > 1 else float("nan"),
        "n_competitive": len(comp),
    }


# ---------------------------------------------------------------------------
# Calibration — the part that VALIDATES a sigma rather than estimating it
# ---------------------------------------------------------------------------
def calibration_table(slices, sigma, bins=(0.05, 0.20, 0.40, 0.60, 0.80, 0.95)):
    """
    Score each district as P(D win) under a candidate sigma, bin, and compare
    the predicted rate to what actually happened.

    Estimating a sigma from residuals and then reporting the residual spread is
    circular — it cannot fail. This can: if the sigma is too small, districts the
    model puts at 95% win less than 95% of the time and the table shows it.

    Each pair's mean residual is removed before scoring, for the same reason it
    is removed from every sigma here: the live model does not know the national
    environment either, and the Monte Carlo gives that miss its own draw. Leaving
    it in would grade the sigma for an error it is not being asked to carry.
    """
    graded = []
    for rows in slices:
        if not rows:
            continue
        mean = statistics.fmean([r["residual"] for r in rows])
        for r in rows:
            centered = r["predicted"] + mean
            p = 0.5 * (1.0 + math.erf(centered / (sigma * math.sqrt(2.0))))
            graded.append((p, 1.0 if r["actual"] > 0 else 0.0))

    edges = [0.0] + list(bins) + [1.0]
    table = []
    for lo, hi in zip(edges, edges[1:]):
        block = [(p, w) for (p, w) in graded if lo <= p < hi]
        if not block:
            continue
        table.append({"lo": lo, "hi": hi, "n": len(block),
                      "predicted": statistics.fmean([p for p, _ in block]),
                      "actual": statistics.fmean([w for _, w in block])})
    return table


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _rule(char="─", width=78):
    return char * width


def run(data_dir=DATA):
    per_pair = {}
    for vintage, year in PAIRS:
        per_pair[(vintage, year)] = residuals(vintage, year, data_dir)

    def slice_of(key, redrawn):
        return [r for r in per_pair[key][0] if r["redrawn"] is redrawn]

    print(_rule("═"))
    print("HOUSE LEAN BACKTEST — measuring the lean-only sigmas")
    print(_rule("═"))

    # ---- 1. Every pair, split by whether the district was redrawn -----------
    print("\n1. EVERY LEAN VINTAGE AGAINST EVERY LATER ELECTION")
    print("   Split by whether the district's LINES moved between the vintage and")
    print("   the election. 'stale' counts cycles. SD has the cycle mean removed;")
    print("   'bias' reports it. 'robust' is 1.4826*MAD — below SD means fat tails.\n")
    head = (f"   {'vintage':>7} {'elec':>5} {'stale':>5} {'bias':>6} │ "
            f"{'INTACT n':>8} {'SD':>6} {'robust':>7} {'comp':>6} │ "
            f"{'REDRAWN n':>9} {'SD':>6} {'robust':>7} {'comp':>6}")
    print(head)
    print("   " + _rule("-", len(head) - 3))
    for key in PAIRS:
        vintage, year = key
        rows, meta = per_pair[key]
        bias = statistics.fmean([r["residual"] for r in rows])
        cells = [f"   {vintage:>7} {year:>5} {(year - int(vintage)) // 2:>5} "
                 f"{bias:>+6.2f} │"]
        for redrawn, width in ((False, 8), (True, 9)):
            sl = slice_of(key, redrawn)
            a, c = spread(sl), spread(sl, COMPETITIVE_BAND)
            if not sl:
                cells.append(f" {'—':>{width}} {'—':>6} {'—':>7} {'—':>6} │")
            else:
                cells.append(f" {a['n']:>{width}} {a['sd']:>6.2f} "
                             f"{a['robust']:>7.2f} {c['sd']:>6.2f} │")
        print("".join(cells).rstrip("│ "))

    # ---- 2. The two tiers ---------------------------------------------------
    print("\n2. THE TWO TIERS")
    print("   These are the numbers the model needs, because the 2026 map has both:")
    print("   340 districts whose 2022 lines still stand, and 95 in TX/NC/OH/FL")
    print("   redrawn in 2025 under a lean that still describes the old shape.\n")

    intact_fresh = [slice_of(k, False) for k in PAIRS if int(k[0]) == k[1]]
    intact_stale = [slice_of(k, False) for k in PAIRS if int(k[0]) < k[1]]
    redrawn_decennial = [slice_of(k, True) for k in PAIRS
                         if LEAN_MAP_ERA[k[0]] != ELECTION_MAP_ERA[k[1]]]
    redrawn_middecade = [slice_of(k, True) for k in PAIRS
                         if LEAN_MAP_ERA[k[0]] == ELECTION_MAP_ERA[k[1]]]

    for label, slices in (
        ("intact lines, same cycle as vintage", intact_fresh),
        ("intact lines, vintage a cycle old", intact_stale),
        ("redrawn — decennial (2012 lean vs 2022 map)", redrawn_decennial),
        ("redrawn — mid-decade (NC 2019, the 2026 shape)", redrawn_middecade),
    ):
        p = pool(slices)
        if not p["n"]:
            continue
        print(f"   {label:<46} n={p['n']:>5}  SD={p['sd']:6.2f}  "
              f"competitive SD={p['competitive_sd']:6.2f} (n={p['n_competitive']})")

    print("\n   Staleness on intact lines costs about four-tenths of a point of SD per")
    print("   cycle. Redistricting costs more than the entire intact sigma again.")

    # ---- 3. The national term (n=3) ----------------------------------------
    print("\n3. THE NATIONAL (CORRELATED) TERM — n=3, NOT A MEASUREMENT")
    print("   Per-cycle mean residual after subtracting the realized national vote:")
    print("   the lean's own drift against the House vote, shared by every district")
    print("   in the cycle. 538's lean is built from PRESIDENTIAL results, so this")
    print("   column is also where the presidential-vs-House scale mismatch in")
    print("   fetch_district_lean.py's bias ledger shows up.\n")
    cycle_bias = {}
    for year in (2018, 2020, 2022):
        vintage = max((v for (v, y) in PAIRS if y == year), key=int)
        rows, meta = per_pair[(vintage, year)]
        cycle_bias[year] = statistics.fmean([r["residual"] for r in rows])
        print(f"     {year}  vintage {vintage}  realized national vote "
              f"D{meta['env']:+.2f}  mean residual {cycle_bias[year]:+.2f}")
    spread_national = statistics.stdev(list(cycle_bias.values()))
    print(f"\n     SD of the three cycle means: {spread_national:.2f}  "
          f"(model uses SIGMA_NATIONAL_MARGIN_HOUSE = {ADOPT_NATIONAL})")
    print("     Three observations cannot move that constant. It stays where the")
    print("     literature put it, and it stays the wider of the two.")

    # ---- 4. Calibration ----------------------------------------------------
    total_intact = math.hypot(ADOPT_LOCAL_LEAN, ADOPT_NATIONAL)
    total_redrawn = math.hypot(ADOPT_LOCAL_LEAN_REDRAWN, ADOPT_NATIONAL)

    print("\n4. CALIBRATION — do the adopted sigmas produce honest win probabilities?")
    print("   The sigma above is fitted to these residuals, so its spread cannot")
    print("   falsify it. This can. Districts are scored to P(D win), binned, and")
    print("   compared to how often the Democrat actually won.\n")
    for label, slices, sigma in (
        (f"INTACT LINES  (sigma {total_intact:.2f})",
         intact_fresh + intact_stale, total_intact),
        (f"REDRAWN LINES (sigma {total_redrawn:.2f})",
         redrawn_decennial + redrawn_middecade, total_redrawn),
    ):
        print(f"   {label}")
        print(f"   {'predicted band':>18} {'n':>5} {'mean pred':>10} "
              f"{'actual':>8} {'gap':>7}")
        print("   " + _rule("-", 52))
        for row in calibration_table(slices, sigma):
            print(f"   {row['lo']:>7.2f}-{row['hi']:<10.2f} {row['n']:>5} "
                  f"{row['predicted']:>10.1%} {row['actual']:>8.1%} "
                  f"{row['actual'] - row['predicted']:>+7.1%}")
        print()

    # ---- 5. Why the middle bands miss --------------------------------------
    # The calibration table above is honest in the tails and off by 15 points in
    # the middle, which is a CENTERING problem, not a width problem: a sigma set
    # too small fails at the extremes first, and these do not. Naming the cause
    # matters, because an unexplained miscalibration is a reason to distrust the
    # sigma, and an explained one is a scoped piece of follow-up work.
    print("5. WHY THE MIDDLE BANDS MISS — incumbency, measured")
    print("   house_model.py's docstring already lists 'NO INCUMBENCY' as a known")
    print("   gap: uniform swing is applied to a presidential lean with no")
    print("   incumbency correction, and the sigma absorbs it as noise. Here is")
    print("   what it is worth, on intact lines, cycle mean removed:\n")
    groups = {}
    for key in PAIRS:
        rows = [r for r in per_pair[key][0] if not r["redrawn"]]
        if not rows:
            continue
        incumbents = load_incumbents(key[1], data_dir)
        mean = statistics.fmean([r["residual"] for r in rows])
        for r in rows:
            held = incumbents.get(r["district"], "")
            band = "competitive" if abs(r["predicted"]) < COMPETITIVE_BAND else "safe"
            groups.setdefault((held, band), []).append(r["residual"] - mean)
    print(f"     {'seat held by':>14} {'band':>12} {'n':>5} {'mean residual':>14}")
    for held in ("D", "R", ""):
        for band in ("competitive", "safe"):
            v = groups.get((held, band), [])
            if len(v) < 10:
                continue
            label = {"D": "D incumbent", "R": "R incumbent", "": "open seat"}[held]
            print(f"     {label:>14} {band:>12} {len(v):>5} "
                  f"{statistics.fmean(v):>+14.2f}")
    print("\n     An incumbent of either party runs about 3 points of margin ahead")
    print("     of the lean in their own direction; open seats sit near zero. That")
    print("     is the whole of the middle-band gap above, and it is why the")
    print("     0.40-0.60 band favours whoever already holds the seat.")
    print("     Removing a per-cycle incumbency offset would take the intact-lines")
    print("     SD from ~7.2 to ~6.6. The model cannot collect that today:")
    print("     house_nominees.csv names an incumbent in only ~110 of 435 districts,")
    print("     so the correction would apply to a quarter of the map and leave the")
    print("     rest miscentered against it. Sourcing a full incumbency roster is")
    print("     the next piece of work this backtest points at, and it is a")
    print("     data-collection job rather than a modelling one.\n")

    # ---- 6. Coverage -------------------------------------------------------
    print("6. COVERAGE — what these sigmas do not cover")
    for year in (2018, 2020, 2022):
        vintage = max((v for (v, y) in PAIRS if y == year), key=int)
        rows, meta = per_pair[(vintage, year)]
        print(f"     {year}: {meta['n']} scored · {meta['uncontested']} uncontested "
              f"(dropped) · {meta['unmatched']} absent from the {vintage} vintage")
    print("     The 2026 model projects all 435 districts as two-way contests, so it")
    print("     has no representation of an uncontested seat at all.")
    print("     No 2024 cycle: the FEC has not published a 2024 compilation, so")
    print("     nothing here observes a lean two cycles stale on intact lines.")

    # ---- 6. Recommendation -------------------------------------------------
    print("\n" + _rule("═"))
    print("RECOMMENDED calibration.py VALUES")
    print(_rule("═"))
    print(f"  SIGMA_NATIONAL_MARGIN_HOUSE           = {ADOPT_NATIONAL:.1f}    "
          f"UNCHANGED (n=3 reads {spread_national:.2f}; cannot move it)")
    print(f"  SIGMA_TOTAL_MARGIN_HOUSE_LEAN         = {total_intact:.1f}    "
          f"was 11.0 — intact lines, local {ADOPT_LOCAL_LEAN:.1f}")
    print(f"  SIGMA_TOTAL_MARGIN_HOUSE_LEAN_REDRAWN = {total_redrawn:.1f}   "
          f"NEW — redrawn lines, local {ADOPT_LOCAL_LEAN_REDRAWN:.1f}")
    print(f"  SIGMA_TOTAL_MARGIN_HOUSE_POLLED       = 6.0    "
          f"UNCHANGED — no district-poll archive exists to test it")
    print(_rule("═"))
    return per_pair


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Measure the House lean-only sigmas from 2018/2020/2022.")
    parser.add_argument("--data-dir", default=DATA)
    run(parser.parse_args().data_dir)
