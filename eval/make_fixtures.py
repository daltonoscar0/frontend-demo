#!/usr/bin/env python3
"""Capture the flagship sentence's real stage output, for the JS tests.

The explanation functions in `web/pipeline.js` turn a decision into prose, and
testing them against hand-written shapes would only prove they handle shapes I
imagined. These fixtures are what the three stages actually produce, so a field
that changes name or disappears breaks the test rather than the page.

    python eval/make_fixtures.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "flagship.json"
SAYSO = ROOT.parent / "sayso" / "sayso"

RAW = "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30"


def main() -> int:
    sayso = json.loads(
        subprocess.run(
            [str(SAYSO), "normalize", "--style", "phone", "--json"],
            input=RAW + "\n", capture_output=True, text=True, check=True,
        ).stdout
    )

    from caesura.api import run as caesura_run
    from lede.api import run as lede_run

    lede = lede_run(
        sayso["output"],
        pron_hints=sayso["meta"]["pron_hints"],
        name_spans=sayso["meta"]["name_spans"],
    )
    caesura = caesura_run(
        sayso["output"], system="both", name_spans=sayso["meta"]["name_spans"]
    )

    blob = lede.as_dict()
    blob["pronunciation"] = lede.pronunciation

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"sayso": sayso, "lede": blob, "caesura": caesura}, indent=2) + "\n"
    )
    print(f"-> {OUT.relative_to(ROOT)}")
    print(f"   {len(sayso['decisions'])} spans, {len(blob['decisions'])} heteronyms, "
          f"{len(caesura['decisions'])} breaks, {len(caesura['meta']['vetoed'])} vetoed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
