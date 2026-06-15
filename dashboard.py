import sys

# When invoked via `python dashboard.py`, re-launch under `streamlit run`
try:
    from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx as _ctx
except ImportError:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _ctx  # older streamlit

if _ctx() is None:
    import subprocess
    raise SystemExit(subprocess.call(["streamlit", "run", __file__]))

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from model import predict_all_races, project_senate_control, get_approval_score, ECON_WEIGHT, APPROVAL_WEIGHT
from charts import build_margins_fig, build_vote_shares_fig, build_seat_count_fig

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
            "lean_baseline":  r.get("lean_baseline"),
            "adjustment":     r["adjustment"],
            "appr_adj":       r.get("appr_adj", 0.0),
            "pct":            r["projected"],
            "is_incumbent":   r["is_incumbent"],
            "is_flip":        r.get("is_flip", False),
            "winner":         r.get("winner", False),
            "runoff_likely":  r.get("runoff_likely", False),
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

        d_party   = "D*" if d["is_incumbent"] else "D"
        r_party   = "R*" if r["is_incumbent"] else "R"
        dem_label = f"{'★ ' if d['winner'] else ''}{d_party} {d['name']}"
        rep_label = f"{'★ ' if r['winner'] else ''}{r_party} {r['name']}"

        rows.append({
            "State":    state,
            "Dem":      dem_label,
            "Dem %":    d["pct"],
            "Rep":      rep_label,
            "Rep %":    r["pct"],
            "Margin":   round(margin, 1),
            "Leader":   winner_party,
            "Flip":     "⚡" if winner_data["is_flip"] else "",
            "Runoff":   "🔄" if winner_data.get("runoff_likely") else "",
        })

    approval_pct = get_approval_score()
    df = pd.DataFrame(rows).sort_values("Margin", ascending=False)
    return df, climate, control, approval_pct, predictions

df, climate, control, approval_pct, predictions = load_predictions()

# --- Economic climate caption ---
direction = "favors Democrats" if climate > 0 else "favors Republicans"
appr_str = f" · Approval: {approval_pct}% (±{abs((approval_pct - 50) / 50 * APPROVAL_WEIGHT * 10):.2f}pp)" if approval_pct else ""
st.caption(f"Economic climate score: **{climate:+.3f}** ({direction}) · Econ adj: ±{abs(climate * ECON_WEIGHT * 10):.1f}pp{appr_str}")

# --- Senate control banner ---
st.divider()
_seat_line = (f"D: {control['D']} · R: {control['R']} · "
              f"{control['seats_remaining']} unassigned · 100 total seats")
if control["control"] == "Democrats":
    st.success(f"🔵 Projected Senate control: **Democrats**  —  {_seat_line}")
elif control["control"] == "Republicans":
    st.error(f"🔴 Projected Senate control: **Republicans**  —  {_seat_line}")
else:
    st.warning(f"⚠️ Senate control unclear  —  {_seat_line}")

if control["tiebreaker"]:
    st.caption("50–50 tie — VP casts tiebreaking vote → Republicans control")

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

# --- Seat count chart ---
st.subheader("Projected seat count")
seat_fig = build_seat_count_fig(control)
st.pyplot(seat_fig)
plt.close(seat_fig)

st.divider()

# --- Race margins chart ---
st.subheader("Race margins")
margins_fig, *_ = build_margins_fig(predictions, control)
st.pyplot(margins_fig)
plt.close(margins_fig)

st.divider()

# --- Vote share chart ---
st.subheader("Projected vote shares")
shares_fig, *_ = build_vote_shares_fig(predictions)
st.pyplot(shares_fig)
plt.close(shares_fig)

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

styled = (df.style
            .map(color_margin, subset=["Margin"])
            .format({"Margin": lambda v: f"{v:+.1f}"}))
st.dataframe(styled, width="stretch", hide_index=True)

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
if row.get("Runoff") == "🔄":
    st.caption("🔄 Runoff likely — no candidate projected to clear 50%")
st.progress(
    int(row["Dem %"]) / 100,
    text=f"Dem {row['Dem %']}% · Rep {row['Rep %']}% · Margin {margin_label}"
)
