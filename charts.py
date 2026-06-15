import matplotlib
try:
    matplotlib.use("macosx")
except Exception:
    matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from model import predict_all_races, project_senate_control

try:
    import mplcursors
    _HAS_MPLCURSORS = True
except ImportError:
    _HAS_MPLCURSORS = False


def _bar_labels(ax, bars, color, fontsize=7.5, offset=0.6):
    """Bold percentage labels positioned cleanly above each bar."""
    for bar in bars:
        h = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0, h + offset,
            f"{h:.1f}%",
            ha="center", va="bottom",
            fontsize=fontsize, fontweight="bold", color=color,
        )


def _flip_direction(winner_party, incumbent_party):
    """Return 'FLIP! X→Y' string showing seat changing hands."""
    return f"FLIP! {incumbent_party}→{winner_party}"


def plot_race_margins():
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "projected":       r["projected"],
            "name":            r["name"],
            "is_flip":         r.get("is_flip", False),
            "winner":          r.get("winner", False),
            "is_incumbent":    r["is_incumbent"],
            "incumbent_party": r["incumbent_party"],
        }

    states, margins, colors, flip_labels, y_labels = [], [], [], [], []
    for state, parties in sorted(seen.items()):
        d = parties.get("D")
        r = parties.get("R")
        if d is None or r is None:
            continue
        margin = d["projected"] - r["projected"]
        winner_data = d if margin > 0 else r
        win_party   = "D" if margin > 0 else "R"
        states.append(state)
        margins.append(margin)
        colors.append("#3a7abf" if margin > 0 else "#c0392b")

        if winner_data["is_flip"]:
            flip_labels.append(_flip_direction(win_party, winner_data["incumbent_party"]))
        else:
            flip_labels.append(None)

        d_lbl  = "D*" if d["is_incumbent"] else "D"
        r_lbl  = "R*" if r["is_incumbent"] else "R"
        d_last = d["name"].rsplit(" ", 1)[-1]
        r_last = r["name"].rsplit(" ", 1)[-1]
        y_labels.append(f"{state}  {d_lbl} {d_last} / {r_lbl} {r_last}")

    combined = sorted(zip(states, margins, colors, flip_labels, y_labels),
                      key=lambda x: x[1], reverse=True)
    states, margins, colors, flip_labels, y_labels = zip(*combined)

    fig, ax = plt.subplots(figsize=(11, len(states) * 0.52 + 2))
    y = range(len(states))
    bars = ax.barh(y, margins, color=colors, height=0.62)
    ax.axvline(0, color="black", linewidth=0.9)

    for i, (margin, flip_lbl) in enumerate(zip(margins, flip_labels)):
        if flip_lbl:
            offset = 0.5 if margin > 0 else -0.5
            ax.text(margin + offset, i, flip_lbl, va="center",
                    fontsize=7.5, fontweight="bold",
                    color="#1a4a7a" if margin > 0 else "#7a0000")

    ax.set_yticks(list(y))
    ax.set_yticklabels(y_labels, fontsize=8, fontweight="bold")
    ax.set_xlabel("D margin  (positive = Dem leads)", fontsize=9, fontweight="bold")

    title = (f"2026 Senate Race Margins\n"
             f"Projected control: {control['control']}  "
             f"(D: {control['D']}  R: {control['R']}  "
             f"uncalled: {control['not_called']})")
    ax.set_title(title, fontweight="bold", fontsize=11)

    d_patch = mpatches.Patch(color="#3a7abf", label="Dem leads")
    r_patch = mpatches.Patch(color="#c0392b", label="Rep leads")
    ax.legend(handles=[d_patch, r_patch], loc="lower right", fontsize=9)

    if _HAS_MPLCURSORS:
        cursor = mplcursors.cursor(bars, hover=True)

        @cursor.connect("add")
        def _margins_hover(sel):
            i = sel.index
            lbl = f"{states[i]}\nMargin: {margins[i]:+.1f}\n{y_labels[i]}"
            if flip_labels[i]:
                lbl += f"\n{flip_labels[i]}"
            sel.annotation.set_text(lbl)
            sel.annotation.get_bbox_patch().set(fc="lightyellow", alpha=0.9)

    plt.tight_layout()
    plt.savefig("margins.png", dpi=150)
    plt.show(block=True)
    print("Saved to margins.png")


def plot_seat_count():
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

    fig, ax = plt.subplots(figsize=(8, 3.2))

    d_seats  = control["D"]
    r_seats  = control["R"]
    uncalled = control["seats_remaining"]
    total    = d_seats + r_seats + uncalled

    d_bar = ax.barh(0, d_seats, color="#3a7abf", height=0.55, label=f"Democrat ({d_seats})")
    r_bar = ax.barh(0, r_seats, color="#c0392b", height=0.55,
                    left=d_seats, label=f"Republican ({r_seats})")
    if uncalled > 0:
        ax.barh(0, uncalled, color="#cccccc", height=0.55,
                left=d_seats + r_seats, label=f"Unassigned ({uncalled})")

    # Large seat-count labels inside bars
    if d_seats > 4:
        ax.text(d_seats / 2, 0, str(d_seats), ha="center", va="center",
                fontsize=16, fontweight="bold", color="white")
    if r_seats > 4:
        ax.text(d_seats + r_seats / 2, 0, str(r_seats), ha="center", va="center",
                fontsize=16, fontweight="bold", color="white")

    ax.axvline(50, color="black", linewidth=1.4, linestyle="--", label="50-seat majority")
    ax.text(50, -0.31, "VP\ntiebreak", ha="center", va="top", fontsize=7, color="black",
            fontweight="bold")
    ax.set_xlim(0, 100)
    ax.set_yticks([])

    xlabel = (f"Projected seats  (D + R = {total})" if uncalled == 0
              else f"Projected seats  (D + R + unassigned = {total})")
    ax.set_xlabel(xlabel, fontsize=9, fontweight="bold")

    title = f"Projected Senate: {control['control']}"
    if control["tiebreaker"]:
        title += "  (50–50 · R via VP tiebreak)"
    ax.set_title(title, fontweight="bold", fontsize=12)
    ax.legend(loc="lower right", fontsize=9)

    plt.tight_layout()
    plt.savefig("seat_count.png", dpi=150)
    plt.show(block=True)
    print("Saved to seat_count.png")


def plot_vote_shares():
    predictions, _, nominees_count = predict_all_races()

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "projected":       r["projected"],
            "name":            r["name"],
            "is_incumbent":    r["is_incumbent"],
            "is_flip":         r.get("is_flip", False),
            "winner":          r.get("winner", False),
            "incumbent_party": r["incumbent_party"],
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

        winner_info = d_info if margin > 0 else r_info
        win_party   = d_key if margin > 0 else "R"
        flip_lbl    = None
        if winner_info["is_flip"]:
            flip_lbl = _flip_direction(win_party, winner_info["incumbent_party"])

        rows.append((state, d_key, d_info, r_info, margin, flip_lbl))

    rows.sort(key=lambda row: row[4], reverse=True)

    d_pcts    = [row[2]["projected"] for row in rows]
    r_pcts    = [row[3]["projected"] for row in rows]
    flip_lbls = [row[5] for row in rows]
    x     = range(len(rows))
    width = 0.38

    fig, ax = plt.subplots(figsize=(14, 7.5))
    d_bars = ax.bar([i - width / 2 for i in x], d_pcts, width, color="#3a7abf", label="Democrat")
    r_bars = ax.bar([i + width / 2 for i in x], r_pcts, width, color="#c0392b", label="Republican")
    ax.axhline(50, color="gray", linestyle="--", linewidth=0.9, label="50% threshold")

    # Bold bar-percentage labels (no margin text above bars)
    _bar_labels(ax, d_bars, color="#0f2a52", fontsize=7, offset=0.5)
    _bar_labels(ax, r_bars, color="#5a0000", fontsize=7, offset=0.5)

    # X-axis labels: state + optional FLIP! line + candidate names
    x_labels = []
    for state, d_key, d_info, r_info, margin, flip_lbl in rows:
        d_lbl  = "D*" if d_info["is_incumbent"] else d_key
        r_lbl  = "R*" if r_info["is_incumbent"] else "R"
        d_last = d_info["name"].rsplit(" ", 1)[-1]
        r_last = r_info["name"].rsplit(" ", 1)[-1]
        state_line = state if not flip_lbl else f"{state}\n{flip_lbl}"
        x_labels.append(f"{state_line}\n{d_lbl} {d_last}\n{r_lbl} {r_last}")

    ax.set_xticks(list(x))
    ax.set_xticklabels(x_labels, fontsize=7, fontweight="bold", linespacing=1.4)
    ax.set_ylabel("Projected vote share (%)", fontsize=10, fontweight="bold")
    ax.set_ylim(0, max(d_pcts + r_pcts) + 11)
    ax.set_title("2026 Senate Projected Vote Shares  (* = incumbent)", fontweight="bold", fontsize=12)
    ax.legend(fontsize=9)

    # Highlight FLIP states with a faint background band
    for i, flip_lbl in enumerate(flip_lbls):
        if flip_lbl:
            ax.axvspan(i - 0.5, i + 0.5, color="gold", alpha=0.15, zorder=0)

    if _HAS_MPLCURSORS:
        cursor_d = mplcursors.cursor(d_bars, hover=True)
        cursor_r = mplcursors.cursor(r_bars, hover=True)

        @cursor_d.connect("add")
        def _hover_d(sel):
            i   = sel.index
            row = rows[i]
            state, d_key, d_info, r_info, margin, flip_lbl = row
            inc = " (incumbent)" if d_info["is_incumbent"] else ""
            txt = f"{state} — {d_key} {d_info['name']}{inc}\nProjected: {d_info['projected']}%"
            if flip_lbl:
                txt += f"\n{flip_lbl}"
            sel.annotation.set_text(txt)
            sel.annotation.get_bbox_patch().set(fc="#d0e4ff", alpha=0.95)

        @cursor_r.connect("add")
        def _hover_r(sel):
            i   = sel.index
            row = rows[i]
            state, d_key, d_info, r_info, margin, flip_lbl = row
            inc = " (incumbent)" if r_info["is_incumbent"] else ""
            txt = f"{state} — R {r_info['name']}{inc}\nProjected: {r_info['projected']}%"
            if flip_lbl:
                txt += f"\n{flip_lbl}"
            sel.annotation.set_text(txt)
            sel.annotation.get_bbox_patch().set(fc="#ffd0d0", alpha=0.95)

    plt.tight_layout()
    plt.savefig("vote_shares.png", dpi=150)
    plt.show(block=True)
    print("Saved to vote_shares.png")


if __name__ == "__main__":
    plot_race_margins()
    plot_seat_count()
    plot_vote_shares()
