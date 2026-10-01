# Daily Poll Report — 2026-09-23

Scanned aggregators and pollster releases for polls surfacing in the last ~24–48h. **Two genuinely new releases** (both dated Sep 23): the national Verasight/Strength In Numbers tracker and a Marist Texas poll. Everything else circulating (UMass Aug 21–26, Marquette Sep 2–9, Quinnipiac 9/10, and the Race-to-WH / DDHQ figures) is either older or model output, not new polling.

## 1. Senate (2026)

**Marist — Texas** (RV, n=1,139, field Sep 17–20, MOE ±4.4)
- Talarico (D) **50** — Paxton (R) **44** → **D+6**
- Talarico leads independents 55%. Notable: a Democrat ahead outside the MOE in a Texas Senate race.

Context: recent TX Senate polling had the race roughly even-to-single-digits (YouGov 8/27–9/4 put Talarico at 48). Marist is on the stronger end for Talarico, but it's an RV (not LV) sample. No other new individual state Senate polls in the window.

## 2. Presidential approval

**Verasight / Strength In Numbers — national** (adults, n=1,528, field Sep 16–21, MOE ±2.6)
- Trump **33% approve / 63% disapprove** (net **−30**) — a new low for this 16-wave tracker, down from −24 in August.
- Driver is GOP softening: net approval among Republicans/leaners fell +54 → +43 (11-pt drop). Approval on prices hit a record −54.

Context: this is well below the ~37–40 approval average and reinforces the downward move also seen in Economist/YouGov (already in file, 35% on 9/22). Two independent trackers both hitting lows in the same week is a real signal, not noise.

## 3. House / generic ballot

**Verasight — national generic ballot:** D **50** / R **42** among RV (**D+8**; D+10 among likely voters, D+12 among "definitely vote"). GOP's 42% is a series low.

**Marist — Texas generic ballot:** D **49** / R **46** (**D+3**), a big shift from R+7 at the same point in the 2024 cycle.

Context: consistent with the ~D+7–8 national average; Verasight's LV screen (D+10) is on the high end.

## Flags for the forecasting model

- **Texas Senate** is the item worth attention: Marist has it D+6 (RV). If real, TX becomes more competitive than the model's current lean likely assumes. Worth watching whether other pollsters corroborate before shifting the state prior — single RV poll, so treat as one data point, not a trend.
- **National environment** continues drifting Democratic: approval at/near tracker lows and generic ballot steady at ~D+8. If the model uses a national-environment / generic-ballot input as a uniform swing, this nudges it slightly further D.
- When House races come online: the Verasight LV-vs-RV gap (D+8 RV → D+10 LV) is a concrete example of a likely-voter turnout adjustment you may want to parameterize rather than ignore.

## Files updated (in /data/)
- `senate.csv` — +2 rows (Marist TX Senate)
- `president.csv` and `president_approval_polls.csv` — +1 row (Verasight national approval)
- `house.csv` — +4 rows (Verasight + Marist TX generic ballot)

Backups: `president_backup_pretask_2026-09-23.csv`. Dates written in the file's native m/d/yy format; numeric_grade set to 3.0 as a placeholder (both are unrated/newer pollsters in this dataset).
