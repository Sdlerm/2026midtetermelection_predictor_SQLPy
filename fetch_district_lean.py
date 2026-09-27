"""
fetch_district_lean.py — build data/district_lean.csv, the structural baseline
for House Tier 2.

Two inputs, one output:

  Downballot 2024 pres by CD  ─┐
  (2026 lines)                  ├─→  data/district_lean.csv   (generated, do not edit)
  district_lean_overrides     ─┘

The overrides file is hand-maintained and always wins. That split is the whole
point: re-running this script re-downloads the base and rebuilds the output,
but never touches your corrections.

THE BASE
--------
The Downballot's calculation of the 2024 presidential result in every House
district on the lines that will be used in 2026 (released July 2026; it covers
all ten states with new maps — TX, NC, OH, FL, CA, UT, AL, LA, TN, and MO,
where the sheet uses the pre-redraw lines, matching the old map staying in
force for November). Exact vote totals, not the rounded display tab.

It replaced 538's partisan-lean file, which was 2022 vintage on 2022 maps: stale
not just in the 2025-26 redraw states but in AL, LA, GA and NY, which were
redrawn again before 2024 (AL-02 read R+33 on a seat Democrats have held since
2024). Overrides remain for anything the sheet gets wrong or a late map change.

BIAS LEDGER
-----------
  * Scale: converted to 538's convention — RELATIVE TO THE NATION — by
    subtracting the national 2024 D margin (computed from the sheet's own
    totals, ≈ -1.65). house_model then adds the 2026 environment on top; a raw
    margin would carry 2024's R+1.65 environment into the lean and count part
    of the national swing twice. Overrides must be on this relative scale too.
  * One election, one candidate: 538 blended 2016/2020 presidential with
    state-legislative results; this is 2024 alone. Districts whose 2024 swing
    was Trump-specific (heavily Hispanic South Texas/South Florida seats, for
    one) may revert in a midterm without him on the ballot, which a single-
    cycle lean cannot anticipate.
  * The House sigmas were measured (backtest_house.py) on presidential-based
    leans aged one to three cycles. A fresh 2024 lean on current lines is the
    one-cycle-stale, intact-lines case, so SIGMA_*_HOUSE_LEAN applies.
"""

import csv
import os
import ssl
import urllib.request

from calibration import CURRENT_LINES_SOURCES

DATA = os.path.join(os.path.dirname(__file__), "data")
OUT_PATH       = os.path.join(DATA, "district_lean.csv")
OVERRIDES_PATH = os.path.join(DATA, "district_lean_overrides.csv")

# "Exact vote totals" tab of The Downballot's 2026-lines sheet, as CSV.
# Human-readable page: https://www.the-downballot.com/p/the-downballots-calculations-of-presidential
SOURCE_URL = (
    "https://docs.google.com/spreadsheets/d/1eZfaFI-c-PFOoKx1-zZA2MP0_dxRq_LVK0re3BOQqy0/"
    "export?format=csv&gid=1491069057"
)
SOURCE_TAG = "downballot-2024"   # must stay in calibration.CURRENT_LINES_SOURCES

EXPECTED_DISTRICTS = 435


def _pad(district):
    """'1' -> '01'. Same normalization as house_ingest._pad, so the keys this
    file writes join cleanly against the ones the DB stores."""
    return f"{int(float(district)):02d}"


def _split_key(key):
    """'AK-1' -> ('AK', '01'). Raises on anything that isn't STATE-NUMBER."""
    state, _, district = key.partition("-")
    state = state.strip().upper()
    if len(state) != 2 or not district:
        raise ValueError(f"unparseable district key: {key!r}")
    return state, _pad(district)


def load_overrides(path=OVERRIDES_PATH):
    """
    Read the hand-maintained corrections. Missing file is fine — it just means
    no corrections yet. Blank margins are skipped rather than parsed as 0.0,
    so you can park a district in the file with a note before you've sourced
    its number.

    Returns {(state, district): (margin, note)}.
    """
    overrides = {}
    if not os.path.exists(path):
        return overrides

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("district") or "").strip()
            if not key or key.startswith("#"):
                continue
            margin = (row.get("dem_margin") or "").strip()
            if not margin:
                continue                      # parked, not yet sourced
            overrides[_split_key(key)] = (float(margin), (row.get("note") or "").strip())
    return overrides


def _urlopen(url):
    """
    urlopen, with one retry through certifi's CA bundle.

    This Python install trusts no CAs by default (the python.org installer's
    "Install Certificates.command" step never ran), so the first attempt dies
    on CERTIFICATE_VERIFY_FAILED. certifi is already a dependency, so the retry
    is free. The retry is NOT an unverified context — verification still
    happens, just against a bundle Python can actually find. If certifi is
    missing too, the original SSL error propagates rather than being swallowed.

    urllib wraps the SSL failure inside URLError, so the cert case has to be
    identified via __cause__ — catching ssl.SSLCertVerificationError directly
    never fires. Any non-cert URLError (DNS, timeout, refused) re-raises
    untouched: a certifi retry can't fix those and would only obscure them.
    """
    try:
        return urllib.request.urlopen(url, timeout=60)
    except urllib.error.URLError as err:
        if not isinstance(getattr(err, "reason", None), ssl.SSLCertVerificationError):
            raise
        try:
            import certifi
        except ImportError:
            raise
        print("NOTE: system CA store unusable — verifying via certifi instead.")
        return urllib.request.urlopen(
            url, timeout=60, context=ssl.create_default_context(cafile=certifi.where())
        )


def _parse_votes(cell):
    """'140,026' -> 140026.0; blank -> None."""
    cell = (cell or "").strip().replace(",", "")
    return float(cell) if cell else None


def fetch_base(url=SOURCE_URL):
    """
    Download the Downballot sheet and return ({(state, district): lean}, national_margin).

    lean is the district's 2024 D-minus-R margin MINUS the national one, in points.
    The sheet has two banner rows and a two-row header; columns are
    District, Incumbent, Party, (blank), Harris, Trump, Total, ... — the positions
    are checked against the header rather than trusted.
    """
    with _urlopen(url) as resp:
        text = resp.read().decode("utf-8")

    rows = list(csv.reader(text.splitlines()))
    if len(rows) < 5 or rows[2][:1] != ["District"] or rows[3][4:7] != ["Harris", "Trump", "Total"]:
        raise ValueError(
            "Downballot sheet layout changed (expected District / Harris, Trump, Total "
            f"in columns 0 / 4-6; got {rows[2:4]}). Check the tab still at {url}."
        )

    margins, H, T, TOT = {}, 0.0, 0.0, 0.0
    for row in rows[4:]:
        key = (row[0] if row else "").strip()
        if not key:
            continue
        harris, trump, total = (_parse_votes(c) for c in row[4:7])
        if not total:
            continue
        H, T, TOT = H + harris, T + trump, TOT + total
        margins[_split_key(key.replace("-AL", "-1"))] = (harris - trump) / total * 100

    national = (H - T) / TOT * 100
    return {k: m - national for k, m in margins.items()}, national


def build(url=SOURCE_URL, out_path=OUT_PATH):
    base, national = fetch_base(url)
    print(f"National 2024 D margin from the sheet: {national:+.2f} (leans are relative to it)")
    overrides = load_overrides()

    if len(base) != EXPECTED_DISTRICTS:
        print(f"WARNING: source returned {len(base)} districts, expected {EXPECTED_DISTRICTS}")

    # Overrides may introduce districts the base file lacks — that's how a
    # newly created district gets in. Union, don't intersect.
    keys = sorted(set(base) | set(overrides))

    rows = []
    for key in keys:
        state, district = key
        if key in overrides:
            margin, note = overrides[key]
            source = "override"
        else:
            margin, note, source = base[key], "", SOURCE_TAG
        rows.append({
            "district":   f"{state}-{district}",
            "dem_margin": f"{margin:.2f}",
            "source":     source,
            "note":       note,
        })

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["district", "dem_margin", "source", "note"])
        writer.writeheader()
        writer.writerows(rows)

    # ---- Summary: every gap is loud, none is fatal ----
    n_override = sum(1 for r in rows if r["source"] == "override")
    print(f"Wrote {len(rows)} districts to {out_path}")
    print(f"  {len(rows) - n_override} from {SOURCE_TAG}  ·  {n_override} hand-override(s)")

    stale = sorted(r["district"] for r in rows if r["source"] not in CURRENT_LINES_SOURCES)
    if stale:
        print(
            f"\nWARNING: {len(stale)} district(s) with a lean from a source not known to be "
            f"on 2026 lines (calibration.CURRENT_LINES_SOURCES):\n  {', '.join(stale)}"
        )

    return rows


if __name__ == "__main__":
    build()
