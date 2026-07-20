import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from senate_model import STATES_WITH_2026_RACES
from senate_model import predict_all_races, project_senate_control

st.set_page_config(page_title="2026 Senate Predictor", layout="wide")
st.title("🗳️ 2026 Senate Election Predictor")
st.caption("Weighted polling average · Credibility × recency decay · Updates on refresh")

PARTY_COLOR = {"D": "#3a7abf", "R": "#c0392b", "I": "#8e44ad"}
PARTY_ICON  = {"D": "🔵", "R": "🔴", "I": "🟣"}

@st.cache_data(ttl=300)

def plot_margin_map(df):
    """
Plots projection data into an interactive map in the dashboard.
    """
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

    # Split into two traces so lean-only races render visibly different
    # (reduced opacity + explicit hover label). Shared zmin/zmax pins both
    # traces to ONE color scale — without it each trace would normalize its
    # own colors and a -42 lean-only WY would recalibrate red differently
    # than a polled -9 OH.
    polled    = df[df["Basis"] == "polls"]
    lean_only = df[df["Basis"] == "lean only"]
    zmin, zmax = df["Margin"].min(), df["Margin"].max()

    if not lean_only.empty:
        fig.add_trace(go.Choropleth(
            locations=lean_only["State"],
            locationmode="USA-states",
            z=lean_only["Margin"],
            colorscale="RdBu",
            zmid=0,
            zmin=zmin,
            zmax=zmax,
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
        colorscale="RdBu",
        zmid=0,
        zmin=zmin,
        zmax=zmax,
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

    fig.update_layout(
        geo=dict(scope="usa"),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig

def plot_race_margins(df):
    """
    Interactive horizontal bar chart of leader margins per race. Error bars
    show combined polling standard error (leader + challenger, independent).
    """
    colors = [PARTY_COLOR.get(p, "#7f8c8d") for p in df["Leader Party"]]
    # Fold the ± into precomputed text: lean-only rows have Margin StdErr = None
    # (NaN in the DataFrame) and would otherwise render as "±nan" in the hover.
    # Plotly silently skips NaN entries in the error_x array, so lean-only bars
    # simply show no error bar.
    hover_text = [
        f"{row['Leader']} {row['Leader %']}% vs {row['Challenger']} {row['Challenger %']}%"
        + (f" · ±{row['Margin StdErr']:.1f} poll stderr" if pd.notna(row["Margin StdErr"]) else " · lean only, no polls")
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

    # Dummy traces so the leader-party colors get a legend (the real trace
    # above is a single series, colored per-bar).
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

def load_predictions():
    """
    Load and process election predictions data with caching.

    This function retrieves predictions for all races, calculates Senate control
    projections, and structures the data into a DataFrame suitable for
    visualization. The results are cached for 5 minutes (300 seconds) to improve
    performance and reduce redundant calculations.

    The function processes race predictions by grouping candidates by state,
    identifying the top two candidates by projected vote percentage, and
    calculating vote margins. The margin convention uses positive values when a
    non-Republican leads and negative values when a Republican leads, enabling
    meaningful left-right spectrum visualization regardless of party composition
    (including Independent candidates).

    Returns:
        tuple: A three-element tuple containing:
            - pandas.DataFrame: Processed election data with columns for State,
              Leader, Leader %, Leader Party, Challenger, Challenger %, Margin,
              and Flip indicator, sorted by margin in descending order
            - Any: Climate data from the prediction model
            - Any: Senate control projection results

    Notes:
        States with fewer than two candidates are excluded from the results.
        Incumbent candidates are marked with an asterisk (*) in their party label.
        Winners are prefixed with a star symbol (★) in their label.
        Flip races are indicated with a lightning bolt symbol (⚡) in the Flip
        column.
        The margin is calculated as the difference between leader and challenger
        projected percentages, with sign based on whether the leader is Republican.
    """
    predictions, climate, nominees_count = predict_all_races()
    control = project_senate_control(predictions)

    # Group by state only — do NOT assume a "D" and "R" key. A race can have
    # any combination of parties (e.g. NE 2026 is I vs R, no Dem candidate).
    seen = {}
    for r in predictions:
        seen.setdefault(r["state"], []).append(r)

    rows = []
    for state, cands in seen.items():
        # Top 2 by projected pct = the actual contest, same filter logic as
        # load_historical.py's top-2-by-votes rule. Skip if fewer than 2
        # candidates have data for this state.
        cands = sorted(cands, key=lambda c: c["projected"], reverse=True)
        if len(cands) < 2:
            continue
        leader, challenger = cands[0], cands[1]

        lead_size = leader["projected"] - challenger["projected"]
        # Sign convention: positive = leader is not Republican, negative = Republican leads.
        # Keeps the chart's left/right spectrum meaningful even with an I candidate.
        margin = -lead_size if leader["party"] == "R" else lead_size
        # Combined polling uncertainty on the margin, assuming leader/challenger
        # poll error is independent. Lean-only races have poll_stderr=None —
        # they get NO error bar (None), not a zero-width one that would claim
        # false certainty.
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

df, climate, control = load_predictions()

# --- Economic climate caption ---
direction = "favors Democrats" if climate > 0 else "favors Republicans"
st.caption(f"Economic climate score: **{climate:+.3f}** ({direction}) · Adjustment: ±{abs(climate * 0.3 * 10):.1f}pp")

st.subheader("2026 Senate map")
st.plotly_chart(plot_margin_map(df), use_container_width=True)

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

st.divider()

# --- Margin chart ---
st.subheader("Race margins")
st.plotly_chart(plot_race_margins(df), use_container_width=True)

st.divider()

# --- Full table ---
st.subheader("All races")

def color_margin(val):
    """
    Generate a CSS background color style string based on a numeric value's
    sign and magnitude. Positive values produce blue backgrounds, negative
    values produce red backgrounds, with intensity proportional to the
    absolute value.

    Parameters
    ----------
    val : numeric
        The value used to determine background color intensity and hue. Positive
        values result in blue backgrounds, negative values result in red
        backgrounds.

    Returns
    -------
    str
        A CSS background-color style string in rgba format with calculated
        opacity based on the input value's magnitude.
    """
    intensity = min(int(abs(val) * 12), 180)
    color = "58,122,191" if val > 0 else "192,57,43"
    return f"background-color: rgba({color},{intensity/255:.2f})"

styled = (df[["State", "Leader", "Leader %", "Challenger", "Challenger %", "Margin", "Basis", "Flip"]]
          .style
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