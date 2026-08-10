"""
fetch_district_lean.py — build data/district_lean.csv, the structural baseline
for House Tier 2.

Two inputs, one output:

  538 partisan-lean file  ─┐
                           ├─→  data/district_lean.csv   (generated, do not edit)
  district_lean_overrides ─┘

The overrides file is hand-maintained and always wins. That split is the whole
point: re-running this script re-downloads the base and rebuilds the output,
but never touches your corrections.

WHY OVERRIDES ARE NOT OPTIONAL HOUSEKEEPING
-------------------------------------------
538's district file is 2022 vintage on 2022 maps. The 2025 mid-decade redraws
(TX, NC, OH, FL — the same states dashboard.py flags as REDRAWN) changed lines
without changing seat counts, so the join succeeds silently and hands you
confidently wrong numbers for those districts. Every redrawn district needs a
hand-sourced override before its projection means anything. Unoverridden
redrawn districts are printed as a warning on every run, not buried.

BIAS LEDGER
-----------
  * Vintage: 538's lean is built from 2016/2020 presidential results. It has no
    2024 in it. Districts that swung hard in 2024 are mismeasured in whichever
    direction they swung.
  * Scale: 538's partisan lean is defined RELATIVE TO THE NATION; state_lean.csv
    stores a raw D margin. In a near-even national environment the two are within
    a point or so of each other, which is why they can share the same blend
    formula — but they are not the same quantity, and in a wave year the gap
    grows. Overrides sourced from raw presidential margins are on the raw scale.
    This is a known, unresolved inconsistency, not a resolved one.
"""

import csv
import os
import ssl
import urllib.request

DATA = os.path.join(os.path.dirname(__file__), "data")
OUT_PATH       = os.path.join(DATA, "district_lean.csv")
OVERRIDES_PATH = os.path.join(DATA, "district_lean_overrides.csv")

SOURCE_URL = (
    "https://raw.githubusercontent.com/fivethirtyeight/data/master/"
    "partisan-lean/fivethirtyeight_partisan_lean_DISTRICTS.csv"
)
SOURCE_COLUMN = "2022"          # the file's lone data column; named for its vintage
SOURCE_TAG    = "538-2022"

# States whose 2026 lines differ from the 2022 lines the source file describes.
# Kept in sync with dashboard.REDRAWN by hand — duplicated rather than imported
# because importing dashboard.py drags in streamlit for a data script.
REDRAWN = {"TX", "NC", "OH", "FL"}

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


def fetch_base(url=SOURCE_URL):
    """Download the 538 file and return {(state, district): margin}."""
    with _urlopen(url) as resp:
        text = resp.read().decode("utf-8")

    reader = csv.DictReader(text.splitlines())
    if SOURCE_COLUMN not in (reader.fieldnames or []):
        raise ValueError(
            f"source file has no {SOURCE_COLUMN!r} column (found {reader.fieldnames}). "
            "538 may have republished it under a different vintage — check the URL."
        )

    base = {}
    for row in reader:
        key = (row.get("district") or "").strip()
        value = (row.get(SOURCE_COLUMN) or "").strip()
        if not key or not value:
            continue
        base[_split_key(key)] = float(value)
    return base


def build(url=SOURCE_URL, out_path=OUT_PATH):
    base = fetch_base(url)
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

    stale_redrawn = sorted(
        f"{s}-{d}" for (s, d) in keys
        if s in REDRAWN and (s, d) not in overrides
    )
    if stale_redrawn:
        print(
            f"\nWARNING: {len(stale_redrawn)} district(s) in redrawn states still on "
            f"{SOURCE_TAG} lines.\nTheir lean describes boundaries that no longer exist. "
            f"Add rows to {os.path.basename(OVERRIDES_PATH)} as you source them:\n"
            f"  {', '.join(stale_redrawn)}"
        )

    return rows


if __name__ == "__main__":
    build()
