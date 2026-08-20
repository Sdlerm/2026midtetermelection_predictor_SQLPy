import math
import os

import matplotlib

try:
    matplotlib.use("macosx")
except Exception:
    matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from senate_model import predict_all_races, project_senate_control
from house_model import predict_house_races

TOTAL_HOUSE_SEATS = 435
HOUSE_MAJORITY = 218

CHARTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)


def _bar_stderr(stderr):
    """
    One candidate's stderr as an xerr value: NaN when it is unknown.

    matplotlib draws a real, visible cap at +/-0.0, so mapping a missing
    stderr to zero tells the reader a lean-only race is known exactly. It
    skips NaN entirely and draws nothing, which is the honest rendering.
    Matches dashboard.plot_race_margins, which already relies on plotly
    skipping NaN the same way, and dashboard.load_predictions, which sets
    margin_stderr = None rather than "a zero-width one that would claim
    false certainty".
    """
    return float("nan") if stderr is None else stderr


def _combine_stderr(d_stderr, r_stderr):
    """
    Both candidates' stderrs in quadrature, as the error on their margin.

    NaN if EITHER side is missing: a margin error built from one candidate's
    uncertainty alone understates it, so half an answer is worse than none.
    """
    if d_stderr is None or r_stderr is None:
        return float("nan")
    return math.sqrt(d_stderr ** 2 + r_stderr ** 2)

def plot_race_margins():
    """
    Generate a horizontal bar plot visualizing the projected vote margins
    for Senate races by state, along with additional information such as
    incumbency and potential party flips.

    This function processes projection data, calculates the vote margins
    for each race, and creates a sorted horizontal bar chart displaying
    these margins. The chart indicates party control via color codes and
    highlights flipped states. Error bars show the standard error of each
    candidate's polling AVERAGE — it narrows as polling accumulates — with a
    binomial sampling-error fallback when the weight rests on a single poll.
    Races with no polling draw no bar at all rather than a zero-width one. It also displays a summary of projected
    Senate control in the chart title, and the final plot is saved as an
    image.

    Returns
    -------
    None
        The function generates a visual plot, saves it as an image file,
        and displays it; does not return any value.
    """
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions)

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "projected":    r["projected"],
            "name":         r["name"],
            "is_flip":      r.get("is_flip", False),
            "winner":       r.get("winner", False),
            "is_incumbent": r["is_incumbent"],
            "stderr":       r.get("poll_stderr"),
        }

    states, margins, colors, flips, y_labels, margin_errs = [], [], [], [], [], []
    for state, parties in sorted(seen.items()):
        d_key = "D" if "D" in parties else ("I" if "I" in parties else None)
        if d_key is None or "R" not in parties:
            continue
        d = parties[d_key]
        r = parties["R"]
        margin = d["projected"] - r["projected"]
        margin_err = _combine_stderr(d.get("stderr"), r.get("stderr"))
        winner_data = d if margin > 0 else r
        states.append(state)
        margins.append(margin)
        margin_errs.append(margin_err)
        colors.append("#3a7abf" if margin > 0 else "#c0392b")
        flips.append(winner_data["is_flip"])
        d_lbl  = d_key + "*" if d["is_incumbent"] else d_key
        r_lbl  = "R*" if r["is_incumbent"] else "R"
        d_last = d["name"].rsplit(" ", 1)[-1]
        r_last = r["name"].rsplit(" ", 1)[-1]
        y_labels.append(f"{state}  {d_lbl} {d_last} / {r_lbl} {r_last}")

    combined = sorted(zip(states, margins, colors, flips, y_labels, margin_errs),
                      key=lambda x: x[1], reverse=True)
    states, margins, colors, flips, y_labels, margin_errs = zip(*combined)

    fig, ax = plt.subplots(figsize=(10, len(states) * 0.5 + 2))
    y = range(len(states))
    ax.barh(y, margins, xerr=margin_errs, color=colors, height=0.6,
            ecolor="#666666", capsize=3)
    ax.axvline(0, color="black", linewidth=0.8)

    # Flip labels
    for i, (margin, is_flip) in enumerate(zip(margins, flips)):
        if is_flip:
            offset = 0.4 if margin > 0 else -0.4
            ax.text(margin + offset, i, "FLIP", va="center",
                    fontsize=7, fontweight="bold",
                    color="#3a7abf" if margin > 0 else "#c0392b")

    ax.set_yticks(list(y))
    ax.set_yticklabels(y_labels, fontsize=7.5)
    ax.set_xlabel("D margin (positive = Dem leads)")

    # Control summary in title
    title = (f"2026 Senate Race Margins\n"
             f"Projected control: {control['control']}  "
             f"(D: {control['D']}  R: {control['R']}  "
             f"uncalled: {control['not_called']})")
    ax.set_title(title, fontweight="bold", fontsize=10)

    d_patch = mpatches.Patch(color="#3a7abf", label="Dem leads")
    r_patch = mpatches.Patch(color="#c0392b", label="Rep leads")
    ax.legend(handles=[d_patch, r_patch], loc="lower right")

    plt.tight_layout()
    out_path = os.path.join(CHARTS_DIR, "margins.png")
    plt.savefig(out_path, dpi=150)
    print(f"Saved to {out_path}")


def plot_seat_count():
    """
    Plots a horizontal bar chart representing the projected seat count in the Senate based on predicted race outcomes.

    The chart demonstrates the number of seats held by Democrats, Republicans, and unassigned seats,
    providing a visual representation of the Senate's current or predicted balance of power. Additional
    elements in the chart include a dashed line at the 50-seat majority mark, annotations for the Vice President's
    tiebreaker, and dynamic adjustments to labels and titles depending on the data.

    No error bars here on purpose: a real one would mean simulating outcome
    distributions across all races and propagating them into a distribution
    of possible seat totals (i.e. the Monte Carlo work, not done yet) rather
    than decorating a single point estimate with a number that isn't backed
    by anything.

    Raises:
        None

    Args:
        None

    Returns:
        None
    """
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions)

    fig, ax = plt.subplots(figsize=(7, 3))

    d_seats = control["D"]
    r_seats = control["R"]
    uncalled = control["not_called"]  # all Class 2 seats not yet assigned; keeps bar total = 100
    total = d_seats + r_seats + uncalled

    ax.barh(0, d_seats, color="#3a7abf", height=0.5, label=f"Democrat ({d_seats})")
    ax.barh(0, r_seats, color="#c0392b", height=0.5,
            left=d_seats, label=f"Republican ({r_seats})")
    if uncalled > 0:
        ax.barh(0, uncalled, color="#cccccc", height=0.5,
                left=d_seats + r_seats, label=f"Unassigned ({uncalled})")

    ax.axvline(50, color="black", linewidth=1.2, linestyle="--", label="50-seat majority")
    ax.text(50, 0.29, "← VP\ntiebreak", ha="center", va="bottom", fontsize=6.5, color="black")
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    xlabel = f"Projected seats  (D + R = {total})" if uncalled == 0 else f"Projected seats  (D + R + unassigned = {total})"
    ax.set_xlabel(xlabel)

    title = f"Projected Senate: {control['control']}"
    if control["tiebreaker"]:
        title += "  (50–50 · R via VP tiebreak)"
    ax.set_title(title, fontweight="bold")
    ax.legend(loc="lower right", fontsize=8)

    plt.tight_layout()
    out_path = os.path.join(CHARTS_DIR, "seat_count.png")
    plt.savefig(out_path, dpi=150)
    print(f"Saved to {out_path}")


def _plot_vote_shares(rows, title, out_name):
    """
    Render a paired vote-share chart for `rows` and save it to `out_name`.

    Shared by the Senate and House vote-share charts, which differ only in
    their data source and title. Rows must already be sorted by D margin
    (descending); each is the (key, d_key, d_info, r_info, margin) tuple the
    two callers build, where `key` is a state abbreviation or a district code.

    Layout is horizontal — one race per row, bars running left to right. The
    vertical version this replaces packed 34 states into a 14-inch-wide figure,
    which collided the three-line x tick labels, the per-bar percentages, and
    the FLIP! markers into an unreadable band. Going horizontal gives each race
    its own row: the candidate line fits on one line beside the bars, the two
    percentages land at different heights so they cannot overlap, and flips get
    a dedicated right-hand column instead of floating above the bars. Figure
    height scales with the row count, so density stays fixed as races are added.

    Returns
    -------
    None
        Saves the figure to `CHARTS_DIR/out_name` and prints the path.
    """
    d_pcts = [row[2]["projected"] for row in rows]
    r_pcts = [row[3]["projected"] for row in rows]
    # NaN, not 0: matplotlib draws visible caps at +/-0.0 but skips NaN
    # entirely, which is what a candidate with no polling should show.
    d_errs = [_bar_stderr(row[2]["stderr"]) for row in rows]
    r_errs = [_bar_stderr(row[3]["stderr"]) for row in rows]
    y = range(len(rows))
    # Bars are 0.36 tall on centres 0.40 apart, leaving a ~2px surface gap
    # between the pair at the 0.42 in/row this figure is sized to.
    height = 0.36
    offset = 0.20

    fig_h = len(rows) * 0.42 + 1.6
    fig, ax = plt.subplots(figsize=(11, fig_h))

    d_bars = ax.barh([i - offset for i in y], d_pcts, height, xerr=d_errs, capsize=1.5,
                     color="#3a7abf", label="Democrat", ecolor="#8a8a8a",
                     error_kw={"elinewidth": 0.9})
    r_bars = ax.barh([i + offset for i in y], r_pcts, height, xerr=r_errs, capsize=1.5,
                     color="#c0392b", label="Republican", ecolor="#8a8a8a",
                     error_kw={"elinewidth": 0.9})
    ax.axvline(50, color="#555555", linestyle="--", linewidth=0.9, label="50% threshold")

    # Values sit in muted ink, not the series colour — the bar beside each
    # number already carries party identity. Placed past the error bar rather
    # than with bar_label's bar-relative padding, which puts short-bar labels
    # underneath their own error whisker.
    for i, (pct, err) in enumerate(zip(d_pcts, d_errs)):
        ax.text(pct + err + 1.4, i - offset, f"{pct:.1f}", va="center", ha="left",
                fontsize=7, color="#444444")
    for i, (pct, err) in enumerate(zip(r_pcts, r_errs)):
        ax.text(pct + err + 1.4, i + offset, f"{pct:.1f}", va="center", ha="left",
                fontsize=7, color="#444444")

    max_extent = max(pct + err for pct, err in zip(d_pcts + r_pcts, d_errs + r_errs))
    flip_x = max_extent + 5.5
    for i, (_, d_key, d_info, r_info, margin) in enumerate(rows):
        if not (d_info.get("is_flip", False) or r_info.get("is_flip", False)):
            continue
        # Flips name the gaining party in text, so the marker never depends on
        # colour alone to be read.
        ax.text(flip_x, i, f"FLIP → {d_key if margin > 0 else 'R'}",
                va="center", ha="left", fontsize=7, fontweight="bold",
                color="#3a7abf" if margin > 0 else "#c0392b")

    y_labels = []
    for key, d_key, d_info, r_info, _ in rows:
        d_lbl  = d_key + "*" if d_info["is_incumbent"] else d_key
        r_lbl  = "R*" if r_info["is_incumbent"] else "R"
        d_last = d_info["name"].rsplit(" ", 1)[-1]
        r_last = r_info["name"].rsplit(" ", 1)[-1]
        y_labels.append(f"{key}   {d_lbl} {d_last} / {r_lbl} {r_last}")

    ax.set_yticks(list(y))
    ax.set_yticklabels(y_labels, fontsize=8)
    ax.invert_yaxis()          # strongest D margin on top
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_xlabel("Projected vote share (%)")
    ax.set_xlim(0, flip_x + 7)
    # Ticks stop at the last decade the data reaches, so no gridline is drawn
    # through the flip column out past the longest bar.
    ax.set_xticks(range(0, int(max_extent // 10) * 10 + 1, 10))

    ax.xaxis.grid(True, color="#e4e4e4", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#cccccc")

    # Title and legend live in a fixed 0.8-inch band at the top, so their
    # spacing holds whatever the row count does to the figure height. The
    # legend is added after tight_layout and excluded from it: laid out
    # normally, its figure-coordinate anchor makes tight_layout reserve the
    # whole top of the figure and open a large gap above the first bar.
    handles, labels = ax.get_legend_handles_labels()
    fig.suptitle(title, fontweight="bold", fontsize=11, y=1 - 0.22 / fig_h)
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.8 / fig_h))
    leg = fig.legend(handles, labels, loc="upper center",
                     bbox_to_anchor=(0.5, 1 - 0.44 / fig_h),
                     ncols=3, frameon=False, fontsize=9)
    leg.set_in_layout(False)

    out_path = os.path.join(CHARTS_DIR, out_name)
    plt.savefig(out_path, dpi=150)
    print(f"Saved to {out_path}")


def plot_vote_shares():
    """
    Plots projected vote shares for Democratic and Republican candidates in the
    2026 Senate races, displaying incumbency status, flips, and candidate names.

    One horizontal row per race, sorted by D margin, with error bars showing
    the standard error of each candidate's polling average (or a binomial
    sampling-error fallback when the weight rests on a single poll), and no
    bar where there is no polling. See `_plot_vote_shares` for the layout
    itself.

    ### Notes
    - This function relies on `predict_all_races` to provide predictions for all Senate races.
    - The chart is saved as `vote_shares.png` in the `data/charts` directory.
    - The function assumes the predictions include the fields: "state", "party", "projected",
      "name", and "is_incumbent".
    - Incumbent candidates are marked with an asterisk (*) in the corresponding labels.
    """
    predictions, _, nominees_count = predict_all_races()

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "projected":    r["projected"],
            "name":         r["name"],
            "is_incumbent": r["is_incumbent"],
            "is_flip":      r.get("is_flip", False),
            "stderr":       r.get("poll_stderr"),
        }

    rows = []
    for state in sorted(seen):
        parties = seen[state]
        d_key = "D" if "D" in parties else ("I" if "I" in parties else None)
        if d_key is None or "R" not in parties:
            continue
        d_info = parties[d_key]
        r_info = parties["R"]
        margin = d_info["projected"] - r_info["projected"]
        rows.append((state, d_key, d_info, r_info, margin))

    rows.sort(key=lambda row: row[4], reverse=True)

    _plot_vote_shares(rows,
                      "2026 Senate Projected Vote Shares  (* = incumbent)",
                      "vote_shares.png")


def _house_rows(polled_only=True):
    """
    Group Tier 2 House predictions into one row per district for plotting.

    Pairs each district's Democratic-side candidate (D, or I when no D is
    running) with its Republican candidate, mirroring the pairing logic the
    Senate charts use. Districts without both sides present are skipped —
    house_model already dropped one-sided races, but a D/I-less or R-less
    multi-way race could still slip through.

    polled_only=True (the default) restricts output to poll-backed districts.
    The per-district charts need that: 435 paired bars is an unreadable figure,
    and lean-only rows carry stderr=None, which the error-bar math cannot
    consume. Only the chamber-wide seat chart passes False.

    Returns
    -------
    list of tuple
        (race, d_key, d_info, r_info, margin) sorted by D margin, descending.
        d_info / r_info are dicts with projected, name, is_incumbent, is_flip,
        winner, has_polls, and stderr keys; margin is D projected minus R.
    """
    predictions, _, _ = predict_house_races()

    seen = {}
    for r in predictions:
        if polled_only and not r.get("has_polls"):
            continue
        race = r["race"]
        if race not in seen:
            seen[race] = {}
        seen[race][r["party"]] = {
            "projected":    r["projected"],
            "name":         r["name"],
            "is_incumbent": r["is_incumbent"],
            "is_flip":      r.get("is_flip", False),
            "winner":       r.get("winner", False),
            "has_polls":    r.get("has_polls", False),
            "stderr":       r.get("poll_stderr"),
        }

    rows = []
    for race in sorted(seen):
        parties = seen[race]
        d_key = "D" if "D" in parties else ("I" if "I" in parties else None)
        if d_key is None or "R" not in parties:
            continue
        d_info = parties[d_key]
        r_info = parties["R"]
        margin = d_info["projected"] - r_info["projected"]
        rows.append((race, d_key, d_info, r_info, margin))

    rows.sort(key=lambda row: row[4], reverse=True)
    return rows


def plot_house_vote_shares():
    """
    Plots projected vote shares for Democratic and Republican candidates in
    the poll-backed 2026 House districts (Tier 2).

    Same layout as the Senate vote-shares chart: one horizontal row per
    district with error bars for polling uncertainty, a 50% reference line,
    percentage labels, incumbency asterisks, and a flip marker where the
    projected winner's party differs from the incumbent's. Figure height scales
    with the number of polled districts.

    The chart is saved as `house_vote_shares.png` in the `data/charts`
    directory. If no polled districts are available (house_ingest.py not run),
    prints a notice and returns without plotting.
    """
    rows = _house_rows()
    if not rows:
        print("No polled House races found — run house_ingest.py first.")
        return

    _plot_vote_shares(
        rows,
        f"2026 House Projected Vote Shares — Tier 2, {len(rows)} poll-backed "
        f"districts  (* = incumbent)",
        "house_vote_shares.png")


def plot_house_race_margins():
    """
    Generate a horizontal bar plot of projected vote margins for the polled
    2026 House districts (Tier 2), poll-backed only.

    Same layout as the Senate margins chart: one bar per district (D margin,
    positive = Dem leads), colored by leading party, with error bars combining
    both candidates' polling uncertainty in quadrature, incumbency asterisks
    in the labels, and FLIP annotations where the projected winner's party
    differs from the incumbent's.

    The chart is saved as `house_margins.png` in the `data/charts` directory.
    If no polled districts are available (house_ingest.py not run), prints a
    notice and returns without plotting.
    """
    rows = _house_rows()
    if not rows:
        print("No polled House races found — run house_ingest.py first.")
        return

    races, margins, colors, flips, y_labels, margin_errs = [], [], [], [], [], []
    for race, d_key, d_info, r_info, margin in rows:
        margin_err = _combine_stderr(d_info.get("stderr"), r_info.get("stderr"))
        winner_data = d_info if margin > 0 else r_info
        races.append(race)
        margins.append(margin)
        margin_errs.append(margin_err)
        colors.append("#3a7abf" if margin > 0 else "#c0392b")
        flips.append(winner_data["is_flip"])
        d_lbl  = d_key + "*" if d_info["is_incumbent"] else d_key
        r_lbl  = "R*" if r_info["is_incumbent"] else "R"
        d_last = d_info["name"].rsplit(" ", 1)[-1]
        r_last = r_info["name"].rsplit(" ", 1)[-1]
        y_labels.append(f"{race}  {d_lbl} {d_last} / {r_lbl} {r_last}")

    fig, ax = plt.subplots(figsize=(10, len(races) * 0.4 + 2))
    y = range(len(races))
    ax.barh(y, margins, xerr=margin_errs, color=colors, height=0.6,
            ecolor="#666666", capsize=3)
    ax.axvline(0, color="black", linewidth=0.8)

    # Flip labels
    for i, (margin, err, is_flip) in enumerate(zip(margins, margin_errs, flips)):
        if is_flip:
            offset = err + 0.4 if margin > 0 else -(err + 0.4)
            ax.text(margin + offset, i, "FLIP", va="center",
                    ha="left" if margin > 0 else "right",
                    fontsize=7, fontweight="bold",
                    color="#3a7abf" if margin > 0 else "#c0392b")

    ax.set_yticks(list(y))
    ax.set_yticklabels(y_labels, fontsize=7)
    ax.invert_yaxis()                # biggest D margin at the top
    ax.set_xlabel("D margin (positive = Dem leads)")

    ax.set_title(f"2026 House Race Margins — Tier 2, {len(races)} poll-backed districts",
                 fontweight="bold", fontsize=10)

    d_patch = mpatches.Patch(color="#3a7abf", label="Dem leads")
    r_patch = mpatches.Patch(color="#c0392b", label="Rep leads")
    ax.legend(handles=[d_patch, r_patch], loc="lower right")

    plt.tight_layout()
    out_path = os.path.join(CHARTS_DIR, "house_margins.png")
    plt.savefig(out_path, dpi=150)
    print(f"Saved to {out_path}")


def plot_house_party_control():
    """
    Plots a horizontal bar chart of projected party leads across all 435 2026
    House districts (Tier 2), against the 218-seat majority line.

    Tier 2 closed the coverage gap Tier 1 had — every district now has a
    projection — but seat COUNTS are still not a control PROBABILITY. Each bar
    is split by basis: the solid segment is poll-backed, the hatched segment is
    lean-only. That split is the honest replacement for Tier 1's gray
    "unmodeled" band: the uncertainty didn't disappear when coverage arrived,
    it moved from "we have no estimate" to "our estimate rests on 2022-vintage
    structural lean". A control probability needs a Monte Carlo over correlated
    district errors, which this chart deliberately does not fake.

    The chart is saved as `house_control.png` in the `data/charts` directory.
    If no districts are available (house_ingest.py not run), prints a notice
    and returns without plotting.
    """
    rows = _house_rows(polled_only=False)
    if not rows:
        print("No House races found — run house_ingest.py first.")
        return

    d_polled = sum(1 for _, _, d_info, _, margin in rows
                   if margin > 0 and d_info["has_polls"])
    d_lean   = sum(1 for _, _, d_info, _, margin in rows
                   if margin > 0 and not d_info["has_polls"])
    r_polled = sum(1 for _, _, _, r_info, margin in rows
                   if margin <= 0 and r_info["has_polls"])
    r_lean   = sum(1 for _, _, _, r_info, margin in rows
                   if margin <= 0 and not r_info["has_polls"])
    d_leads, r_leads = d_polled + d_lean, r_polled + r_lean
    flips = sum(
        1 for _, _, d_info, r_info, _ in rows
        if d_info["is_flip"] or r_info["is_flip"]
    )

    fig, ax = plt.subplots(figsize=(9, 3))

    # Order matters: polled segments sit on the outside edges so the two
    # hatched lean-only blocks meet in the middle, straddling the majority line
    # where the seats least supported by polling actually decide control.
    left = 0
    for width, color, hatch, label in [
        (d_polled, "#3a7abf", None, f"Dem — polls ({d_polled})"),
        (d_lean,   "#3a7abf", "//", f"Dem — lean only ({d_lean})"),
        (r_lean,   "#c0392b", "//", f"Rep — lean only ({r_lean})"),
        (r_polled, "#c0392b", None, f"Rep — polls ({r_polled})"),
    ]:
        if width:
            ax.barh(0, width, color=color, height=0.5, left=left,
                    hatch=hatch, edgecolor="white", linewidth=0, label=label)
        left += width

    ax.axvline(HOUSE_MAJORITY, color="black", linewidth=1.2, linestyle="--",
               label=f"{HOUSE_MAJORITY}-seat majority")
    ax.set_xlim(0, TOTAL_HOUSE_SEATS)
    ax.set_yticks([])
    ax.set_xlabel(f"House seats  (D + R = {TOTAL_HOUSE_SEATS})")

    title = (f"House Tier 2: projected leads in all {len(rows)} districts — "
             f"seat counts, NOT a control probability\n"
             f"D: {d_leads}  R: {r_leads}  flips: {flips}  ·  "
             f"{d_lean + r_lean} seats rest on lean only (hatched)")
    ax.set_title(title, fontweight="bold", fontsize=10)
    ax.legend(loc="lower right", fontsize=8, ncols=2)

    plt.tight_layout()
    out_path = os.path.join(CHARTS_DIR, "house_control.png")
    plt.savefig(out_path, dpi=150)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    plot_race_margins()
    plot_seat_count()
    plot_vote_shares()
    plot_house_race_margins()
    plot_house_vote_shares()
    plot_house_party_control()
    # Single show at the end: starting/stopping the macosx event loop once per
    # chart crashes the interpreter (GIL error in start_main_loop) on py3.13.
    plt.show(block=True)