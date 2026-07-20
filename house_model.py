"""
house_model.py — Tier 1 projections for polled House districts.

V1 SCOPE (deliberate):
  * Polls-only: LEAN_ALPHA_HOUSE = 1.0. No district structural lean exists
    yet (needs CPVI on post-redistricting maps). The blend line is written
    as if lean existed so that sourcing district_lean.csv later is a
    one-constant change. Bias ledger: no partisan skew was introduced; cost is
    overconfidence where polling is thin.
  * NO chamber-control projection. 39 polled districts out of 435 cannot
    say who holds the House, and this file refuses to pretend otherwise.
    That number arrives with Tier 2 (CPVI + generic ballot + climate).
"""

from init_db import get_connection
from senate_model import (
    weighted_average_and_stderr,
    get_climate_score, climate_adjustment,
    get_approval_score, approval_adjustment,
)
from house_ingest import load_house_nominees

LEAN_ALPHA_HOUSE = 1.0   # polls-only until district_lean.csv exists (see docstring)


def _district_lean():
    """Placeholder: returns neutral 50.0 until CPVI-on-new-maps data is
    sourced. With LEAN_ALPHA_HOUSE = 1.0 this value gets 0% weight, so it
    cannot affect output — it exists so the blend formula below is already
    the final shape."""
    return 50.0


def predict_house_races(year=2026):
    roster, _skipped = load_house_nominees()
    climate  = get_climate_score(year)
    approval = get_approval_score(year)

    con = get_connection()
    cur = con.cursor()
    cur.execute(
        "SELECT id, state, district FROM races WHERE year = ? AND district != ''",
        (year,),
    )
    races = cur.fetchall()

    results = []
    for race_id, state, district in races:
        finalists = []
        parties = [p for (s, d, p) in roster if s == state and d == district]

        for party in parties:
            info = roster[(state, district, party)]
            cur.execute(
                "SELECT id FROM candidates WHERE race_id = ? AND name = ?",
                (race_id, info["name"]),
            )
            row = cur.fetchone()
            if not row:
                continue                     # nominee never appeared in polls — skip (scripture rule)
            poll_avg, poll_stderr = weighted_average_and_stderr(race_id, row[0])
            if poll_avg is None:
                continue

            econ = climate_adjustment(party, climate)
            appr = approval_adjustment(party, approval)
            lean = _district_lean()
            blended   = LEAN_ALPHA_HOUSE * poll_avg + (1 - LEAN_ALPHA_HOUSE) * lean
            projected = round(blended + econ + appr, 2)

            finalists.append({
                "state": state, "district": district,
                "race": f"{state}-{district}",
                "name": info["name"], "party": party,
                "poll_avg": poll_avg, "econ": econ, "appr": appr,
                "projected": projected,
                "poll_stderr": poll_stderr,
                "is_incumbent": info.get("is_incumbent", False),
            })

        if len(finalists) < 2:
            continue                          # can't project a one-sided race

        finalists.sort(key=lambda f: f["projected"], reverse=True)
        finalists[0]["winner"] = True
        for f in finalists[1:]:
            f["winner"] = False

        incumbent_party = next((f["party"] for f in finalists if f["is_incumbent"]), "")
        w = finalists[0]
        w["is_flip"] = bool(incumbent_party) and w["party"] != incumbent_party
        for f in finalists[1:]:
            f["is_flip"] = False

        results.extend(finalists)

    con.close()
    return results, climate, approval


if __name__ == "__main__":
    results, climate, approval = predict_house_races()

    print(f"Climate score: {climate:+.3f} · Approval score: {approval:+.2f}")
    print(f"Tier 1 (polled districts only) — NOT a chamber projection\n")

    d_leads = r_leads = flips = 0
    current = None
    for r in sorted(results, key=lambda x: x["race"]):
        if r["race"] != current:
            current = r["race"]
            print(f"\n── {current} ──────────────")
        marker = "★" if r.get("winner") else " "
        inc    = " [incumbent]" if r["is_incumbent"] else ""
        flip   = " ⚡FLIP" if r.get("winner") and r.get("is_flip") else ""
        print(f"  {marker} {r['party']}  {r['name']:<28} poll: {r['poll_avg']:.1f}% ±{r['poll_stderr']:.1f}%  "
              f"econ: {r['econ']:+.2f}pp  appr: {r['appr']:+.2f}pp  → {r['projected']:.1f}%{inc}{flip}")
        if r.get("winner"):
            d_leads += r["party"] == "D"
            r_leads += r["party"] == "R"
            flips   += r.get("is_flip", False)

    print(f"\n{'─'*45}")
    print(f"  Tier 1 districts projected: {d_leads + r_leads}")
    print(f"  D leads: {d_leads}  ·  R leads: {r_leads}  ·  flips: {flips}")
    print(f"  ({435 - d_leads - r_leads} districts unmodeled — Tier 2 pending)")
    print(f"{'─'*45}")