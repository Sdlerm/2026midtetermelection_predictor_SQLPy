import csv
from senate_model import predict_all_races

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
        winner = left if left["projected"] >= r["projected"] else r
        rows.append({
            "state_abbr": state,
            "margin": round(left["projected"] - r["projected"], 1),
            "leader": winner["party"],
            "dem_candidate": left["name"],
            "dem_pct": left["projected"],
            "rep_candidate": r["name"],
            "rep_pct": r["projected"],
            "flip": winner.get("is_flip", False)
        })

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {path}")
    return path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "map_data.csv"
    yr = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    export_map_csv(out, yr)