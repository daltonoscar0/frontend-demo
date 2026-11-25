"""Shared setup.

`CAESURA_MODEL` points System B at the weights. The Makefile exports it; this
is here so that a bare `pytest` in a checkout with a sibling caesura also works,
and so the model tests skip cleanly rather than reach for the Hub when it is not
there. The suite must run offline.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_weights = ROOT.parent / "caesura" / "outputs" / "breaks"
if _weights.exists():
    os.environ.setdefault("CAESURA_MODEL", str(_weights))
