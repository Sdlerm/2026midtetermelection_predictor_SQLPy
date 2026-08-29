"""Put the repo root on sys.path so tests import the modules directly.

Every module in this project lives at the top level and imports its siblings by
bare name (`from house_model import ...`). There is no package to install, so
pytest's rootdir insertion is what makes those imports resolve.
"""
import os
import sys

import matplotlib

# Agg before anything imports pyplot. charts.py draws real figures in these
# tests and the CI runner has no display; without this, importing charts picks
# an interactive backend and the render tests hang rather than fail.
matplotlib.use("Agg")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
