The Approach
The weighted poll average already gives you a solid base. Economics doesn't replace it — it applies a climate adjustment: a small shift up or down based on whether the national environment favors the incumbent party.
The logic political scientists use:

High unemployment, high inflation, low consumer sentiment, low GDP → bad for the party holding the White House (Republicans in 2026)
So in a bad economic climate, D candidates get a small bump, R candidates get a small penalty — and vice versa

The formula:
projected = poll_avg + (party_direction × climate_score × weight)
Where:
party_direction = +1 for D, -1 for R (since Rs hold the White House)
climate_score = a single number derived from all 6 indicators, normalized
weight = how much you trust economics vs polls (start at 0.3 — tunable)

The Normalization
Each indicator needs to be converted into a -1 to +1 score where +1 = good for Democrats:
IndicatorLogicUNEMPLOYMENTHigher → worse economy → +DCPI_YOYHigher inflation → +DCONSUMER_SENTIMENTLower sentiment → +DREAL_DISPOSABLE_INCLower income → +DGDP_GROWTHLower growth → +DFED_FUNDS_RATEHigher rates → +D
We normalize each against its historical range, then average them into one climate_score.

**_****`Projection Data Flow:`_****
(1) Poll Averages (from senate.csv downloaded from NYT)
(2) State Lean Factor: Poll Average --> Blended = (poll_avg)*(LEAN_ALPHA) + (state_lean)*(1-LEAN_ALPHA)
(3) Economic Climate Adjustment: Blended +/- Calculated Overall Climate Adj (+/- pp)
(4) Presidential Approval Rating (R-Trump incumbent: lower than average (historically) approval (which is the case -- Trump is poisonous unhinged brainless diarrhea, approval is now ~39%) hurts GOP)
(5) (Blended +/- Climate_Adjustment) + (Approval_average - 50)/50
(6) Monte Carlo Simulations
