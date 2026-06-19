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
from senate_model import predict_all_races, project_senate_control, get_approval_score, ECON_WEIGHT, APPROVAL_WEIGHT
from house_model import predict_house_races, project_house_control
from charts import build_margins_fig, build_vote_shares_fig, build_seat_count_fig, build_house_margins_fig, build_house_seat_count_fig, build_generic_ballot_fig

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


@st.cache_data(ttl=300)
def load_house_predictions():
    results, generic_ballot, h_climate, h_approval = predict_house_races()
    h_control = project_house_control(results, generic_ballot)

    seen = {}
    for r in results:
        key = (r["state"], r["district"])
        if key not in seen:
            seen[key] = {}
        seen[key][r["party"]] = {
            "name":         r["name"],
            "poll_avg":     r["poll_avg"],
            "lean_baseline":r["lean_baseline"],
            "adjustment":   r["adjustment"],
            "appr_adj":     r["appr_adj"],
            "pct":          r["projected"],
            "is_incumbent": r["is_incumbent"],
            "is_flip":      r.get("is_flip", False),
            "winner":       r.get("winner", False),
        }

    rows = []
    for (state, district), parties in seen.items():
        d_key = "D" if "D" in parties else ("I" if "I" in parties else None)
        if d_key is None or "R" not in parties:
            continue
        d = parties[d_key]
        r = parties["R"]
        margin = d["pct"] - r["pct"]
        winner_party = d_key if d["winner"] else "R"
        winner_data  = d if d["winner"] else r

        d_party   = f"{d_key}*" if d["is_incumbent"] else d_key
        r_party   = "R*" if r["is_incumbent"] else "R"
        dem_label = f"{'★ ' if d['winner'] else ''}{d_party} {d['name']}"
        rep_label = f"{'★ ' if r['winner'] else ''}{r_party} {r['name']}"

        rows.append({
            "District": f"{state}-{district}",
            "Dem":      dem_label,
            "Dem %":    d["pct"],
            "Rep":      rep_label,
            "Rep %":    r["pct"],
            "Margin":   round(margin, 1),
            "Leader":   winner_party,
            "Flip":     "⚡" if winner_data["is_flip"] else "",
        })

    if rows:
        house_df = pd.DataFrame(rows).sort_values("Margin", ascending=False)
    else:
        house_df = pd.DataFrame(columns=["District", "Dem", "Dem %", "Rep", "Rep %", "Margin", "Leader", "Flip"])
    return house_df, results, generic_ballot, h_control, h_climate, h_approval

house_df, house_results, generic_ballot, house_control, house_climate, house_approval = load_house_predictions()

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
if not df.empty:
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
        row["Dem %"] / 100,
        text=f"Dem {row['Dem %']}% · Rep {row['Rep %']}% · Margin {margin_label}"
    )
else:
    st.info("No Senate race data available.")

# ===========================================================================
#  HOUSE
# ===========================================================================
st.divider()
st.divider()
st.title("🏛️ 2026 House Election Predictor")
st.caption("Tier 1 (polled, competitive districts) only — the remaining ~375 unpolled "
           "seats feed into the seat projection via generic ballot + CPVI, but aren't "
           "shown race-by-race since there's no district-level polling for them yet.")

# --- Generic ballot + climate caption ---
gen_d = generic_ballot.get("D")
gen_r = generic_ballot.get("R")
gen_str = f"Generic ballot: D {gen_d}% · R {gen_r}% (margin {gen_d - gen_r:+.1f}pp)" if gen_d is not None and gen_r is not None else "Generic ballot: no data"
h_direction = "favors Democrats" if house_climate > 0 else "favors Republicans"
h_appr_str = f" · Approval: {house_approval}%" if house_approval else ""
st.caption(f"{gen_str} · Climate score: **{house_climate:+.3f}** ({h_direction}){h_appr_str}")

# --- House control banner ---
st.divider()
_house_seat_line = f"D: {house_control['D']} · R: {house_control['R']} · 435 total seats"
if house_control["control"] == "Democrats":
    st.success(f"🔵 Projected House control: **Democrats**  —  {_house_seat_line}")
elif house_control["control"] == "Republicans":
    st.error(f"🔴 Projected House control: **Republicans**  —  {_house_seat_line}")
else:
    st.warning(f"⚠️ House control unclear  —  {_house_seat_line}")

st.caption(f"Generic ballot swing from 2024 baseline: {house_control['seat_swing']:+d} seats · "
           f"Polled Tier-1 races called — R: {house_control['polled_R']}  D/I: {house_control['polled_D']}")

st.divider()

# --- Summary metrics ---
col1, col2, col3, col4 = st.columns(4)
h_dem_leads = (house_df["Leader"] != "R").sum()
h_rep_leads = (house_df["Leader"] == "R").sum()
h_flips     = house_df["Flip"].str.contains("⚡").sum()
col1.metric("Dem/Ind leads", h_dem_leads)
col2.metric("Rep leads", h_rep_leads)
col3.metric("Projected flips", h_flips)
col4.metric("Tier-1 races tracked", len(house_df))

st.divider()

# --- House seat count chart ---
st.subheader("Projected House seat count")
house_seat_fig = build_house_seat_count_fig(house_control)
st.pyplot(house_seat_fig)
plt.close(house_seat_fig)

st.divider()

# --- Generic ballot chart ---
st.subheader("National generic ballot")
generic_fig = build_generic_ballot_fig(generic_ballot)
st.pyplot(generic_fig)
plt.close(generic_fig)

st.divider()

# --- House race margins chart ---
st.subheader("Tier-1 race margins")
n_races = len(house_df)
top_n = None
if n_races > 15:
    top_n = st.slider("Show closest N races", min_value=5, max_value=n_races, value=15)
house_margins_fig, *_ = build_house_margins_fig(house_results, house_control, top_n=top_n)
st.pyplot(house_margins_fig)
plt.close(house_margins_fig)

st.divider()

# --- Full House table ---
st.subheader("All Tier-1 House races")
styled_house = (house_df.style
                .map(color_margin, subset=["Margin"])
                .format({"Margin": lambda v: f"{v:+.1f}"}))
st.dataframe(styled_house, width="stretch", hide_index=True)

st.divider()

# --- Per-district drilldown ---
st.subheader("District drilldown")
if not house_df.empty:
    h_selected = st.selectbox("Select a district", house_df["District"].tolist())
    h_row = house_df[house_df["District"] == h_selected].iloc[0]

    hc1, hc2 = st.columns(2)
    with hc1:
        st.metric(f"🔵 {h_row['Dem']}", f"{h_row['Dem %']}%")
    with hc2:
        st.metric(f"🔴 {h_row['Rep']}", f"{h_row['Rep %']}%")

    h_margin_label = f"+{abs(h_row['Margin'])} {h_row['Leader']}"
    if h_row["Flip"] == "⚡":
        st.caption("⚡ Projected flip from current party")
    st.progress(
        h_row["Dem %"] / 100,
        text=f"Dem {h_row['Dem %']}% · Rep {h_row['Rep %']}% · Margin {h_margin_label}"
    )
else:
    st.info("No Tier-1 House district data available.")