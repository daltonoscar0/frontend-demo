"""Every number the page shows traces to a saved run, and the run reproduces.

Two separate claims, and the page depends on both:

  1. The files in `results/` are internally consistent -- the totals match the
     rows, the failure counts match the failure lists -- so a hand edit, or a
     half-finished run, is caught.
  2. Re-running each evaluation gives the same numbers, which is what each
     script's `--check` mode asserts.

The second is slow (about ten minutes of CPU forward passes), so it is marked
`slow` and excluded from the default run. The first is instant and always runs,
because the failure it catches -- a results file that no longer says what the
page quotes -- is the one that would put a wrong number in front of a reader.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
WEB_RESULTS = ROOT / "web" / "results"


def load(name: str) -> dict:
    path = RESULTS / name
    if not path.exists():
        pytest.skip(f"no saved run at {path}; run `make eval`")
    return json.loads(path.read_text())


# --------------------------------------------------------------------------
# Internal consistency
# --------------------------------------------------------------------------


def test_normalization_totals_match_its_rows():
    r = load("normalization.json")
    assert sum(b["n"] for b in r["per_class"].values()) == r["n"]
    assert r["correct"] + len(r["failures"]) == r["n"]
    assert r["accuracy"] == pytest.approx(r["correct"] / r["n"], abs=5e-5)
    for name, bucket in r["per_class"].items():
        assert bucket["accuracy"] == pytest.approx(
            bucket["correct"] / bucket["n"], abs=5e-5
        ), name


def test_every_normalization_failure_has_a_cause():
    """Thirty-two failures look like thirty-two problems. They are eleven.

    An uncategorised failure means the table on the page is a list of symptoms,
    which is the thing it exists not to be.
    """
    r = load("normalization.json")
    assert all(f["cause"] for f in r["failures"])
    assert "uncategorised" not in r["causes"], r["causes"].get("uncategorised")
    assert sum(info["n"] for info in r["causes"].values()) == len(r["failures"])


def test_normalization_was_scored_through_the_build_the_page_loads():
    r = load("normalization.json")
    assert r["runner"] == "wasm"
    assert r["style"] == "phone"


def test_heteronym_counts_are_consistent():
    r = load("heteronyms.json")
    assert len(r["failures"]) == r["errors"]
    assert r["micro"] == pytest.approx((r["n"] - r["errors"]) / r["n"], abs=5e-4)
    assert r["micro_ci"][0] <= r["micro"] <= r["micro_ci"][1]
    assert sum(i["n"] for i in r["failure_buckets"].values()) == r["errors"]
    assert len(r["per_word"]) == r["n_homographs"]
    below = r["words_below_100_percent"]
    assert all(r["per_word"][w]["accuracy"] < 1.0 for w in below)
    assert len(below) == sum(
        1 for info in r["per_word"].values() if info["accuracy"] < 1.0
    )


def test_the_heteronym_baselines_are_the_ones_the_probe_has_to_beat():
    r = load("heteronyms.json")
    baseline = r["pos_baseline"]
    assert baseline["available"], baseline
    # The page says the POS baseline, not the MLE one, is the number worth
    # beating. That only holds if it is in fact the harder of the two.
    assert baseline["mle_micro"] < baseline["pos_rule_micro"] < r["micro"]


def test_break_scores_are_well_formed():
    r = load("breaks.json")
    assert not r["limit"], "saved run was a --limit smoke run"
    for set_name, block in r["results"].items():
        assert block["n_boundaries"] > 0, set_name
        for system, scores in block["systems"].items():
            for label in ("b2", "b3", "any"):
                s = scores[label]
                for key in ("p", "r", "f1"):
                    assert 0.0 <= s[key] <= 100.0, (set_name, system, label, key)
                if s["p"] and s["r"]:
                    expected = 2 * s["p"] * s["r"] / (s["p"] + s["r"])
                    assert s["f1"] == pytest.approx(expected, abs=0.15), (
                        set_name, system, label,
                    )


def test_the_punctuation_baseline_covers_every_adversarial_sentence():
    """It is only meaningful if nothing was quietly dropped.

    The punctuated form has to tokenise to the same words as the hand-annotated
    one, or the boundaries are not comparable. A sentence where they differ is
    excluded, and an exclusion silently shrinking the baseline's denominator
    would flatter it.
    """
    r = load("breaks.json")
    punctuation = r["results"]["adversarial"]["systems"]["punctuation"]
    assert punctuation["excluded"] == []
    assert punctuation["n_boundaries"] == r["results"]["adversarial"]["n_boundaries"]


def test_the_punctuation_baseline_is_defined_only_where_it_means_something():
    """On LibriTTS-R and Switchboard the gold *is* the punctuation, so the
    baseline would score 100 and measure nothing. It must not appear there."""
    r = load("breaks.json")
    assert "punctuation" in r["results"]["adversarial"]["systems"]
    for set_name in ("libritts_test", "switchboard"):
        assert "punctuation" not in r["results"][set_name]["systems"], set_name


def test_the_finding_the_page_leads_with_still_holds():
    """Where written English licenses no comma, punctuation recovers no minor
    break at all, and the dependency rules recover most of them. That sentence
    is the case for the stage existing; if it stops being true the page has to
    stop saying it.
    """
    r = load("breaks.json")
    systems = r["results"]["adversarial"]["systems"]
    unpunctuated = ("reduced_relative", "long_subject", "non_punctuation_break")
    for bucket in unpunctuated:
        assert systems["punctuation"]["by_construction"][bucket]["b2"]["r"] == 0.0, bucket
        assert systems["rules"]["by_construction"][bucket]["b2"]["r"] > 0.0, bucket


def test_the_page_reads_the_same_files_the_evaluation_wrote():
    """`make sync-site` copies results/ into web/results/. A stale copy there
    would put a number on the page that no run produced."""
    for name in ("normalization.json", "heteronyms.json", "breaks.json"):
        source = RESULTS / name
        copied = WEB_RESULTS / name
        if not source.exists():
            pytest.skip("no saved runs yet")
        assert copied.exists(), f"{name} not synced into web/results/"
        assert json.loads(copied.read_text()) == json.loads(source.read_text()), (
            f"web/results/{name} is stale; run `make sync-site`"
        )


# --------------------------------------------------------------------------
# Reproduction
# --------------------------------------------------------------------------


def check(script: str) -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "eval" / script), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.slow
def test_normalization_reproduces():
    check("eval_normalization.py")


@pytest.mark.slow
def test_heteronyms_reproduce():
    check("eval_heteronyms.py")


@pytest.mark.slow
def test_breaks_reproduce():
    check("eval_breaks.py")
