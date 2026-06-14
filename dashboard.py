import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from model import predict_all_races, project_senate_control

st.set_page_config(page_title="2026 Senate Predictor", layout="wide")
st.title("🗳️ 2026 Senate Election Predictor")
st.caption("Weighted polling average · Credibility × recency decay · Updates on refresh")

@st.cache_data(ttl=300)
def load_predictions():
    predictions, climate, nominees_count = predict_all_races()
    control = project_senate_control(predictions, nominees_count)

    seen = {}
    for r in predictions:
        state = r["state"]
        if state not in seen:
            seen[state] = {}
        seen[state][r["party"]] = {
            "name":           r["name"],
            "poll_avg":       r["poll_avg"],
            "adjustment":     r["adjustment"],
            "pct":            r["projected"],
            "is_incumbent":   r["is_incumbent"],
            "is_flip":        r.get("is_flip", False),
            "winner":         r.get("winner", False),
        }

    rows = []
    for state, parties in seen.items():
        d = parties.get("D")
        r = parties.get("R")
        if not d or not r:
            continue
        margin = d["pct"] - r["pct"]
        winner_party = "D" if margin > 0 else "R"
        winner_data  = d if margin > 0 else r

        dem_label = f"{'★ ' if d['winner'] else ''}{d['name']}"
        rep_label = f"{'★ ' if r['winner'] else ''}{r['name']}"
        if d["is_incumbent"]:
            dem_label += " [inc]"
        if r["is_incumbent"]:
            rep_label += " [inc]"

        rows.append({
            "State":    state,
            "Dem":      dem_label,
            "Dem %":    d["pct"],
            "Rep":      rep_label,
            "Rep %":    r["pct"],
            "Margin":   round(margin, 1),
            "Leader":   winner_party,
            "Flip":     "⚡" if winner_data["is_flip"] else "",
        })

    return pd.DataFrame(rows).sort_values("Margin", ascending=False), climate, control

df, climate, control = load_predictions()

# --- Economic climate caption ---
direction = "favors Democrats" if climate > 0 else "favors Republicans"
st.caption(f"Economic climate score: **{climate:+.3f}** ({direction}) · Adjustment: ±{abs(climate * 0.3 * 10):.1f}pp")

# --- Senate control banner ---
st.divider()
if control["control"] == "Democrats":
    st.success(f"🔵 Projected Senate control: **Democrats**  —  D: {control['D']} seats · R: {control['R']} seats · {control['not_called']} uncalled")
elif control["control"] == "Republicans":
    st.error(f"🔴 Projected Senate control: **Republicans**  —  R: {control['R']} seats · D: {control['D']} seats · {control['not_called']} uncalled")
else:
    st.warning(f"⚠️ Senate control unclear  —  D: {control['D']} · R: {control['R']} · {control['not_called']} uncalled")

if control["tiebreaker"]:
    st.caption("50-50 tie — Vance tiebreaker gives Republicans control")

# --- Flips summary ---
if control["flips"]:
    flip_names = "  ·  ".join([f"⚡ {f['state']} → {f['party']}" for f in control["flips"]])
    st.caption(f"Projected flips: {flip_names}")

st.divider()

# --- Summary metrics ---
col1, col2, col3, col4 = st.columns(4)
dem_leads = (df["Leader"] == "D").sum()
rep_leads = (df["Leader"] == "R").sum()
flips     = df["Flip"].str.contains("⚡").sum()
col1.metric("Dem leads", dem_leads)
col2.metric("Rep leads", rep_leads)
col3.metric("Projected flips", flips)
col4.metric("Races tracked", len(df))

st.divider()

# --- Margin chart ---
st.subheader("Race margins")
fig, ax = plt.subplots(figsize=(10, len(df) * 0.45 + 1.5))
colors = ["#3a7abf" if m > 0 else "#c0392b" for m in df["Margin"]]
bars = ax.barh(df["State"], df["Margin"], color=colors, height=0.6)

# Mark flips with a lightning bolt on the bar
for i, (_, row) in enumerate(df.iterrows()):
    if row["Flip"] == "⚡":
        x = row["Margin"]
        offset = 0.3 if x > 0 else -0.3
        ax.text(x + offset, i, "⚡", va="center", fontsize=9)

ax.axvline(0, color="black", linewidth=0.8)
ax.set_xlabel("D margin (positive = Dem leads)")
d_patch = mpatches.Patch(color="#3a7abf", label="Dem leads")
r_patch = mpatches.Patch(color="#c0392b", label="Rep leads")
ax.legend(handles=[d_patch, r_patch])
plt.tight_layout()
st.pyplot(fig)

st.divider()

# --- Full table ---
st.subheader("All races")

def color_margin(val):
    if val > 0:
        intensity = min(int(abs(val) * 12), 180)
        return f"background-color: rgba(58,122,191,{intensity/255:.2f})"
    else:
        intensity = min(int(abs(val) * 12), 180)
        return f"background-color: rgba(192,57,43,{intensity/255:.2f})"

styled = df.style.map(color_margin, subset=["Margin"])
st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()

# --- Per-state drilldown ---
st.subheader("State drilldown")
selected = st.selectbox("Select a state", df["State"].tolist())
row = df[df["State"] == selected].iloc[0]

c1, c2 = st.columns(2)
with c1:
    st.metric(f"🔵 {row['Dem']}", f"{row['Dem %']}%")
with c2:
    st.metric(f"🔴 {row['Rep']}", f"{row['Rep %']}%")

margin_label = f"+{abs(row['Margin'])} {'D' if row['Margin'] > 0 else 'R'}"
if row["Flip"] == "⚡":
    st.caption("⚡ Projected flip from current party")
st.progress(
    int(row["Dem %"]) / 100,
    text=f"Dem {row['Dem %']}% · Rep {row['Rep %']}% · Margin {margin_label}"
)