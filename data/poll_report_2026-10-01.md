# Daily Poll Report — 2026-10-01 (Thu)

Window checked: polls released ~9/29–10/1. Note: the FiveThirtyEight/ABC CSV feed these files were built from remains dead, so refreshes are done by hand-mapping individual pollster releases into the 538 schema. After cross-checking RealClearPolling (Senate), DecisionDeskHQ (generic ballot) and news aggregators, the local files were already current through today's releases — the only change this run was a data-recovery fix (see CSV updates).

## 1. Senate (2026)
No genuinely new individual Senate polls surfaced in the last 24–48h beyond what senate.csv already captured (verified against RCP's latest-polls/senate list). The recent captured batch:

- **OH (special):** Brown (D) leading across three reads — Suffolk/USA Today ~Brown +3–4 (47–43/44), Big Data Brown +3 (46–43), Marist Brown +8 (51–43).
- **MI:** El-Sayed (D) modal — Fox/Beacon-Shaw El-Sayed +1 (50–49), Marist El-Sayed +7 (51–44), Cygnal/Beacon El-Sayed +5 (45–40), GBAO (D internal) El-Sayed +4 (48–44).
- **ME:** co/efficient Collins (R) +2 (48–46).
- **IA:** Quantus Hinson (R) +1 (47–46).
- **MN:** InsiderAdvantage Flanagan (D) +1 (46–45).
- **NC:** Big Data Cooper (D) +12 (51–39); Fabrizio Ward/Impact Cooper +11 (53–42).

Picture vs. averages: the one race drifting is **Ohio's special**, where Brown now leads by a consistent 3–8 across pollsters — if the model still treats OH as lean-R, that's a meaningful shift toward a D edge. **Michigan** has firmed from toss-up to a slight-to-clear El-Sayed lead. ME (narrow Collins), IA (narrow Hinson) and MN (narrow Flanagan) remain tight toss-ups; NC stays a solid Cooper lead. Nothing else crossed a threshold.

## 2. Presidential approval
No brand-new (Oct 1) approval poll was verifiable, but 7 reads from 9/28–9/30 that a prior run had failed to save were recovered into the file this run (see CSV updates). Toplines of the recovered batch:

- **Quinnipiac** (9/24–27): 33 approve / 60 disapprove — described as a term low.
- **YouGov/Economist** (9/25–28): 37/62 (LV).
- **Angus Reid** (9/19–25): 34/61.
- **The Argument/Verasight** (9/16–22): 37/62.
- **Morning Consult** (9/25–27): 42/55.5.
- **HarrisX/Harvard CAPS** (9/26–28): 42/55.
- **Clarity Campaign Labs** (9/11–16): 41/58.

Movement: none meaningful — the newly-added reads cluster 33–43 approve / 55–62 disapprove, keeping the aggregate near term lows (net roughly the mid-20s negative). The recurring pattern is live-caller/probability-panel pollsters (Quinnipiac, Angus Reid) at the bleak end and online panels (Harris, Morning Consult) a bit higher.

## 3. House (2026)
Generic ballot remains the live story; no new district polls in the last 24–48h beyond captured (recent district reads: PPP TX-09, Bullfinch CA-06, Impact MT-01, DCCC WI-01 & OH-09, Hart OH-15, PPP NY-23, Braun VT-01).

- Aggregate (DDHQ) ~**D+7.9** (48.8–40.9).
- **YouGov/Economist** (9/25–28): D 53 – R 38 (RV) → **D+15**; D 45 – R 37 (LV) → D+8 — already in house.csv.
- HarrisX/Harvard, Quinnipiac, Morning Consult, Angus Reid, Clarity generic reads all captured.

A DDHQ entry for "Meridian Research" with field dates Oct 1–3 (D+2) was disregarded — future-dated relative to today and unverifiable.

A D+8 generic environment stays in historical "wave" territory.

## CSV updates
- **president_approval_polls.csv** — recovered **11 question-rows (7 polls)** that an earlier run on 2026-10-01 had accidentally written into `poll_report_2026-10-01.md` instead of this file; merged by question_id. 1,267 → **1,278 rows**. Backup: `president_approval_polls_backup_2026-10-01_scheduled.csv`.
- **senate.csv** — already current through 10/1 (latest captured created 10/1 04:46); verified against RCP, no new rows to add. Unchanged.
- **house.csv** — already current through 9/30; verified against DDHQ generic ballot, no new rows to add. Unchanged.

## Data-integrity flag
`poll_report_2026-10-01.md` had been overwritten by an earlier run with the full president-approval CSV dump (1,276 lines of CSV, no report). That content has now been recovered into president_approval_polls.csv and this file rewritten as the proper report. **Recommendation:** the approval-update script appears to write to the report path by mistake — have it write to a temp file, validate the header line equals the president-approval schema, and only then move it into place. This would prevent silent data loss (the 7 reads were stranded for ~2h and would have been lost on the next overwrite).

## Flags for the forecasting project
- **Ohio special → Brown +3–8:** the single most model-relevant shift this run. If the Senate model's OH lean is still R-tilted, update it; the race now reads as a D edge.
- **Michigan → El-Sayed:** moved from toss-up to a modal D lead; worth re-checking the state's prior.
- **Generic ballot ~D+8:** the key input for the planned House module — wave territory; wire the DDHQ/aggregate generic-ballot average into the national swing prior when House races are added.
- **Approval net ≈ -25:** stable correlated national fundamental supporting a D-leaning environment prior.
- **Pipeline fragility:** today's wrong-file write (recovered here) plus the dead 538 feed make ingestion the top maintenance item. A validated-write guard and a stable replacement source (RCP / DDHQ / Silver Bulletin / pollster scraping) mapped to the 538 schema remain the priority.
