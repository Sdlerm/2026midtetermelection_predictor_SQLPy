import matplotlib
try:
    matplotlib.use("macosx")
except Exception:
    matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from senate_model import predict_all_races, project_senate_control

def plot_race_margins():
    """
    Generate a horizontal bar plot visualizing the projected vote margins
    for Senate races by state, along with additional information such as
    incumbency and potential party flips.

    This function processes projection data, calculates the vote margins
    for each race, and creates a sorted horizontal bar chart displaying
    these margins. The chart indicates party control via color codes and
    highlights flipped states. It also displays a summary of projected
    Senate control in the chart title, and the final plot is saved as an
    image.

    Returns
    -------
    None
        The function generates a visual plot, saves it as an image file,
        and displays it; does not return any value.
    """
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

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
        }

    states, margins, colors, flips, y_labels = [], [], [], [], []
    for state, parties in sorted(seen.items()):
        d = parties.get("D")
        r = parties.get("R")
        if d is None or r is None:
            continue
        margin = d["projected"] - r["projected"]
        winner_data = d if margin > 0 else r
        states.append(state)
        margins.append(margin)
        colors.append("#3a7abf" if margin > 0 else "#c0392b")
        flips.append(winner_data["is_flip"])
        d_lbl  = "D*" if d["is_incumbent"] else "D"
        r_lbl  = "R*" if r["is_incumbent"] else "R"
        d_last = d["name"].rsplit(" ", 1)[-1]
        r_last = r["name"].rsplit(" ", 1)[-1]
        y_labels.append(f"{state}  {d_lbl} {d_last} / {r_lbl} {r_last}")

    combined = sorted(zip(states, margins, colors, flips, y_labels),
                      key=lambda x: x[1], reverse=True)
    states, margins, colors, flips, y_labels = zip(*combined)

    fig, ax = plt.subplots(figsize=(10, len(states) * 0.5 + 2))
    y = range(len(states))
    ax.barh(y, margins, color=colors, height=0.6)
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
    plt.savefig("margins.png", dpi=150)
    plt.show(block=True)
    print("Saved to margins.png")


def plot_seat_count():
    """
    Plots a horizontal bar chart representing the projected seat count in the Senate based on predicted race outcomes.

    The chart demonstrates the number of seats held by Democrats, Republicans, and unassigned seats,
    providing a visual representation of the Senate's current or predicted balance of power. Additional
    elements in the chart include a dashed line at the 50-seat majority mark, annotations for the Vice President's
    tiebreaker, and dynamic adjustments to labels and titles depending on the data.

    Raises:
        None

    Args:
        None

    Returns:
        None
    """
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

    fig, ax = plt.subplots(figsize=(7, 3))

    d_seats = control["D"]
    r_seats = control["R"]
    uncalled = control["seats_remaining"]  # all Class 2 seats not yet assigned; keeps bar total = 100
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
    plt.savefig("seat_count.png", dpi=150)
    plt.show(block=True)
    print("Saved to seat_count.png")


def plot_vote_shares():
    """
    Plots projected vote shares for Democratic and Republican candidates in the 2026 Senate
    races, displaying incumbency status, vote margins, and other contextual details.

    The function generates a bar chart comparing Democratic and Republican vote shares
    across different states with labeling for vote margins, incumbency, and names of candidates.
    ### Raises
    ValueError: If the data format for predictions is invalid or required fields are missing
                in the input.

    ### Notes
    - This function relies on `predict_all_races` to provide predictions for all Senate races.
    - The chart is saved as `vote_shares.png` in the current working directory.
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

    d_pcts = [row[2]["projected"] for row in rows]
    r_pcts = [row[3]["projected"] for row in rows]
    x = range(len(rows))
    width = 0.35

    fig, ax = plt.subplots(figsize=(14, 7))
    d_bars = ax.bar([i - width / 2 for i in x], d_pcts, width, color="#3a7abf", label="Democrat")
    r_bars = ax.bar([i + width / 2 for i in x], r_pcts, width, color="#c0392b", label="Republican")
    ax.axhline(50, color="gray", linestyle="--", linewidth=0.8, label="50% threshold")

    ax.bar_label(d_bars, fmt="%.1f%%", fontsize=6, padding=2, color="#1a4a7a")
    ax.bar_label(r_bars, fmt="%.1f%%", fontsize=6, padding=2, color="#8b0000")

    for i, (_, d_key, d_info, r_info, margin) in enumerate(rows):
        is_flip = d_info.get("is_flip", False) or r_info.get("is_flip", False)
        if not is_flip:
            continue
        top = max(d_pcts[i], r_pcts[i])
        ax.text(i, top + 2.5, "FLIP!", ha="center", va="bottom", fontsize=7,
                fontweight="bold", color="#3a7abf" if margin > 0 else "#c0392b")

    x_labels = []
    for state, d_key, d_info, r_info, _ in rows:
        d_lbl  = "D*" if d_info["is_incumbent"] else d_key
        r_lbl  = "R*" if r_info["is_incumbent"] else "R"
        d_last = d_info["name"].rsplit(" ", 1)[-1]
        r_last = r_info["name"].rsplit(" ", 1)[-1]
        x_labels.append(f"{state}\n{d_lbl} {d_last}\n{r_lbl} {r_last}")

    ax.set_xticks(list(x))
    ax.set_xticklabels(x_labels, fontsize=7)
    ax.set_ylabel("Projected vote share (%)")
    ax.set_ylim(0, max(d_pcts + r_pcts) + 10)
    ax.set_title("2026 Senate Projected Vote Shares  (* = incumbent)", fontweight="bold")
    ax.legend()

    plt.tight_layout()
    plt.savefig("vote_shares.png", dpi=150)
    plt.show(block=True)
    print("Saved to vote_shares.png")


if __name__ == "__main__":
    plot_race_margins()
    plot_seat_count()
    plot_vote_shares()