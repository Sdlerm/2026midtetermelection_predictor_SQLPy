import csv
from model import predict_all_races

def export_map_csv(path="map_data.csv", year=2026):
    predictions, _, _ = predict_all_races(year)

    # Collapse per-candidate rows into one row per state (a D/I row + an R row).
    # This is the SAME pattern as the `seen` dict in charts.py plot_vote_shares.
    seen = {}
    for r in predictions:
        seen.setdefault(r["state"], {})[r["party"]] = r

    rows = []
    for state, parties in seen.items():
        left = parties.get("D") or parties.get("I")   # NE carries through as I
        r = parties.get("R")
        if not left or not r:
            continue
        rows.append({
            "state_abbr": state,
            "margin": round(left["projected"] - r["projected"], 1),
            "leader": left["party"],
            # ... add dem_candidate, dem_pct, rep_candidate, rep_pct, flip
        })

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
