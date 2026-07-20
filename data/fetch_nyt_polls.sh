#!/usr/bin/env bash
#
# fetch_nyt_polls.sh
# -------------------
# Downloads the latest NYT poll CSVs straight into this data/ folder.
# Run it by hand any time:   bash fetch_nyt_polls.sh
# Or schedule it (see notes at the bottom).
#
# After it runs, point senate_ingest.py at these files as usual.

# Always save into the same folder this script lives in (your data/ folder),
# no matter where you run it from.
DIR="$(cd "$(dirname "$0")" && pwd)"

# --- The files to grab: "URL  ->  local filename" ---
# senate.csv is the one you confirmed. The other two are my best guess at
# NYT's naming — open them in a browser once to confirm they exist, and
# fix the left-hand URL if NYT calls them something else.
BASE="https://www.nytimes.com/newsgraphics/polls"

# senate.csv and house.csv are confirmed working.
curl -fsSL "$BASE/senate.csv" -o "$DIR/nyt_senate.csv" && echo "OK  nyt_senate.csv"
curl -fsSL "$BASE/house.csv"  -o "$DIR/nyt_house.csv"  && echo "OK  nyt_house.csv"

# Approval file: NYT's exact name is unknown, so try the likely candidates
# in order and keep the first one that actually returns data (HTTP 200).
APPROVAL_NAMES="president_approval_polls.csv approval.csv president-approval.csv president_approval.csv presidential-approval.csv"
got_approval=""
for name in $APPROVAL_NAMES; do
  if curl -fsSL "$BASE/$name" -o "$DIR/nyt_president_approval.csv" 2>/dev/null; then
    echo "OK  nyt_president_approval.csv  (from $name)"
    got_approval="yes"
    break
  fi
done
if [ -z "$got_approval" ]; then
  echo "SKIP nyt_president_approval.csv  (none of the guessed names worked — find the real one and add it to APPROVAL_NAMES)"
fi

echo "Done. Files saved in: $DIR"

# ----------------------------------------------------------------------
# Flags explained:
#   -f  fail (don't save) if the server returns an error like 404/403
#   -s  silent (no progress bar clutter)
#   -S  but still show an error message if it fails
#   -L  follow redirects if the URL moves
#   -o  save to this filename
#
# NOTE: I saved these as nyt_*.csv (NOT senate.csv) on purpose, so the
# download can't overwrite/clobber your existing ingested files by accident.
# Let senate_ingest.py read the nyt_* copies and merge into your real ones.
#
# To run it automatically every morning at 7:00am, open Terminal, type:
#     crontab -e
# then add this one line (adjust the path if your repo lives elsewhere):
#     0 7 * * * /bin/bash /Users/minimaclerman/election_predictor_clean/data/fetch_nyt_polls.sh
# Save and close. That "0 7 * * *" means "minute 0, hour 7, every day."
# ----------------------------------------------------------------------
