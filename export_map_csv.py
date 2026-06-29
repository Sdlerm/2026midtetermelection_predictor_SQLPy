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
import argparse
import csv
from pathlib import Path

from senate_model import INDIE_CAUCUS, predict_all_races


DEFAULT_OUTPUT = Path(__file__).with_name("senate_map.csv")
FIELDNAMES = [
    "state",
    "winner_party",
    "winner_caucus",
    "winner_name",
    "winner_projected",
    "runner_up_party",
    "runner_up_name",
    "runner_up_projected",
    "winner_margin",
    "dem_name",
    "dem_projected",
    "rep_name",
    "rep_projected",
    "dem_margin",
    "flip",
    "incumbent_party",
]


def build_rows(predictions):
    grouped = {}
    for prediction in predictions:
        grouped.setdefault(prediction["state"], []).append(prediction)

    rows = []
    for state, candidates in sorted(grouped.items()):
        ranked = sorted(candidates, key=lambda candidate: candidate["projected"], reverse=True)
        winner = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None

        dem = next((candidate for candidate in ranked if candidate["party"] == "D"), None)
        rep = next((candidate for candidate in ranked if candidate["party"] == "R"), None)

        rows.append({
            "state": state,
            "winner_party": winner["party"],
            "winner_caucus": INDIE_CAUCUS.get(winner["party"], winner["party"]),
            "winner_name": winner["name"],
            "winner_projected": winner["projected"],
            "runner_up_party": runner_up["party"] if runner_up else "",
            "runner_up_name": runner_up["name"] if runner_up else "",
            "runner_up_projected": runner_up["projected"] if runner_up else "",
            "winner_margin": round(winner["projected"] - runner_up["projected"], 1) if runner_up else "",
            "dem_name": dem["name"] if dem else "",
            "dem_projected": dem["projected"] if dem else "",
            "rep_name": rep["name"] if rep else "",
            "rep_projected": rep["projected"] if rep else "",
            "dem_margin": round(dem["projected"] - rep["projected"], 1) if dem and rep else "",
            "flip": winner.get("is_flip", False),
            "incumbent_party": winner["incumbent_party"],
        })

    return rows


def export_csv(output_path):
    predictions, _, _ = predict_all_races()
    rows = build_rows(predictions)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export state-level Senate model predictions to a CSV file."
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path (default: {DEFAULT_OUTPUT.name})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    row_count = export_csv(output_path)
    print(f"Wrote {row_count} rows to {output_path}")