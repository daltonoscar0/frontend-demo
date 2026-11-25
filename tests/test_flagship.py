"""End to end, on the sentence the whole thing was specified against.

    I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30

It exercises every stage: an ordinal, a title, a street suffix and a clock time
for stage 1; four heteronyms for stage 2, one of which is only decidable from
what stage 1 passed forward; and a sixteen-word sentence with no punctuation for
stage 3.

These assertions are the real outputs, not the desired ones. Stage 3 misses two
breaks it should place, and that is asserted here as it stands rather than
written down as a wish, because a test that encodes a hope fails every day and
teaches nothing. The reason it misses them is in the repository README and on
the page.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAYSO_BINARY = ROOT.parent / "sayso" / "sayso"
WASM_RUNNER = ROOT / "wasm" / "parity.mjs"

RAW = "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30"
NORMALISED = "I read the second Doctor Lee lead a live band on Reading Road at ten thirty"


# --------------------------------------------------------------------------
# Stage 1
# --------------------------------------------------------------------------


def sayso_json(text: str, style: str = "phone") -> dict:
    if not SAYSO_BINARY.exists():
        pytest.skip(f"no sayso binary at {SAYSO_BINARY}; run `make sayso` there")
    out = subprocess.run(
        [str(SAYSO_BINARY), "normalize", "--style", style, "--json"],
        input=text + "\n", capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def stage1() -> dict:
    return sayso_json(RAW)


def test_the_flagship_sentence_normalises(stage1):
    assert stage1["output"] == NORMALISED


@pytest.mark.parametrize(
    "surface,result,kind",
    [
        ("2nd", "second", "ordinal"),
        ("Dr. Lee", "Doctor Lee", "name"),
        ("Reading Rd", "Reading Road", "address"),
        ("10:30", "ten thirty", "time"),
    ],
)
def test_each_class_fires_once(stage1, surface, result, kind):
    """Four classes, and the two readings of an abbreviation in one sentence.

    "Dr." is a title and "Rd" is a street suffix, and nothing decides that but
    the cost of the whole path: ADDRESS can read "2nd Dr" as "second drive" more
    cheaply than ORDINAL reads "2nd", and loses anyway because the sentence
    still has to pay for "Lee".
    """
    decision = next(d for d in stage1["decisions"] if d["surface"] == surface)
    assert decision["result"] == result
    assert decision["kind"] == kind


def test_the_decisions_point_at_both_sides(stage1):
    for decision in stage1["decisions"]:
        a, b = decision["span"]
        assert stage1["input"][a:b] == decision["surface"]
        a, b = decision["out_span"]
        assert stage1["output"][a:b] == decision["result"]


def test_stage_one_hands_forward_what_the_later_stages_cannot_know(stage1):
    """The two fields that make this a chain rather than a stack."""
    meta = stage1["meta"]
    # "Doctor Lee" and "Reading Road", as offsets into the normalised text.
    assert meta["name_spans"] == [[18, 28], [49, 61]]
    assert [NORMALISED[a:b] for a, b in meta["name_spans"]] == [
        "Doctor Lee",
        "Reading Road",
    ]
    # And that "Reading" here is the town.
    assert meta["pron_hints"]["Reading"]["arpabet"] == "R EH1 D IH0 NG"


def test_the_wasm_build_agrees_with_the_binary_on_this_sentence():
    """The page runs the WASM build, so the page has to be what was tested."""
    proc = subprocess.run(
        ["node", str(WASM_RUNNER), "--style", "phone"],
        input=RAW + "\n", capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"wasm build not available: {proc.stderr.strip()[:200]}")
    native = subprocess.run(
        [str(SAYSO_BINARY), "normalize", "--style", "phone", "--json"],
        input=RAW + "\n", capture_output=True, text=True, check=True,
    ).stdout
    assert proc.stdout == native


# --------------------------------------------------------------------------
# Stage 2
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stage2(stage1) -> dict:
    lede_api = pytest.importorskip("lede.api")
    return lede_api.run(
        stage1["output"],
        pron_hints=stage1["meta"]["pron_hints"],
        name_spans=stage1["meta"]["name_spans"],
    )


def test_exactly_the_four_heteronyms_are_found(stage2):
    assert [d.token for d in stage2.decisions] == ["read", "lead", "live", "Reading"]


@pytest.mark.parametrize(
    "token,pron,label",
    [
        # "I read the second ..." is the past tense. The present tense is the
        # more common reading overall, so this is context and not frequency.
        ("read", "R EH1 D", "past tense verb"),
        # "Doctor Lee lead a live band": the verb, not the metal. The hardest of
        # the four, and the one the specification flagged.
        ("lead", "L IY1 D", "noun-verb"),
        # "a live band" is the adjective, not the verb.
        ("live", "L AY1 V", "adjective"),
        # The Berkshire town, not the gerund.
        ("Reading", "R EH1 D IH0 NG", "city"),
    ],
)
def test_each_heteronym_takes_the_right_reading(stage2, token, pron, label):
    decision = next(d for d in stage2.decisions if d.token == token)
    assert " ".join(decision.pron) == pron
    assert decision.label == label


def test_the_three_probe_decisions_are_confident(stage2):
    """Not an accuracy claim -- three sentences prove nothing -- but a
    regression guard: if a probability collapses toward a coin flip, the weights
    or the pooling changed."""
    for token in ("read", "lead", "live"):
        decision = next(d for d in stage2.decisions if d.token == token)
        assert decision.rule.startswith("probe:")
        chosen = next(
            a for a in decision.alternatives if a["wordid"] == decision.wordid
        )
        assert chosen["probability"] > 0.8, (token, chosen)


def test_reading_is_decided_by_the_stage_before_it(stage2):
    decision = next(d for d in stage2.decisions if d.token == "Reading")
    assert decision.rule == "hint"


def test_nothing_in_the_sentence_is_out_of_vocabulary(stage2):
    assert stage2.as_dict()["meta"]["oov"] == []


def test_every_word_gets_a_pronunciation(stage2):
    for token in stage2.as_dict()["tokens"]:
        if token["kind"] == "punct":
            continue
        assert token["pron"], token


# --------------------------------------------------------------------------
# Stage 3
# --------------------------------------------------------------------------


def model_available() -> bool:
    try:
        from caesura.model import model_available as available
    except ImportError:
        return False
    return available()


@pytest.fixture(scope="module")
def stage3(stage1) -> dict:
    caesura_api = pytest.importorskip("caesura.api")
    if not model_available():
        pytest.skip("caesura System B weights not present; set CAESURA_MODEL")
    return caesura_api.run(
        stage1["output"],
        system="both",
        name_spans=stage1["meta"]["name_spans"],
    )


def test_the_utterance_gets_its_final_break(stage3):
    assert stage3["meta"]["breaks"][-1] == "b3"
    assert stage3["output"].rstrip().endswith("<b3>")


def test_no_break_is_placed_inside_a_name(stage3):
    """The defect the chain exists to fix, from the other side.

    Without the name spans the sequence labeller puts <b3> between "doctor" and
    "lee" at 0.404, and the recall-oriented union inherits it. A pause inside a
    name is audibly broken however the rest of the sentence is phrased. Stage 1
    is what knows the name is there.
    """
    words = stage3["meta"]["words"]
    breaks = stage3["meta"]["breaks"]
    for first, second in (("doctor", "lee"), ("reading", "road")):
        i = words.index(first)
        assert words[i + 1] == second
        assert breaks[i] == "none", f"break between {first} and {second}"


def test_the_suppression_is_recorded_rather_than_silent(stage3):
    vetoed = stage3["meta"]["vetoed"]
    doctor = stage3["meta"]["words"].index("doctor")
    assert vetoed[str(doctor)] == "model:name_span"


def test_without_the_name_spans_the_name_is_split(stage1):
    """Pinned so that the fix cannot quietly stop being load-bearing."""
    caesura_api = pytest.importorskip("caesura.api")
    if not model_available():
        pytest.skip("caesura System B weights not present")
    plain = caesura_api.run(stage1["output"], system="both")
    words = plain["meta"]["words"]
    assert plain["meta"]["breaks"][words.index("doctor")] == "b3"


def test_the_two_internal_breaks_are_missed(stage3):
    """Asserted as it is, not as it should be.

    caesura's hand annotation wants <b2> after "lee" and after "band". Neither
    system places either. The rules cannot: the parse makes "the second doctor
    lee" the subject, four tokens, one below the five-token floor on
    b2_long_subject. Lowering that floor would produce the break on this one
    sentence, which is exactly why it has not been lowered -- the floor was
    chosen against a corpus and should move, if it moves, because the corpus
    says so.

    When that is genuinely fixed this test fails, which is the correct way for
    it to break.
    """
    words = stage3["meta"]["words"]
    breaks = stage3["meta"]["breaks"]
    assert breaks[words.index("lee")] == "none"
    assert breaks[words.index("band")] == "none"


def test_breaks_are_sensible_rather_than_merely_present(stage3):
    """Whatever it places, it must not place nonsense.

    Three properties that hold of any defensible reading of this sentence: it
    ends with a major break, it never breaks inside a name, and it never breaks
    between a determiner and its noun.
    """
    words = stage3["meta"]["words"]
    breaks = stage3["meta"]["breaks"]
    assert breaks[-1] == "b3"
    for i, word in enumerate(words[:-1]):
        if word in ("the", "a", "an"):
            assert breaks[i] == "none", f"break after {word!r}"


def test_emphasis_lands_on_a_content_word(stage3):
    emphasis = stage3["meta"]["emphasis"]
    assert emphasis, "the utterance should carry at least a nuclear accent"
    assert all(word not in ("the", "a", "an", "on", "at") for word in emphasis)


# --------------------------------------------------------------------------
# The whole chain, as the page runs it
# --------------------------------------------------------------------------


def test_the_chain_produces_all_three_outputs(stage1, stage2, stage3):
    """What the page shows, in one assertion: normalised text, a phoneme string
    and the text with breaks marked."""
    assert stage1["output"] == NORMALISED
    phones = stage2.pronunciation
    assert phones.startswith("AY1 R EH1 D")
    assert "R EH1 D IH0 NG R OW1 D" in phones, "Reading Road, as the town"
    assert "<b3>" in stage3["output"]
    assert stage3["meta"]["words"] == [
        w.lower() for w in NORMALISED.split()
    ], "stage 3 sees exactly stage 1's words"
