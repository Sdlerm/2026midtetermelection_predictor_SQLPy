import matplotlib
try:
    matplotlib.use("macosx")
except Exception:
    matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from model import predict_all_races, project_senate_control

def plot_race_margins():
    predictions, _, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "projected":  r["projected"],
            "is_flip":    r.get("is_flip", False),
            "winner":     r.get("winner", False),
            "is_incumbent": r["is_incumbent"],
        }

    states, margins, colors, flips = [], [], [], []
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

    combined = sorted(zip(states, margins, colors, flips),
                      key=lambda x: x[1], reverse=True)
    states, margins, colors, flips = zip(*combined)

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
    ax.set_yticklabels(states, fontsize=9)
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
    ax.barh(0, uncalled, color="#cccccc", height=0.5,
            left=d_seats + r_seats, label=f"Uncalled ({uncalled})")

    ax.axvline(50, color="black", linewidth=1.2, linestyle="--", label="50 seat threshold")
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_xlabel("Projected seats")

    title = f"Projected Senate: {control['control']}"
    if control["tiebreaker"]:
        title += " (Vance tiebreaker)"
    ax.set_title(title, fontweight="bold")
    ax.legend(loc="lower right", fontsize=8)

    plt.tight_layout()
    plt.savefig("seat_count.png", dpi=150)
    plt.show(block=True)
    print("Saved to seat_count.png")


def plot_vote_shares():
    predictions, _, _ = predict_all_races()

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = r["projected"]

    states, d_pcts, r_pcts = [], [], []
    for state, parties in sorted(seen.items()):
        d = parties.get("D")
        r = parties.get("R")
        if d is None or r is None:
            continue
        states.append(state)
        d_pcts.append(d)
        r_pcts.append(r)

    combined = sorted(zip(states, d_pcts, r_pcts), key=lambda x: x[1], reverse=True)
    states, d_pcts, r_pcts = zip(*combined)

    x = range(len(states))
    width = 0.35

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar([i - width/2 for i in x], d_pcts, width, color="#3a7abf", label="Democrat")
    ax.bar([i + width/2 for i in x], r_pcts, width, color="#c0392b", label="Republican")
    ax.axhline(50, color="gray", linestyle="--", linewidth=0.8, label="50% threshold")

    ax.set_xticks(list(x))
    ax.set_xticklabels(states, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Projected vote share (%)")
    ax.set_title("2026 Senate Projected Vote Shares", fontweight="bold")
    ax.legend()

    plt.tight_layout()
    plt.savefig("vote_shares.png", dpi=150)
    plt.show(block=True)
    print("Saved to vote_shares.png")


if __name__ == "__main__":
    plot_race_margins()
    plot_seat_count()
    plot_vote_shares()