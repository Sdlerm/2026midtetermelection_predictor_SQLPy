# dashboard.py — 2026 election predictor (Senate + House Tier 1)
#
# Regenerated 2026-07-20. Changes from previous version:
#   * ALL imports consolidated at top (kills the `from turtle import st`
#     auto-import bug and the whole use-before-import class with it)
#   * @st.cache_data(ttl=300) moved back onto load_predictions() — it had
#     drifted onto plot_margin_map (caching the cheap function, not the
#     expensive one)
#   * House flips DataFrame renamed house_flips (was shadowing the Senate
#     flip count)
#   * House map now uses categorical rating bins (Safe/Likely/Lean/Toss Up)
#     driven by calibration.py thresholds, replacing the continuous gradient.
#     (Requires LEAN=10.0 < LIKELY=15.0 in calibration.py — applied 2026-07-20.)

import json
import math
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from calibration import (
    TOSSUP_MARGIN_THRESHOLD,
    LEAN_MARGIN_THRESHOLD,
    LIKELY_MARGIN_THRESHOLD,
)
from house_model import predict_house_races
from senate_model import ECON_WEIGHT, STATES_WITH_2026_RACES, predict_all_races, project_senate_control

# ---------------------------------------------------------------------------
# Page config & shared constants
# ---------------------------------------------------------------------------

st.set_page_config(page_title="2026 Election Predictor", layout="wide")
st.title("🗳️ 2026 Election Predictor")
st.caption("Weighted polling average · Credibility × recency decay · Updates on refresh")

PARTY_COLOR = {"D": "#3a7abf", "R": "#c0392b", "I": "#8e44ad"}
PARTY_ICON  = {"D": "🔵", "R": "🔴", "I": "🟣"}

STATE_FIPS = {
    "AL":"01","AK":"02","AZ":"04","AR":"05","CA":"06","CO":"08","CT":"09","DE":"10",
    "FL":"12","GA":"13","HI":"15","ID":"16","IL":"17","IN":"18","IA":"19","KS":"20",
    "KY":"21","LA":"22","ME":"23","MD":"24","MA":"25","MI":"26","MN":"27","MS":"28",
    "MO":"29","MT":"30","NE":"31","NV":"32","NH":"33","NJ":"34","NM":"35","NY":"36",
    "NC":"37","ND":"38","OH":"39","OK":"40","OR":"41","PA":"42","RI":"44","SC":"45",
    "SD":"46","TN":"47","TX":"48","UT":"49","VT":"50","VA":"51","WA":"53","WV":"54",
    "WI":"55","WY":"56",
}
AT_LARGE = {"AK", "DE", "ND", "SD", "VT", "WY"}   # census GEOIDs use '00'; our DB stores '01'
REDRAWN  = {"TX", "NC", "OH", "FL"}               # 2025 mid-decade maps; boundary file shows OLD lines

# Rating bins — colors approximate the standard ratings-map palette.
RATING_COLORS = {
    "Safe D":   "#0d2b52",
    "Likely D": "#2563cf",
    "Lean D":   "#9ec8f5",
    "Toss Up":  "#f5efad",
    "Lean R":   "#f0a3bb",
    "Likely R": "#d63b2f",
    "Safe R":   "#7a1210",
}
RATING_ORDER = list(RATING_COLORS)  # index = z value for the discrete colorscale


def rate(margin):
    """Signed margin (+ = non-R leads) -> rating bucket per calibration.py.
    Assumes thresholds ordered TOSSUP < LEAN < LIKELY (competitive -> settled)."""
    a = abs(margin)
    if a < TOSSUP_MARGIN_THRESHOLD:
        return "Toss Up"
    side = "D" if margin > 0 else "R"
    if a < LEAN_MARGIN_THRESHOLD:
        return f"Lean {side}"
    if a < LIKELY_MARGIN_THRESHOLD:
        return f"Likely {side}"
    return f"Safe {side}"


def _geoid(state, district):
    """DB (state, '07') -> census GEOID '2607'. At-large: census says '00'."""
    d = "00" if state in AT_LARGE else district
    return STATE_FIPS[state] + d


# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_predictions():
    """Senate predictions -> display DataFrame + climate + control projection.

    Lean-only races (no polls anywhere in the matchup) carry Basis='lean only'
    and Margin StdErr=None — they get NO error bar rather than a zero-width
    one that would claim false certainty.
    """
    predictions, climate, nominees_count = predict_all_races()
    control = project_senate_control(predictions)

    seen = {}
    for r in predictions:
        seen.setdefault(r["state"], []).append(r)

    rows = []
    for state, cands in seen.items():
        cands = sorted(cands, key=lambda c: c["projected"], reverse=True)
        if len(cands) < 2:
            continue
        leader, challenger = cands[0], cands[1]

        lead_size = leader["projected"] - challenger["projected"]
        # Sign convention: positive = leader is not Republican. Keeps the
        # left/right spectrum meaningful even with an I candidate.
        margin = -lead_size if leader["party"] == "R" else lead_size

        has_polls = leader.get("has_polls", True) and challenger.get("has_polls", True)
        if has_polls:
            margin_stderr = round(math.sqrt(
                (leader["poll_stderr"] or 0) ** 2 + (challenger["poll_stderr"] or 0) ** 2
            ), 1)
        else:
            margin_stderr = None

        def label(c):
            party = f"{c['party']}*" if c["is_incumbent"] else c["party"]
            star = "★ " if c.get("winner") else ""
            return f"{star}{party} {c['name']}"

        rows.append({
            "State":          state,
            "Leader":         label(leader),
            "Leader %":       leader["projected"],
            "Leader Party":   leader["party"],
            "Challenger":     label(challenger),
            "Challenger %":   challenger["projected"],
            "Margin":         round(margin, 1),
            "Margin StdErr":  margin_stderr,
            "Basis":          "polls" if has_polls else "lean only",
            "Flip":           "⚡" if leader.get("is_flip", False) else "",
        })

    return pd.DataFrame(rows).sort_values("Margin", ascending=False), climate, control


@st.cache_data(ttl=300)
def load_house_df():
    """House Tier 1 predictions -> display DataFrame with GEOID + rating."""
    results, _, _ = predict_house_races()
    by_race = {}
    for r in results:
        by_race.setdefault(r["race"], []).append(r)

    rows = []
    for race, cands in by_race.items():
        cands = sorted(cands, key=lambda c: c["projected"], reverse=True)
        if len(cands) < 2:
            continue
        lead, chal = cands[0], cands[1]
        gap = lead["projected"] - chal["projected"]
        margin = -gap if lead["party"] == "R" else gap
        rows.append({
            "Race":         race,
            "GEOID":        _geoid(lead["state"], lead["district"]),
            "Leader":       f"{lead['party']} {lead['name']}",
            "Leader %":     lead["projected"],
            "Challenger":   f"{chal['party']} {chal['name']}",
            "Challenger %": chal["projected"],
            "Margin":       round(margin, 1),
            "Rating":       rate(margin),
            "Flip":         "⚡" if lead.get("is_flip") else "",
        })
    return pd.DataFrame(rows)


CD_GEOJSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cd119.geojson")


@st.cache_data
def load_cd_geojson():
    with open(CD_GEOJSON_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def plot_margin_map(df):
    """Senate choropleth. Lean-only races render at reduced opacity on their
    own trace; shared zmin/zmax pins both traces to ONE color scale (without
    it, a lean-only WY at -42 would recalibrate red differently than a
    polled -9 OH)."""
    tracked_states = set(df["State"])
    untracked_states = [s for s in STATES_WITH_2026_RACES if s not in tracked_states]

    fig = go.Figure()

    if untracked_states:
        fig.add_trace(go.Choropleth(
            locations=untracked_states,
            locationmode="USA-states",
            z=[0] * len(untracked_states),
            colorscale=[[0, "#d9d9d9"], [1, "#d9d9d9"]],
            showscale=False,
            marker_line_color="white",
            text=untracked_states,
            hovertemplate="%{text}: not yet tracked<extra></extra>",
        ))

    polled    = df[df["Basis"] == "polls"]
    lean_only = df[df["Basis"] == "lean only"]
    zmin, zmax = df["Margin"].min(), df["Margin"].max()

    if not lean_only.empty:
        fig.add_trace(go.Choropleth(
            locations=lean_only["State"],
            locationmode="USA-states",
            z=lean_only["Margin"],
            colorscale="RdBu", zmid=0, zmin=zmin, zmax=zmax,
            marker_opacity=0.45,
            marker_line_color="white",
            showscale=False,
            text=lean_only["State"],
            customdata=lean_only[["Leader", "Leader %", "Challenger", "Challenger %"]],
            hovertemplate=(
                "<b>%{text}</b>  (%{z:+.1f} margin) · LEAN-ONLY, no polls"
                "<br>%{customdata[0]}: %{customdata[1]}%"
                "<br>%{customdata[2]}: %{customdata[3]}%"
                "<extra></extra>"
            ),
        ))

    fig.add_trace(go.Choropleth(
        locations=polled["State"],
        locationmode="USA-states",
        z=polled["Margin"],
        colorscale="RdBu", zmid=0, zmin=zmin, zmax=zmax,
        marker_line_color="white",
        colorbar_title="D margin",
        text=polled["State"],
        customdata=polled[["Leader", "Leader %", "Challenger", "Challenger %"]],
        hovertemplate=(
            "<b>%{text}</b>  (%{z:+.1f} margin)"
            "<br>%{customdata[0]}: %{customdata[1]}%"
            "<br>%{customdata[2]}: %{customdata[3]}%"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(geo=dict(scope="usa"), margin=dict(l=0, r=0, t=10, b=0))
    return fig


def plot_house_map(hdf):
    """House Tier 1 choropleth with discrete rating bins. z is the rating's
    index in RATING_ORDER; the colorscale has hard stops (each color repeated
    at both ends of its band) so bins never blend into each other."""
    gj = load_cd_geojson()

    n = len(RATING_ORDER)
    discrete_scale = []
    for i, r_name in enumerate(RATING_ORDER):
        discrete_scale.append([i / n, RATING_COLORS[r_name]])
        discrete_scale.append([(i + 1) / n, RATING_COLORS[r_name]])

    z = hdf["Rating"].map(RATING_ORDER.index)

    fig = go.Figure(go.Choropleth(
        geojson=gj,
        featureidkey="properties.GEOID",
        locations=hdf["GEOID"],
        z=z,
        zmin=0, zmax=n,          # n (not n-1): z=k must fall inside band k
        colorscale=discrete_scale,
        showscale=False,          # dot legend below replaces the colorbar
        marker_line_color="white",
        marker_line_width=0.3,
        customdata=hdf[["Race", "Rating", "Leader", "Leader %", "Challenger", "Challenger %"]],
        hovertemplate=(
            "<b>%{customdata[0]}</b> — %{customdata[1]}"
            "<br>%{customdata[2]}: %{customdata[3]}%"
            "<br>%{customdata[4]}: %{customdata[5]}%"
            "<extra></extra>"
        ),
    ))
    fig.update_geos(scope="usa", visible=False)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    return fig


def plot_race_margins(df):
    """Senate horizontal bar chart of leader margins with poll-stderr error
    bars. Lean-only rows have Margin StdErr = NaN; plotly silently skips NaN
    in the error array, so those bars simply show no error bar."""
    colors = [PARTY_COLOR.get(p, "#7f8c8d") for p in df["Leader Party"]]
    hover_text = [
        f"{row['Leader']} {row['Leader %']}% vs {row['Challenger']} {row['Challenger %']}%"
        + (f" · ±{row['Margin StdErr']:.1f} poll stderr" if pd.notna(row["Margin StdErr"])
           else " · lean only, no polls")
        + (" · ⚡ projected flip" if row["Flip"] == "⚡" else "")
        for _, row in df.iterrows()
    ]

    fig = go.Figure(go.Bar(
        x=df["Margin"],
        y=df["State"],
        orientation="h",
        marker_color=colors,
        error_x=dict(type="data", array=df["Margin StdErr"], visible=True, thickness=1, width=3),
        text=hover_text,
        hovertemplate="<b>%{y}</b>: %{x:+.1f} margin<br>%{text}<extra></extra>",
    ))

    for party in ["D", "R", "I"]:
        if party in df["Leader Party"].values:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=PARTY_COLOR[party]),
                name=f"{party} leads",
            ))

    fig.add_vline(x=0, line_color="black", line_width=0.8)
    fig.update_layout(
        xaxis_title="Leader margin (positive = non-Republican leads)",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=0, r=0, t=10, b=0),
        height=len(df) * 28 + 150,
        legend=dict(title=None),
    )
    return fig


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

df, climate, control = load_predictions()

direction = "favors Democrats" if climate > 0 else "favors Republicans"
st.caption(f"Economic climate score: **{climate:+.3f}** ({direction}) · "
           f"Adjustment: ±{abs(climate * ECON_WEIGHT * 10):.1f}pp")

# --- Senate map ---
st.subheader("2026 Senate map")
st.plotly_chart(plot_margin_map(df), width="stretch")

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

if control["flips"]:
    flip_names = "  ·  ".join([f"⚡ {f['state']} → {f['party']}" for f in control["flips"]])
    st.caption(f"Projected flips: {flip_names}")

# --- House map ---
st.divider()
st.subheader("2026 House map — Tier 1 (polled districts only)")
hdf = load_house_df()
st.caption(f"{len(hdf)} of 435 districts modeled · uncolored districts are unmodeled, "
           f"not safe · ⚠️ TX/NC/OH/FL boundaries shown are pre-2025 redraw")
if os.path.exists(CD_GEOJSON_PATH):
    st.caption("   ".join(f"● {r_name}" for r_name in RATING_ORDER))
    st.plotly_chart(plot_house_map(hdf), width="stretch")
else:
    st.warning(
        "House map skipped — `data/cd119.geojson` not found. Download the 119th "
        "Congress district boundaries (e.g. Census cartographic boundary file "
        "`cb_2024_us_cd119_500k`, converted to GeoJSON with a `GEOID` property) "
        "and save as `data/cd119.geojson`. Table below still works."
    )
    st.dataframe(
        hdf[["Race", "Leader", "Leader %", "Challenger", "Challenger %", "Margin", "Rating", "Flip"]],
        width="stretch", hide_index=True,
    )

house_flips = hdf[hdf["Flip"] == "⚡"]
if len(house_flips):
    st.caption("Projected flips: " +
               " · ".join(house_flips["Race"] + " → " + house_flips["Leader"].str[0]))

# --- Summary metrics (Senate) ---
st.divider()
col1, col2, col3, col4, col5 = st.columns(5)
dem_leads = (df["Leader Party"] == "D").sum()
rep_leads = (df["Leader Party"] == "R").sum()
ind_leads = (df["Leader Party"] == "I").sum()
flips     = df["Flip"].str.contains("⚡").sum()
col1.metric("Dem leads", dem_leads)
col2.metric("Rep leads", rep_leads)
col3.metric("Ind leads", ind_leads)
col4.metric("Projected flips", flips)
col5.metric("Races tracked", len(df))

# --- Margin chart ---
st.divider()
st.subheader("Race margins")
st.plotly_chart(plot_race_margins(df), width="stretch")

# --- Full table ---
st.divider()
st.subheader("All races")

def color_margin(val):
    """Blue/red background with intensity proportional to |margin|."""
    intensity = min(int(abs(val) * 12), 180)
    color = "58,122,191" if val > 0 else "192,57,43"
    return f"background-color: rgba({color},{intensity/255:.2f})"

styled = (df[["State", "Leader", "Leader %", "Challenger", "Challenger %", "Margin", "Basis", "Flip"]]
          .style
          .map(color_margin, subset=["Margin"])
          .format({"Margin": lambda v: f"{v:+.1f}"}))
st.dataframe(styled, width="stretch", hide_index=True)

# --- Per-state drilldown ---
st.divider()
st.subheader("State drilldown")
selected = st.selectbox("Select a state", df["State"].tolist())
row = df[df["State"] == selected].iloc[0]

c1, c2 = st.columns(2)
with c1:
    st.metric(f"{PARTY_ICON.get(row['Leader Party'], '⚪')} {row['Leader']}", f"{row['Leader %']}%")
with c2:
    st.metric(f"{row['Challenger']}", f"{row['Challenger %']}%")

margin_label = f"{abs(row['Margin'])} {'non-R' if row['Margin'] > 0 else 'R'}"
if row["Basis"] == "lean only":
    st.caption("📐 Lean-only projection — no polling exists for this race; "
               "numbers derive from structural lean + national adjustments.")
if row["Flip"] == "⚡":
    st.caption("⚡ Projected flip from current party")
st.progress(
    int(row["Leader %"]) / 100,
    text=f"{row['Leader']} {row['Leader %']}% · {row['Challenger']} {row['Challenger %']}% · Margin {margin_label}"
)