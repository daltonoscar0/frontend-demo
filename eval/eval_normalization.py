#!/usr/bin/env python3
"""Score stage 1 on the hard-case set, through the build the page loads.

The system under test is `wasm/dist/sayso.wasm`, driven by node, not the native
binary. That is the whole point: the page claims to run the evaluated system, so
the evaluated system has to be the page's copy. `tests/test_wasm_parity.py`
separately pins the two to byte-identical output, so this is not a weaker test
than scoring the binary -- it is the same test with the demo's artefact in the
loop.

    python eval/eval_normalization.py                 # score, write results
    python eval/eval_normalization.py --check         # fail if numbers moved
    python eval/eval_normalization.py --native        # score the binary instead

Writes `results/normalization.json`: the per-class table, every failing row with
the correct answer, and the root cause each failure belongs to. The page reads
that file and renders it; no number on the page is typed by hand.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "data" / "normalization_cases.tsv"
RESULTS = ROOT / "results" / "normalization.json"
WASM_RUNNER = ROOT / "wasm" / "parity.mjs"
SAYSO_BINARY = ROOT.parent / "sayso" / "sayso"

#: The style the demo page uses: the reading a voice gives a form, not the one a
#: page does. "twenty-five ten", not "twenty-five dollars and ten cents".
STYLE = "phone"

# ---------------------------------------------------------------------------
# Root causes.
#
# Categorising failures is not decoration. Twenty-odd failing rows look like
# twenty-odd problems; they are mostly three, and knowing that is the difference
# between a to-do list and a design conversation. Each entry is matched by hand
# in `classify_failure` below, against the row rather than against the output,
# so a row cannot drift into a flattering category when its output changes.
# ---------------------------------------------------------------------------
CAUSES: "OrderedDict[str, str]" = OrderedDict(
    [
        (
            "one_abbrev_per_span",
            "At most one abbreviation inside a span is expanded. A span is "
            "handed to one class's grammar, and that grammar rewrites the "
            "field it was written for; a second abbreviation in the same span "
            "has no second grammar to claim it.",
        ),
        (
            "house_number_as_quantity",
            "A house number is read as a quantity rather than as digits in "
            "pairs. The grouped reading exists -- PO Box 1234 is 'twelve "
            "thirty-four' -- but ADDRESS does not ask for it for the number "
            "before the street.",
        ),
        (
            "unspoken_character_glued_on",
            "A character with no spoken form that sits against a classified "
            "span is copied through verbatim and concatenated to the word "
            "beside it, with no space: a sign, or a letter on a house number.",
        ),
        (
            "keyword_before_digits",
            "A keyword in front of a number ('dial', 'extension', 'PIN') is "
            "evidence no grammar reads, because a grammar cannot see outside "
            "its own span. Declined by design, not missing.",
        ),
        (
            "regnal_numeral",
            "A Roman numeral behind a name is offered as a cardinal, not an "
            "ordinal, because 'World War II' is the same shape and no weight "
            "separates them. Declined by design.",
        ),
        (
            "two_digit_year",
            "DATE reads four-digit years only, so a two-digit one leaves the "
            "prefix to another class.",
        ),
        (
            "month_abbrev_without_year",
            "An abbreviated month is expanded inside a full date and left "
            "alone without one. 'Sept 11, 2001' works; 'Dec 25' and "
            "'Aug 1990' do not, so DATE wants a year or a comma before it "
            "will claim the month.",
        ),
        (
            "measure_claimed_by_phone",
            "A short digit run against capital letters is claimed by PHONE "
            "before MEASURE gets to it: '797 MB' tags as a phone fragment and "
            "comes out 'seven nine seven, M B'. A weighting problem between "
            "two classes, not a missing grammar.",
        ),
        (
            "bare_capitals_not_spelled",
            "LETTERS spells dotted initialisms and not bare capital runs, "
            "because its table is corpus-derived and adding the short ones "
            "would break 'IN', 'OR', 'ME' and 'HI' as words. So 'PO' stays "
            "'PO'. Declined by design, with a cost.",
        ),
        (
            "single_initial_name",
            "NAME requires two initials, because one initial in front of a "
            "capitalised word absorbs it for free and cost '$5.4 M. Central "
            "Park' its magnitude. Declined by design.",
        ),
        (
            "missing_from_table",
            "The abbreviation or unit is simply not in the table that would "
            "expand it. A coverage gap, not a design decision.",
        ),
        ("uncategorised", "Not yet traced to a cause."),
    ]
)

#: Which cause each failing input belongs to. Keyed by the input column, so the
#: mapping is against the case and not against whatever the system printed.
CAUSE_BY_INPUT: Dict[str, str] = {
    # One abbreviation per span.
    "St Paul St": "one_abbrev_per_span",
    "Mt Vernon Ave": "one_abbrev_per_span",
    "Dr Martin Luther King Jr Blvd": "one_abbrev_per_span",
    "Lt. Col. Baker": "one_abbrev_per_span",
    "St Louis, MO 63101": "one_abbrev_per_span",
    "I walked down St Paul St to St Mary's": "one_abbrev_per_span",
    "1600 Pennsylvania Ave NW": "one_abbrev_per_span",
    "Mr. and Mrs. Smith": "one_abbrev_per_span",
    # House number read as a quantity.
    "350 5th Ave": "house_number_as_quantity",
    "742 Evergreen Terrace": "house_number_as_quantity",
    "1234 W Main St": "house_number_as_quantity",
    "1 Infinite Loop, Cupertino, CA 95014": "house_number_as_quantity",
    # Unspoken character glued to the word beside it.
    "-$40": "unspoken_character_glued_on",
    "221B Baker St": "unspoken_character_glued_on",
    # Keyword in front of digits.
    "dial 911": "keyword_before_digits",
    "extension 4412": "keyword_before_digits",
    "PIN 4417": "keyword_before_digits",
    # Regnal numerals.
    "Elizabeth II": "regnal_numeral",
    "Henry VIII": "regnal_numeral",
    # Two-digit year.
    "11/22/63": "two_digit_year",
    # An abbreviated month with no year beside it.
    "Dec 25": "month_abbrev_without_year",
    "Jun 29": "month_abbrev_without_year",
    "Aug 1990": "month_abbrev_without_year",
    # Class competition, and class coverage.
    "797 MB": "measure_claimed_by_phone",
    "PO Box 1234": "bare_capitals_not_spelled",
    "F. Scott Fitzgerald": "single_initial_name",
    # Table coverage.
    "Ft Worth, TX": "missing_from_table",
    "Rt 66": "missing_from_table",
    "No. 9": "missing_from_table",
    "approx 40": "missing_from_table",
    "5 ft 10 in": "missing_from_table",
    "32 F": "missing_from_table",
    "pp 1-10": "missing_from_table",
}


class Case:
    __slots__ = ("input", "expected", "cls", "note")

    def __init__(self, line: str):
        parts = line.rstrip("\n").split("\t")
        parts += [""] * (4 - len(parts))
        self.input, self.expected, self.cls, self.note = parts[:4]


def read_cases(path: Path = CASES) -> List[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        case = Case(line)
        if case.input and case.expected:
            cases.append(case)
    return cases


def run_wasm(inputs: List[str]) -> List[str]:
    """One node process for the whole set: the module is built once."""
    proc = subprocess.run(
        ["node", str(WASM_RUNNER), "--style", STYLE, "--text-only"],
        input="\n".join(inputs) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"wasm runner failed: {proc.stderr.strip()[:800]}")
    return proc.stdout.splitlines()


def run_native(inputs: List[str]) -> List[str]:
    if not SAYSO_BINARY.exists():
        raise SystemExit(
            f"no sayso binary at {SAYSO_BINARY}; run `make sayso` in the sayso checkout"
        )
    proc = subprocess.run(
        [str(SAYSO_BINARY), "normalize", "--style", STYLE],
        input="\n".join(inputs) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.splitlines()


def classify_failure(case: Case) -> str:
    return CAUSE_BY_INPUT.get(case.input, "uncategorised")


def score(cases: List[Case], outputs: List[str]) -> dict:
    if len(outputs) != len(cases):
        raise RuntimeError(f"{len(cases)} cases but {len(outputs)} outputs")

    per_class: "OrderedDict[str, dict]" = OrderedDict()
    failures = []
    for case, got in zip(cases, outputs):
        bucket = per_class.setdefault(case.cls, {"n": 0, "correct": 0})
        bucket["n"] += 1
        if got == case.expected:
            bucket["correct"] += 1
        else:
            failures.append(
                {
                    "input": case.input,
                    "got": got,
                    "expected": case.expected,
                    "class": case.cls,
                    "note": case.note,
                    "cause": classify_failure(case),
                }
            )

    for bucket in per_class.values():
        bucket["accuracy"] = round(bucket["correct"] / bucket["n"], 4)

    total = len(cases)
    correct = total - len(failures)

    by_cause: "OrderedDict[str, int]" = OrderedDict((k, 0) for k in CAUSES)
    for failure in failures:
        by_cause[failure["cause"]] += 1

    return {
        "stage": "normalize",
        "component": "sayso",
        "runner": "wasm",
        "style": STYLE,
        "cases": CASES.relative_to(ROOT).as_posix(),
        "n": total,
        "correct": correct,
        "accuracy": round(correct / total, 4),
        "per_class": per_class,
        "failures": failures,
        "causes": {
            name: {"description": CAUSES[name], "n": count}
            for name, count in by_cause.items()
            if count
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--native", action="store_true",
                    help="score the native binary instead of the WASM build")
    ap.add_argument("--check", action="store_true",
                    help="compare against the saved run and fail if it moved")
    ap.add_argument("--out", type=Path, default=RESULTS)
    args = ap.parse_args(argv)

    cases = read_cases()
    inputs = [c.input for c in cases]
    outputs = run_native(inputs) if args.native else run_wasm(inputs)
    report = score(cases, outputs)
    report["runner"] = "native" if args.native else "wasm"

    if args.check:
        if not args.out.exists():
            print(f"no saved run at {args.out}", file=sys.stderr)
            return 1
        saved = json.loads(args.out.read_text())
        moved = []
        if saved["n"] != report["n"]:
            moved.append(f"case count {saved['n']} -> {report['n']}")
        if saved["correct"] != report["correct"]:
            moved.append(f"correct {saved['correct']} -> {report['correct']}")
        if moved:
            print("normalization numbers moved:\n  " + "\n  ".join(moved), file=sys.stderr)
            return 1
        print(f"normalization reproduces: {report['correct']}/{report['n']} "
              f"= {report['accuracy']:.1%}")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    width = max(len(c) for c in report["per_class"])
    print(f"stage 1, normalisation -- {report['runner']} build, --style {STYLE}\n")
    print(f"  {'class'.ljust(width)}  {'n':>4}  {'acc':>7}")
    for name, bucket in report["per_class"].items():
        print(f"  {name.ljust(width)}  {bucket['n']:>4}  {bucket['accuracy']:>7.1%}")
    print(f"  {'ALL'.ljust(width)}  {report['n']:>4}  {report['accuracy']:>7.1%}")

    if report["failures"]:
        print(f"\n  {len(report['failures'])} failing rows, by cause:")
        for name, info in report["causes"].items():
            print(f"    {info['n']:>3}  {name}")
        print()
        for failure in report["failures"]:
            print(f"    [{failure['input']}]")
            print(f"       got  [{failure['got']}]")
            print(f"       want [{failure['expected']}]   ({failure['cause']})")
    print(f"\n  -> {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
