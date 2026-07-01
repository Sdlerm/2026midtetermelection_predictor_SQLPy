#!/usr/bin/env python3
"""
Lightweight backtesting / calibration script for the 2026 Senate forecaster.

Purpose
-------
Gives you a quick diagnostic on whether the full model (polls + lean + econ +
approval) is adding value over a pure structural baseline. Results are grouped
by election year (2018, 2020, 2022, 2024) so you can see performance patterns
across cycles.

Limitations (important)
-----------------------
- We are comparing 2026-model projections (current nominees.csv + current polling
  + current climate/approval) against historical actual results from past cycles.
- Nominees were different in most states in older cycles. The mismatch grows
  larger the further back you go (2024 → 2018).
- The polling data in senate.csv contains a mix of cycles; late polls from
  each cycle may be present.
- Therefore this is NOT a true hindcast. It is a sanity check + calibration
  signal: "How does the current model + polling perform when tested against
  past election outcomes?"

Run
---
    python validate_model.py

Output format
-------------
- One clearly separated section per year with overlap
- Per-year table + MAE / winner accuracy summary
- Overall combined summary at the bottom
- Top 5 largest errors across all years (with year + state for context)
"""

import sqlite3
import os
import math
from collections import defaultdict

from senate_model import predict_all_races, lean_baseline, load_state_lean
from init_db import get_connection


def get_historical_results(years=None):
    """
    Returns a nested dict: year -> state -> {"winner_party": "D"/"R", "margin": float (D - R or I - R)}

    Includes all Senate races from the specified years that have a clear D vs R
    (or I caucusing with D) outcome in historical_results.

    If years is None, defaults to [2018, 2020, 2022, 2024].
    """
    if years is None:
        years = [2018, 2020, 2022, 2024]

    con = get_connection()
    cur = con.cursor()

    # Build the IN clause safely
    placeholders = ",".join("?" for _ in years)
    query = f"""
        SELECT r.year, r.state,
               c.party,
               hr.vote_share,
               hr.won
        FROM historical_results hr
        JOIN races r ON hr.race_id = r.id
        JOIN candidates c ON hr.candidate_id = c.id
        WHERE r.year IN ({placeholders})
        ORDER BY r.year, r.state, hr.won DESC
    """
    cur.execute(query, years)
    rows = cur.fetchall()
    con.close()

    # year -> state -> {party: info}
    by_year = defaultdict(lambda: defaultdict(dict))
    for year, state, party, vote_share, won in rows:
        by_year[year][state][party] = {"vote_share": vote_share, "won": bool(won)}

    out = defaultdict(dict)
    for year in sorted(by_year.keys()):
        for state, parties in by_year[year].items():
            if "D" in parties and "R" in parties:
                d = parties["D"]["vote_share"]
                r = parties["R"]["vote_share"]
                margin = d - r
                winner_party = "D" if parties["D"]["won"] else "R"
                out[year][state] = {"winner_party": winner_party, "margin": round(margin, 1)}
            elif "I" in parties and "R" in parties:
                i = parties["I"]["vote_share"]
                r = parties["R"]["vote_share"]
                margin = i - r
                winner_party = "D" if parties["I"]["won"] else "R"
                out[year][state] = {"winner_party": winner_party, "margin": round(margin, 1)}

    return out


def main():
    print("Running current model predictions (2026 nominees + latest polls + climate)...")
    predictions, climate, _ = predict_all_races(year=2026)
    state_lean = load_state_lean()

    # Group predictions by state (top 2 only, same logic as dashboard)
    pred_by_state = defaultdict(list)
    for p in predictions:
        pred_by_state[p["state"]].append(p)

    for s in pred_by_state:
        pred_by_state[s].sort(key=lambda x: x["projected"], reverse=True)

    hist_by_year = get_historical_results()  # defaults to 2018,2020,2022,2024

    # Collect all years that have any overlap with current projections
    all_years = sorted(hist_by_year.keys())
    years_with_overlap = []
    for year in all_years:
        overlap_states = set(pred_by_state.keys()) & set(hist_by_year[year].keys())
        if overlap_states:
            years_with_overlap.append(year)

    if not years_with_overlap:
        print("No overlap found between current projections and any historical results in the database.")
        return

    print(f"\nYears with overlapping states: {years_with_overlap}")
    print(f"Total unique states across all years: {len(set().union(*[set(hist_by_year[y].keys()) for y in years_with_overlap]))}")

    full_model_errors_all = []
    lean_only_errors_all = []
    winner_full_all = 0
    winner_lean_all = 0
    total_all = 0

    for year in years_with_overlap:
        hist = hist_by_year[year]
        overlap = sorted(set(pred_by_state.keys()) & set(hist.keys()))

        print(f"\n{'='*80}")
        print(f"  {year} SENATE RACES  —  {len(overlap)} states with overlap")
        print(f"{'='*80}")

        print(f"\n{'State':<6} {'Full proj':>8} {'Lean-only':>9} {'Actual':>8} {'Full err':>9} {'Lean err':>9}  Winner?")
        print("-" * 75)

        year_full_errors = []
        year_lean_errors = []
        year_winner_full = 0
        year_winner_lean = 0
        year_total = 0

        for state in overlap:
            preds = pred_by_state[state]
            if len(preds) < 2:
                continue

            d_pred = next((p for p in preds if p["party"] in ("D", "I")), None)
            r_pred = next((p for p in preds if p["party"] == "R"), None)
            if not d_pred or not r_pred:
                continue

            full_margin = d_pred["projected"] - r_pred["projected"]

            # Lean-only baseline for same two candidates
            lean_d = lean_baseline(state, d_pred["party"], state_lean)
            lean_r = lean_baseline(state, "R", state_lean)
            lean_margin = lean_d - lean_r

            actual = hist[state]["margin"]
            actual_winner = hist[state]["winner_party"]

            full_err = abs(full_margin - actual)
            lean_err = abs(lean_margin - actual)

            year_full_errors.append(full_err)
            year_lean_errors.append(lean_err)
            full_model_errors_all.append(full_err)
            lean_only_errors_all.append(lean_err)
            year_total += 1
            total_all += 1

            # Winner call (using sign of margin)
            full_winner_correct = (full_margin > 0) == (actual > 0)
            lean_winner_correct = (lean_margin > 0) == (actual > 0)
            if full_winner_correct:
                year_winner_full += 1
                winner_full_all += 1
            if lean_winner_correct:
                year_winner_lean += 1
                winner_lean_all += 1

            winner_str = "✓" if full_winner_correct else "✗"
            if not full_winner_correct:
                winner_str += f" (actual {actual_winner})"

            print(f"{state:<6} {full_margin:>+7.1f}  {lean_margin:>+8.1f}  {actual:>+7.1f}  "
                  f"{full_err:>8.1f}  {lean_err:>8.1f}   {winner_str}")

        if year_total == 0:
            print("  (No comparable two-candidate races in this year)")
            continue

        # Per-year summary
        mae_full_y = sum(year_full_errors) / year_total
        mae_lean_y = sum(year_lean_errors) / year_total
        acc_full_y = year_winner_full / year_total * 100
        acc_lean_y = year_winner_lean / year_total * 100

        print(f"\n  {year} Summary:")
        print(f"    Full model   MAE: {mae_full_y:.2f} pp   Winner acc: {acc_full_y:.1f}%  (n={year_total})")
        print(f"    Lean-only    MAE: {mae_lean_y:.2f} pp   Winner acc: {acc_lean_y:.1f}%  (n={year_total})")
        print(f"    Δ (full - lean) MAE: {mae_full_y - mae_lean_y:+.2f} pp")

    # Overall summary across all years
    if total_all == 0:
        print("\nNo comparable races found across any years.")
        return

    mae_full_all = sum(full_model_errors_all) / total_all
    mae_lean_all = sum(lean_only_errors_all) / total_all
    acc_full_all = winner_full_all / total_all * 100
    acc_lean_all = winner_lean_all / total_all * 100

    print("\n" + "=" * 80)
    print("OVERALL SUMMARY (all years combined)")
    print("=" * 80)
    print(f"  Full model (polls + lean + econ + approval)   MAE: {mae_full_all:.2f} pp   Winner accuracy: {acc_full_all:.1f}%  (n={total_all})")
    print(f"  Lean-only baseline                            MAE: {mae_lean_all:.2f} pp   Winner accuracy: {acc_lean_all:.1f}%  (n={total_all})")
    print(f"  Improvement from adding polling + climate:     {mae_lean_all - mae_full_all:+.2f} pp MAE")
    print("=" * 80)

    # Largest errors overall
    print("\nLargest margin errors (full model, all years):")
    # We need state + year for context. Recompute a flat list with context.
    error_details = []
    for year in years_with_overlap:
        hist = hist_by_year[year]
        for state in (set(pred_by_state.keys()) & set(hist.keys())):
            preds = pred_by_state[state]
            if len(preds) < 2:
                continue
            d_pred = next((p for p in preds if p["party"] in ("D", "I")), None)
            r_pred = next((p for p in preds if p["party"] == "R"), None)
            if not d_pred or not r_pred:
                continue
            full_margin = d_pred["projected"] - r_pred["projected"]
            actual = hist[state]["margin"]
            err = abs(full_margin - actual)
            error_details.append((err, year, state, full_margin, actual))

    worst = sorted(error_details, reverse=True)[:5]
    for err, year, state, full_m, actual_m in worst:
        print(f"  {year} {state}: projected {full_m:+.1f} pp vs actual {actual_m:+.1f} pp (error {err:.1f} pp)")

    print("\nInterpretation notes:")
    print("  - Results are grouped by election year for easier reading.")
    print("  - Nominee differences grow larger in older cycles (2022 → 2018), so treat older years as noisier signals.")
    print("  - The backtest compares 2026-model projections (current nominees + current polling) against past actual outcomes.")
    print("  - It is NOT a true hindcast (no frozen poll snapshots from those cycles).")


if __name__ == "__main__":
    main()
