"""The browser build and the native binary must give identical output.

This is the test that makes "the demo runs the system that was evaluated" a
fact rather than a hope. Stage 1 is compiled twice -- by g++ for the evaluation
harness and by em++ for the page -- and two compilers, two target
architectures and a different standard library are three chances to disagree.

The comparison is over the whole StageResult document and not only the
normalised string, so a divergence in a cost, a tagging or a losing alternative
fails it too. Both sides print the document from the same C++ function, so the
expected result is byte equality, not equality-up-to-formatting.

The sets are sayso's own: the shard it was developed against, a disjoint
held-out shard, a different shard fetched after every weight was fixed, and the
306 hand-written conversational cases. `--quick` in the Makefile is not offered
deliberately: a sample would hide exactly the rare construction where two
compilers differ.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAYSO = ROOT.parent / "sayso"
BINARY = SAYSO / "sayso"
WASM_RUNNER = ROOT / "wasm" / "parity.mjs"
WASM_BUILD = ROOT / "wasm" / "dist" / "sayso.wasm"

# Sets that are committed, so they are present in every checkout and in CI.
COMMITTED_SETS = [
    ("phone cases", SAYSO / "tests" / "data" / "phone_cases.tsv", 1),
    ("normalisation hard cases", ROOT / "eval" / "data" / "normalization_cases.tsv", 0),
]

# Slices of the Google text normalisation corpus. sayso gitignores
# `eval/data/*.tsv` because that corpus is not ours to redistribute, so these
# exist only where someone has run sayso's own `convert_google_tn.py`. They are
# the bulk of the 20,616 documents the README claims, and they are absent in CI
# by design rather than by accident -- which is a distinction the count guard
# below has to make, or it fails every run on a machine that is behaving.
CORPUS_SETS = [
    ("shard 00099, developed against", SAYSO / "eval" / "data" / "google_tn_slice.tsv", 1),
    ("shard 00099, held out", SAYSO / "eval" / "data" / "heldout_slice.tsv", 1),
    ("shard 00098, never seen", SAYSO / "eval" / "data" / "shard98_slice.tsv", 1),
]

SETS = COMMITTED_SETS[:1] + CORPUS_SETS + COMMITTED_SETS[1:]

pytestmark = pytest.mark.skipif(
    not (BINARY.exists() and WASM_BUILD.exists()),
    reason="needs both builds: `make sayso` in the sayso checkout and `make wasm` here",
)


def inputs_from(path: Path, column: int) -> List[str]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) <= column:
            continue
        text = fields[column].strip()
        if text:
            rows.append(text)
    return rows


def native(lines: List[str], style: str) -> str:
    return subprocess.run(
        [str(BINARY), "normalize", "--style", style, "--json"],
        input="\n".join(lines) + "\n", capture_output=True, text=True, check=True,
    ).stdout


def wasm(lines: List[str], style: str) -> str:
    proc = subprocess.run(
        ["node", str(WASM_RUNNER), "--style", style],
        input="\n".join(lines) + "\n", capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"wasm runner failed: {proc.stderr.strip()[:2000]}")
    return proc.stdout


@pytest.mark.parametrize("style", ["plain", "phone"])
@pytest.mark.parametrize("name,path,column", SETS, ids=[s[0] for s in SETS])
def test_identical_documents(name, path, column, style):
    if not path.exists():
        pytest.skip(f"{name} not present at {path}")
    lines = inputs_from(path, column)
    assert lines, f"{name} yielded no inputs"

    from_native = native(lines, style)
    from_wasm = wasm(lines, style)

    if from_native == from_wasm:
        return

    # Report the first divergence rather than a 30 MB diff.
    left = from_native.splitlines()
    right = from_wasm.splitlines()
    assert len(left) == len(right), (
        f"{name}: {len(left)} documents from the binary, {len(right)} from WASM"
    )
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            raise AssertionError(
                f"{name}, --style {style}, input {index + 1} of {len(left)}:\n"
                f"  input  {lines[index]!r}\n"
                f"  native {a[:600]}\n"
                f"  wasm   {b[:600]}"
            )


def _count(sets) -> int:
    return sum(len(inputs_from(p, c)) for _, p, c in sets if p.exists())


def test_the_committed_sets_still_yield_their_inputs():
    """A guard on the guard, for the part that is present everywhere.

    If a path moves and a set silently empties, `test_identical_documents`
    skips or compares nothing and passes vacuously. These two sets are in git,
    so they are present in CI too and a moved path fails here rather than going
    unnoticed until someone looks at the skip count.
    """
    for name, path, column in COMMITTED_SETS:
        assert path.exists(), f"{name} is committed but missing at {path}"
        assert inputs_from(path, column), f"{name} yielded no inputs"
    total = _count(COMMITTED_SETS)
    assert total > 350, f"only {total} inputs from the committed sets; check the paths"


def test_the_sets_together_are_large_enough_to_mean_something():
    """The README's claim: byte-identical on 20,616 documents.

    Needs the corpus slices, which are not redistributable and so are absent in
    CI. Absent, this skips and says what to run; present, the full count is
    required, so the claim stays tested wherever it can be.
    """
    missing = [name for name, path, _ in CORPUS_SETS if not path.exists()]
    if missing:
        pytest.skip(
            f"{len(missing)} corpus slice(s) absent ({', '.join(missing)}). They are "
            f"slices of the Google text normalisation corpus, which sayso gitignores "
            f"rather than redistribute; build them with sayso's "
            f"eval/convert_google_tn.py. The committed sets are still compared above."
        )
    total = _count(SETS)
    assert total > 10_000, f"only {total} parity inputs found; check the paths"
