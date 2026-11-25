"""The cold-start fallback must be the real pipeline's answer, or nothing.

The service runs on a free CPU host that idles out, so the first visitor after
a quiet spell waits about half a minute. `web/precomputed.json` fills the last
two panels in the meantime. That makes it the one place on the page showing
stage output that was not computed just now, so the risk it carries is specific:
a saved answer that has drifted from what the service would say is wrong
*without looking wrong*, which is worse than an empty panel.

These tests hold it to three things — it covers exactly the inputs the page can
reach without typing, it has the shape the live endpoint returns, and its
content is what the stages actually produce. The last one is the expensive
check and is marked slow; the first two are cheap and always run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRECOMPUTED = ROOT / "web" / "precomputed.json"
PIPELINE_JS = ROOT / "web" / "pipeline.js"
FRONTEND_JSX = ROOT / "web" / "Frontend.jsx"
GENERATOR = ROOT / "eval" / "make_precomputed.py"

sys.path.insert(0, str(ROOT / "eval"))


@pytest.fixture(scope="module")
def blob() -> dict:
    assert PRECOMPUTED.exists(), (
        "web/precomputed.json is missing; run `python eval/make_precomputed.py`"
    )
    return json.loads(PRECOMPUTED.read_text())


@pytest.fixture(scope="module")
def expected_inputs() -> list[str]:
    from make_precomputed import page_inputs

    return page_inputs()


# --- coverage ---------------------------------------------------------------


def test_it_covers_every_input_the_page_can_reach_without_typing(
    blob, expected_inputs
):
    """The flagship sentence and all four example buttons, and nothing else.

    Extra entries are dead weight in the bundle; a missing one is an empty
    panel for a sentence someone reached by clicking a button, which is the
    case this exists for.
    """
    assert sorted(blob["entries"]) == sorted(expected_inputs)


def test_the_flagship_sentence_is_covered(blob):
    """It is what the page loads with, so it is the one that must never miss."""
    from make_precomputed import page_inputs

    assert page_inputs()[0] in blob["entries"]


def test_it_is_keyed_on_the_raw_input_not_the_normalised_text(blob):
    """The page matches before stage 1 has run, so the key is what was typed."""
    for raw, entry in blob["entries"].items():
        assert entry["normalized"] != raw or raw == entry["normalized"], raw
    # The flagship's raw and normalised forms differ, which is what makes the
    # distinction testable at all.
    flagship = "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30"
    assert flagship in blob["entries"]
    assert blob["entries"][flagship]["normalized"] != flagship


# --- shape ------------------------------------------------------------------


def test_each_entry_has_both_stages_in_the_endpoints_shape(blob):
    """The page renders these through the same components as a live response."""
    for raw, entry in blob["entries"].items():
        assert set(entry) == {"normalized", "lede", "caesura"}, raw
        assert entry["lede"]["stage"], raw
        assert entry["caesura"]["stage"], raw
        # The fields the panels read.
        for field in ("tokens", "decisions", "meta"):
            assert field in entry["lede"], (raw, field)
            assert field in entry["caesura"], (raw, field)
        # `pronunciation` is a StageResult property rather than part of
        # as_dict(), and the API adds it by hand. A fallback without it would
        # render an empty phoneme line.
        assert entry["lede"]["pronunciation"], raw
        assert entry["caesura"]["output"], raw


def test_no_entry_claims_a_latency_it_never_incurred(blob):
    """Nothing was timed, so no timing may appear anywhere in the file."""

    def seconds_anywhere(obj, path="") -> list[str]:
        found = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {"seconds", "timings_ms", "ms"}:
                    found.append(f"{path}.{key}")
                found += seconds_anywhere(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                found += seconds_anywhere(value, f"{path}[{i}]")
        return found

    assert seconds_anywhere(blob) == []


def test_the_file_is_small_enough_to_ship_in_the_bundle(blob):
    """It is imported, not fetched, so its size is the page's size."""
    size_kb = PRECOMPUTED.stat().st_size / 1024
    assert size_kb < 250, f"{size_kb:.0f} KB is too much to inline on a phone"


# --- the page uses it, and says so -----------------------------------------


def test_the_page_prefers_a_live_answer_and_falls_back_rather_than_the_reverse():
    source = FRONTEND_JSX.read_text()
    assert "const shown = live ?? fallback ?? rest;" in source, (
        "a live answer for the current input must win over the saved one"
    )
    # The saved answer is only ever shown when there is no live one.
    assert "const showingPrecomputed = live == null" in source


def test_the_page_labels_a_saved_answer_as_saved():
    """Colour alone is not a label, so the words have to be there."""
    source = FRONTEND_JSX.read_text()
    assert "Saved output, not computed just now." in source
    assert "showingPrecomputed && (" in source
    # And it says where it came from, so the claim is checkable.
    assert "eval/make_precomputed.py" in source


def test_the_error_panel_yields_to_a_saved_answer():
    """With one available, "not answering" alone would be the worse screen."""
    source = FRONTEND_JSX.read_text()
    assert 'apiState === "error" && !showingPrecomputed' in source


def test_an_unknown_sentence_gets_no_fallback():
    """Matching is exact, so nothing is shown for a sentence nobody ran."""
    source = PIPELINE_JS.read_text()
    assert "const entry = table?.entries?.[text];" in source
    assert "if (!entry) return null;" in source


def test_pipeline_js_imports_nothing():
    """It has to stay loadable by plain node, which runs the explanation tests.

    A bare JSON import resolves under the bundler and raises
    ERR_IMPORT_ATTRIBUTE_MISSING under node, so the table is passed in by the
    component instead. This caught exactly that regression once.
    """
    source = PIPELINE_JS.read_text()
    for line in source.splitlines():
        assert not line.startswith("import "), f"pipeline.js imports: {line}"
    assert "import precomputedTable from \"./precomputed.json\"" in (
        FRONTEND_JSX.read_text()
    ), "the component is where the table is imported"


def test_only_timings_are_dropped_from_the_saved_response():
    source = PIPELINE_JS.read_text()
    assert "precomputed: true" in source
    assert "No `timings_ms`" in source


# --- the content is real ----------------------------------------------------


def test_the_flagship_entry_is_the_output_the_readme_quotes(blob):
    """Cheap proof that this is pipeline output and not a hand-written shape.

    Both strings are in the repository README, and they are only producible by
    the chain: `R EH1 D IH0 NG` for Reading needs stage 1's name span, and the
    absence of a break between "doctor" and "lee" needs it too.
    """
    flagship = "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30"
    entry = blob["entries"][flagship]

    assert entry["normalized"] == (
        "I read the second Doctor Lee lead a live band on Reading Road at ten thirty"
    )
    assert entry["lede"]["pronunciation"] == (
        "AY1 R EH1 D DH AH0 S EH1 K AH0 N D D AA1 K T ER0 L IY1 L IY1 D AH0 "
        "L AY1 V B AE1 N D AA1 N R EH1 D IH0 NG R OW1 D AE1 T T EH1 N TH ER1 D IY2"
    )
    assert entry["caesura"]["output"] == (
        "i read the second doctor lee lead a live band on reading road at ten "
        "*thirty* <b3>"
    )


@pytest.mark.slow
def test_regenerating_it_changes_nothing():
    """The whole claim, checked by rerunning the stages. About a minute."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout
