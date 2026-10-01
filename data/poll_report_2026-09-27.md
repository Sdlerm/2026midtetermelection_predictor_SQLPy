# Daily Poll Report — 2026-09-27 (Sun)

Window checked: polls released ~9/25–9/27. Note: the FiveThirtyEight/ABC CSV feed that these files were originally built from is now dead (the endpoint redirects to the ABC News homepage), so CSV refreshes are limited to reconciling the local pull files. The CSVs already held everything through the evening of 9/26.

## 1. Senate (2026)
No genuinely new individual Senate polls surfaced in the last 24h beyond what the file already captured. The most recent captured releases (created 9/26) are Trafalgar–SC, InsiderAdvantage–IA, InsiderAdvantage–ME, and Trafalgar–AK.

The NYT/Siena batch getting coverage over the weekend (ME, MI, NH) was fielded 9/15–9/22 and is **already in senate.csv** (created 9/24). Toplines for reference:
- **ME:** Collins (R) 49 – Jackson (D) 46 → Collins +3
- **NH:** Pappas (D) 50 – Sununu (R) 45 → Pappas +5
- **MI:** Rogers (R) 44.5 – (Dem) close; ~"NONE"/undecided ~5

Picture vs. averages: no meaningful shift. Maine stays a low-single-digit Collins edge; NH leans D mid-single digits; MI a toss-up leaning slightly R on this poll. Nothing that moves a race across a threshold.

## 2. Presidential approval
Trump approval remains at/near term lows. Aggregator average ~35% approve / ~62% disapprove (net ≈ -27), described as an all-time low for the term. Notable recent individual reads now reflected in the refreshed file:
- **Verasight (Strength in Numbers):** 33 approve / 63 disapprove — worst in 16 waves since May 2025
- **CNN/SSRS, Marist, Echelon, Emerson, RMG, Impact/NRI** — all in the low-to-mid 30s approve
- **Ipsos/Reuters:** 32 / 66

Movement: continued erosion tied to the early-September funding fight; disapproval has widened, not reversed.

## 3. House (2026)
No new district-level polls in the last 24h beyond what's captured (e.g., Remington MO-02 fielded 9/14–9/15, roughly tied 47D–46R). Generic ballot is the live story:
- Aggregate average ~**D+7.4** (≈49.3–41.9); several individual surveys nearer D+8.
- **Emerson (Sept national):** Democrats +11
- **NPR/PBS/Marist:** D 53 – R 41 (RV) → D+12

A D+7 to D+8 environment is historically in House "wave" territory (2018 was ~D+8.6).

## CSV updates
- **president_approval_polls.csv** — refreshed to current pull (now through 9/26; 1,266 poll-question rows). Adds Verasight, CNN/SSRS, Marist, Echelon, Emerson, RMG, Impact/NRI approval reads that were missing. Backup: `president_approval_polls_backup_2026-09-27.csv`.
- **senate.csv** — already current through 9/26 (no dead-feed rows to add today). Backup: `senate_backup_2026-09-27.csv`.
- **house.csv** — already current through 9/25 (no new district polls today). Backup: `house_backup_2026-09-27.csv`.

## Flags for the forecasting project
- **Generic ballot D+7–8** is the single most model-relevant number this cycle for the planned House module — well into wave territory; worth wiring the generic-ballot average into the national swing prior once House races are added.
- **Approval near -27 net** is a useful correlated national fundamental; if the Monte Carlo uses a national error/lean term, the approval trend supports a Democratic-leaning national environment prior.
- **Data-source risk:** the 538/ABC CSV pipeline is defunct. Before the next scheduled run is reliable, the project needs a new ingestion source (e.g., RealClearPolling, DDHQ, Silver Bulletin, or scraping individual pollster releases) mapped into the existing 538 schema. Flagging this as the top maintenance item.
