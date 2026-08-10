# dashboard.py — 2026 election predictor (Senate + House Tier 2)
#
# Regenerated 2026-07-20. Changes from previous version:
#   * ALL imports consolidated at top (kills the `from turtle import st`
#     auto-import bug and the whole use-before-import class with it)
#   * @st.cache_data(ttl=300) moved back onto load_predictions() — it had
#     drifted onto plot_margin_map (caching the cheap function, not the
#     expensive one)
#   * House flips DataFrame renamed house_flips (was shadowing the Senate
#     flip count)
#   * House map now uses categorical rating bins (Safe/Likely/Lean/Tilt)
#     driven by calibration.py thresholds, replacing the continuous gradient.
#     (Requires LEAN=10.0 < LIKELY=15.0 in calibration.py — applied 2026-07-20.)
#   * 2026-08-10: the neutral "Toss Up" bin is gone. Every district is sided
#     with whichever candidate has the greater projected vote share; margins
#     under TILT_MARGIN_THRESHOLD land in "Tilt D"/"Tilt R" instead of a
#     single yellow bucket. The map now answers "who leads?" everywhere.

import json
import math
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import house_sensitivity
import monte_carlo_house as mc_house
import monte_carlo_senate as mc_senate
from calibration import (
    TILT_MARGIN_THRESHOLD,
    LEAN_MARGIN_THRESHOLD,
    LIKELY_MARGIN_THRESHOLD,
    HOUSE_MAJORITY,
)
from house_model import national_environment_margin, predict_house_races
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

# Rating bins — a diverging D-to-R ramp with NO neutral color in the middle.
# Every band names a side, so the map's color always answers "who is ahead
# here?" The two "Tilt" bands are the closest races: pale, but unmistakably
# blue or red rather than a shared yellow.
RATING_COLORS = {
    "Safe D":   "#0d2b52",
    "Likely D": "#2563cf",
    "Lean D":   "#7fb1ec",
    "Tilt D":   "#c9dff5",
    "Tilt R":   "#f9ccd5",
    "Lean R":   "#ef98ac",
    "Likely R": "#d63b2f",
    "Safe R":   "#7a1210",
}
RATING_ORDER = list(RATING_COLORS)  # index = z value for the discrete colorscale


def rate(margin):
    """Signed margin (+ = D leads) -> rating bucket per calibration.py.

    Every margin gets a side: the bucket is named for whichever candidate has
    the greater projected vote share, however thin the gap. Sub-TILT margins
    are "Tilt D"/"Tilt R", not a neutral toss-up — a 0.3pt D lead is a (very
    soft) D projection, and the band is what conveys the softness.

    Assumes thresholds ordered TILT < LEAN < LIKELY (competitive -> settled).
    An exact 0.0 margin sides R, matching the D/R lead counts in the caption
    below, which split on `Margin > 0`."""
    a = abs(margin)
    side = "D" if margin > 0 else "R"
    if a < TILT_MARGIN_THRESHOLD:
        return f"Tilt {side}"
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

# Raw model output, cached once and shared by the display loaders AND the
# simulation loaders below. Without this split the models would run twice per
# refresh — once to build the tables, once to seed the Monte Carlo.
# Treat the returned lists as read-only: they are the cached objects themselves,
# and build_races() only reads them, which is what makes this safe.

@st.cache_data(ttl=300)
def load_senate_predictions():
    return predict_all_races()


@st.cache_data(ttl=300)
def load_house_predictions():
    return predict_house_races()


@st.cache_data(ttl=300)
def load_senate_sim():
    """1,000,000 simulated Senate elections -> probabilities + seat distribution.

    N_SIMS is deliberately left at its CLI value so the dashboard and
    monte_carlo_senate.py cannot disagree. RANDOM_SEED is None, so each cache
    refresh reshuffles; at 1M sims the MC standard error is ~0.05pp, well below
    displayed precision.
    """
    predictions, _climate, _nominees = load_senate_predictions()
    races = mc_senate.build_races(predictions)
    base_d, base_r = mc_senate.baseline_seats(predictions, races)
    return mc_senate.simulate(races, base_d, base_r)


@st.cache_data(ttl=300)
def load_house_sim():
    """1,000,000 simulated House elections -> probabilities + seat distribution.

    baseline_seats() takes only `races` here, unlike the Senate's
    (predictions, races): every House seat is up every cycle, so there is no
    "not up" remainder for a baseline to carry. See monte_carlo_house.
    """
    predictions, _climate, _approval = load_house_predictions()
    races = mc_house.build_races(predictions)
    base_d, base_r = mc_house.baseline_seats(races)
    return mc_house.simulate(races, base_d, base_r)


@st.cache_data(ttl=300)
def load_house_sensitivity():
    """The "+1pp national shift" caveat, DERIVED rather than typed.

    house_sensitivity.py existed for exactly this and was never wired up, so the
    warning box carried a hand-pasted "~38% to ~50%" that had already gone stale
    once. It went stale again the moment the national-environment term landed —
    the real figures moved by tens of points. Computing it from the same
    simulate() that produces the headline is the only way the two stay in sync."""
    predictions, _climate, _approval = load_house_predictions()
    races = mc_house.build_races(predictions)
    base_d, base_r = mc_house.baseline_seats(races)
    return house_sensitivity.format_caveat(
        house_sensitivity.majority_sensitivity(races, base_d, base_r)
    )


def _house_env_note():
    """One line on where the national environment came from — it is the largest
    single term in the lean-only districts, so its provenance belongs on screen
    next to the probabilities it drives."""
    env, source = national_environment_margin()
    return (f"The national environment applied to every district is **D{env:+.1f}** "
            f"on the margin scale, from {source}; it is inferred, not measured, "
            f"and it moves the seat total more than any other single input.")


def load_sim_or_error(loader, chamber):
    """
    Run a simulation loader, converting a seat-identity failure into a message
    instead of a crash.

    baseline_seats() raises ValueError when a chamber's seats don't sum
    correctly — deliberately, so a false forecast can never be returned. But an
    uncaught raise here would take down the ENTIRE page: one chamber's data gap
    would blank the other chamber's numbers and both maps, which are perfectly
    valid. Catching it per chamber keeps the failure loud and localized.

    Only ValueError is caught, and only around the loader. Anything else is a
    real bug and still propagates — a bare `except Exception` here would hide
    exactly the failures worth seeing.

    The cost of a failed chamber is near zero: baseline_seats raises before
    simulate() runs, so nothing is wasted re-deriving it on each rerun (a
    raising function is never cached).

    Returns (results, None) on success, (None, message) on identity failure.
    """
    try:
        return loader(), None
    except ValueError as err:
        return None, str(err)


def render_sim_error(chamber, message):
    """Standard treatment for a chamber whose seat identity is broken."""
    st.error(
        f"**{chamber} simulation unavailable — seat identity check failed.**\n\n"
        f"No probabilities are shown for the {chamber} because the seat totals "
        f"do not add up, and a P(control) computed over the wrong number of "
        f"seats would be confidently wrong rather than merely imprecise. "
        f"Everything else on this page is unaffected.\n\n"
        f"```\n{message}\n```"
    )


@st.cache_data(ttl=300)
def load_predictions():
    """Senate predictions -> display DataFrame + climate + control projection.

    Lean-only races (no polls anywhere in the matchup) carry Basis='lean only'
    and Margin StdErr=None — they get NO error bar rather than a zero-width
    one that would claim false certainty.
    """
    predictions, climate, nominees_count = load_senate_predictions()
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
    """House Tier 2 predictions -> display DataFrame with GEOID + rating.

    Covers all 435 districts. The Basis column separates poll-backed rows from
    lean-only ones; plot_house_map renders the latter at reduced opacity so
    coverage never reads as confidence."""
    results, _, _ = load_house_predictions()
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
            "Basis":        "polls" if lead.get("has_polls") else "lean only",
            "Flip":         "⚡" if lead.get("is_flip") else "",
        })
    return pd.DataFrame(rows).sort_values("Margin", ascending=False)


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

    # The color domain MUST be symmetric around 0, because 0 is where RdBu
    # turns from red to blue and 0 is also where the race changes hands.
    #
    # Using the raw data range breaks that: plotly IGNORES zmid whenever both
    # zmin and zmax are given explicitly, so a range like (-41.3, +23.3) puts
    # the white midpoint at -9.0. Every R-held state between -9 and 0 then
    # renders BLUE — which is exactly how FL (-5.7), MS (-4.2) and SC (-5.6)
    # came out looking like Democratic holds.
    #
    # Anchoring to ±max(|margin|) keeps the neutral point on 0, so the color a
    # state gets always agrees with the sign of its margin.
    zlim = float(max(abs(df["Margin"].min()), abs(df["Margin"].max()))) if len(df) else 1.0
    zlim = max(zlim, 1.0)          # guard: an all-ties df would collapse the scale
    zmin, zmax = -zlim, zlim

    if not lean_only.empty:
        fig.add_trace(go.Choropleth(
            locations=lean_only["State"],
            locationmode="USA-states",
            z=lean_only["Margin"],
            colorscale="RdBu", zmin=zmin, zmax=zmax,   # symmetric around 0; see note above
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
        colorscale="RdBu", zmin=zmin, zmax=zmax,   # symmetric around 0; see note above
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
    """House Tier 2 choropleth with discrete rating bins, all 435 districts.

    z is the rating's index in RATING_ORDER; the colorscale has hard stops
    (each color repeated at both ends of its band) so bins never blend.

    Two traces, same colorscale and same zmin/zmax: lean-only districts render
    at reduced opacity, matching plot_margin_map's treatment of unpolled Senate
    states. Sharing the scale matters — a separate scale per trace would map
    the same rating to different colors depending on which trace it landed in."""
    gj = load_cd_geojson()

    n = len(RATING_ORDER)
    discrete_scale = []
    for i, r_name in enumerate(RATING_ORDER):
        discrete_scale.append([i / n, RATING_COLORS[r_name]])
        discrete_scale.append([(i + 1) / n, RATING_COLORS[r_name]])

    hover_cols = ["Race", "Rating", "Leader", "Leader %", "Challenger", "Challenger %"]

    def _trace(sub, opacity, basis_note):
        return go.Choropleth(
            geojson=gj,
            featureidkey="properties.GEOID",
            locations=sub["GEOID"],
            z=sub["Rating"].map(RATING_ORDER.index),
            zmin=0, zmax=n,       # n (not n-1): z=k must fall inside band k
            colorscale=discrete_scale,
            showscale=False,      # dot legend below replaces the colorbar
            marker_opacity=opacity,
            marker_line_color="white",
            marker_line_width=0.3,
            customdata=sub[hover_cols],
            hovertemplate=(
                "<b>%{customdata[0]}</b> — %{customdata[1]}" + basis_note +
                "<br>%{customdata[2]}: %{customdata[3]}%"
                "<br>%{customdata[4]}: %{customdata[5]}%"
                "<extra></extra>"
            ),
        )

    fig = go.Figure()
    lean_only = hdf[hdf["Basis"] == "lean only"]
    polled    = hdf[hdf["Basis"] == "polls"]

    if not lean_only.empty:
        fig.add_trace(_trace(lean_only, 0.45, " · LEAN-ONLY, no polls"))
    if not polled.empty:
        fig.add_trace(_trace(polled, 1.0, ""))

    fig.update_geos(scope="usa", visible=False)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
    return fig


def plot_seat_distribution(dist, majority, chamber_label):
    """
    Histogram of simulated Democratic seat counts.

    One helper serves both chambers because both simulate() functions return
    `seat_distribution` in the same shape: {d_seats: n_simulations}.

    Bars at or above the majority line are blue, below are red, so the visual
    split IS the probability — the blue share of the mass is P(D majority). That
    is the point of showing a distribution instead of a point estimate: it makes
    a 212-seat mean with a 187–239 range read as genuinely uncertain rather than
    as a narrow miss.
    """
    seats  = sorted(dist)
    counts = [dist[s] for s in seats]
    total  = sum(counts) or 1
    colors = [PARTY_COLOR["D"] if s >= majority else PARTY_COLOR["R"] for s in seats]

    fig = go.Figure(go.Bar(
        x=seats,
        y=[c / total for c in counts],
        marker_color=colors,
        hovertemplate="D seats: %{x}<br>%{y:.2%} of simulations<extra></extra>",
    ))
    fig.add_vline(x=majority - 0.5, line_color="black", line_width=1.2, line_dash="dash",
                  annotation_text=f"{majority} = majority", annotation_position="top")
    fig.update_layout(
        xaxis_title=f"Democratic seats ({chamber_label})",
        yaxis_title="Share of simulations",
        margin=dict(l=0, r=0, t=30, b=0),
        height=260,
        showlegend=False,
        bargap=0.05,
    )
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
              f"{control['not_called']} unassigned · 100 total seats")
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

# --- Senate Monte Carlo ---
st.divider()
st.subheader("Senate outlook — 1,000,000 simulated elections")
_sen_sim, _sen_err = load_sim_or_error(load_senate_sim, "Senate")

if _sen_err:
    render_sim_error("Senate", _sen_err)
else:
    s1, s2, s3 = st.columns(3)
    s1.metric("P(D majority, ≥51)", f"{_sen_sim['p_d_majority']:.1%}")
    s2.metric("P(R control, ≥50 + VP)", f"{_sen_sim['p_r_control']:.1%}")
    s3.metric("Mean D seats", f"{_sen_sim['mean_d_seats']:.1f}")

    # Osborn's seat is tracked separately — the sim makes no caucus assumption,
    # so these only render when a Nebraska-style independent is in the field.
    if _sen_sim.get("p_osborn_wins", 0) > 0:
        st.caption(
            f"Nebraska independent — P(wins): {_sen_sim['p_osborn_wins']:.1%} · "
            f"P(his seat is the pivot): {_sen_sim['p_osborn_pivotal']:.1%}. "
            f"The simulation does not assume who he would caucus with."
        )

    st.plotly_chart(plot_seat_distribution(_sen_sim["seat_distribution"], 51, "Senate"),
                    width="stretch")

# --- House map ---
st.divider()
st.subheader("2026 House map — Tier 2 (all districts)")
hdf = load_house_df()
_h_env, _h_env_source = national_environment_margin()
st.caption(
    f"National environment: **D{_h_env:+.1f}** margin ({_h_env_source}), applied to "
    f"every district's lean baseline. Polled districts feel it at 20% weight — "
    f"their polls already carry 2026."
)
_h_polled = int((hdf["Basis"] == "polls").sum())
_h_d = int((hdf["Margin"] > 0).sum())
_h_r = len(hdf) - _h_d
_h_tilt = int((hdf["Margin"].abs() < TILT_MARGIN_THRESHOLD).sum())
st.caption(
    f"{len(hdf)} of 435 districts modeled · {_h_polled} poll-backed, "
    f"{len(hdf) - _h_polled} lean-only (shown at reduced opacity) · "
    f"D leads {_h_d} · R leads {_h_r} — every district is colored for its "
    f"vote-share leader, including the {_h_tilt} inside {TILT_MARGIN_THRESHOLD:g}pt "
    f"(the Tilt bands); nothing is left uncalled · point estimates, control "
    f"probability below · "
    f"⚠️ TX/NC/OH/FL lean and boundaries are both pre-2025 redraw"
)
if os.path.exists(CD_GEOJSON_PATH):
    # Colored swatches, not the plain bullets this used to print: with the
    # neutral bin gone, the only thing distinguishing the closest districts
    # from the safest is the shade, so the legend has to show the shades.
    st.markdown(
        '<div style="font-size:0.8rem;opacity:0.75;margin-bottom:0.35rem">' +
        "".join(
            f'<span style="white-space:nowrap;margin-right:0.9rem">'
            f'<span style="color:{RATING_COLORS[r_name]};font-size:1.2em">●</span> '
            f'{r_name}</span>'
            for r_name in RATING_ORDER
        ) + "</div>",
        unsafe_allow_html=True,
    )
    st.plotly_chart(plot_house_map(hdf), width="stretch")
else:
    st.warning(
        "House map skipped — `data/cd119.geojson` not found. Download the 119th "
        "Congress district boundaries (e.g. Census cartographic boundary file "
        "`cb_2024_us_cd119_500k`, converted to GeoJSON with a `GEOID` property) "
        "and save as `data/cd119.geojson`. Table below still works."
    )
    st.dataframe(
        hdf[["Race", "Leader", "Leader %", "Challenger", "Challenger %",
             "Margin", "Rating", "Basis", "Flip"]],
        width="stretch", hide_index=True,
    )

# Flips are only detectable where house_nominees.csv names an incumbent (~83
# districts). Elsewhere the incumbent is unknown, so absence of ⚡ means
# "unknown", not "hold" — hence the caption rather than a bare flip list.
house_flips = hdf[hdf["Flip"] == "⚡"]
if len(house_flips):
    st.caption("Flip detection covers rostered districts only — elsewhere the "
               "incumbent is unknown, not held.")
    st.caption("Projected flips: " +
               " · ".join(house_flips["Race"] + " → " + house_flips["Leader"].str[0]))

# --- House Monte Carlo ---
st.divider()
st.subheader("House outlook — 1,000,000 simulated elections")
_hse_sim, _hse_err = load_sim_or_error(load_house_sim, "House")

if _hse_err:
    render_sim_error("House", _hse_err)
else:
    h1, h2, h3, h4 = st.columns(4)
    h1.metric(f"P(D majority, ≥{HOUSE_MAJORITY})", f"{_hse_sim['p_d_majority']:.1%}")
    h2.metric(f"P(R majority, ≥{HOUSE_MAJORITY})", f"{_hse_sim['p_r_majority']:.1%}")
    h3.metric("Mean D seats", f"{_hse_sim['mean_d_seats']:.1f}")
    h4.metric("90% range (D seats)",
              f"{_hse_sim['d_seats_p05']:.0f}–{_hse_sim['d_seats_p95']:.0f}")

    # 435 is odd, so exactly one party clears 218 — P(R) is the exact complement
    # of P(D), not a separately estimated quantity. No tie, no tiebreaker.
    st.caption("435 seats is odd — one party always clears 218, so P(R) is the exact "
               "complement of P(D). There is no tie case and no tiebreaker.")

    st.warning(
        f"**These probabilities rest on unvalidated error estimates.** The Senate σ "
        f"values trace to published pollster-accuracy work; the House σ values in "
        f"`calibration.py` are reasoned by analogy and have not been backtested. "
        f"{_hse_sim['n_lean_only']} of {_hse_sim['n_lean_only'] + _hse_sim['n_polled']} "
        f"districts ride on the lean-only σ, itself applied to 2022-vintage lean "
        f"(95 districts on boundaries that no longer exist). "
        f"{_house_env_note()} "
        f"{load_house_sensitivity()} "
        f"Treat these figures as order-of-magnitude."
    )

    st.plotly_chart(
        plot_seat_distribution(_hse_sim["seat_distribution"], HOUSE_MAJORITY, "House"),
        width="stretch")

    # The map can show a rating but not a probability, so the competitive
    # districts only become readable as a table. Mirrors the CLI's COMPETITIVE
    # DISTRICTS block.
    _competitive = pd.DataFrame([
        {
            "Race":     r["race"],
            "P(D win)": round(r["dem_win_prob"], 4),
            "Margin":   round(r["margin"], 1),
            "Basis":    "polls" if r["has_polls"] else "lean only",
            "Democrat": r["dem_name"],
            "Republican": r["rep_name"],
        }
        for r in _hse_sim["races"] if 0.05 < r["dem_win_prob"] < 0.95
    ]).sort_values("P(D win)", ascending=False)

    st.caption(f"**{len(_competitive)} competitive districts** — P(D win) between 5% and 95%. "
               f"The remaining {len(_hse_sim['races']) - len(_competitive)} sit outside that "
               f"band and are not listed.")
    st.dataframe(
        _competitive,
        width="stretch", hide_index=True,
        column_config={"P(D win)": st.column_config.ProgressColumn(
            "P(D win)", min_value=0.0, max_value=1.0, format="%.1f%%")},
    )

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

# .copy() before adding the simulation column: df is the cached object itself,
# and mutating it would poison the cache for every later reader.
_table = df[["State", "Leader", "Leader %", "Challenger", "Challenger %",
             "Margin", "Basis", "Flip"]].copy()

_fmt = {"Margin": lambda v: f"{v:+.1f}"}

# The probability column is additive: when the Senate simulation is unavailable
# the table still renders in full, just without it. Point estimates do not
# depend on the sim, so there is no reason to withhold them.
if _sen_sim is not None:
    # Sign conventions already agree: Margin is positive when the non-Republican
    # leads, and the sim reports the same side's win probability (the D
    # candidate, or the independent where no D is running). A state the sim
    # skipped maps to NaN and renders blank rather than as a spurious 0%.
    _sen_win = {r["state"]: r["dem_side_win_prob"] for r in _sen_sim["races"]}
    _table.insert(6, "P(D-side win)", _table["State"].map(_sen_win))
    _fmt["P(D-side win)"] = lambda v: "" if pd.isna(v) else f"{v:.1%}"
    st.caption("P(D-side win) is the Monte Carlo win probability for the "
               "non-Republican candidate — the same side the Margin column is signed for.")

styled = (_table
          .style
          .map(color_margin, subset=["Margin"])
          .format(_fmt))
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