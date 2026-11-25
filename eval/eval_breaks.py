#!/usr/bin/env python3
"""Score stage 3: precision, recall and F1 per boundary, against two baselines.

Three test sets, and the third is the one that means anything:

  LibriTTS-R test-clean   1,565 utterances, gold derived from punctuation
  Switchboard             500 utterances, gold derived from punctuation
  adversarial             60 utterances, gold annotated by hand

The first two measure punctuation prediction with prosodic labels attached. Only
the third measures prosody. They disagree, and the disagreement is the finding.

TWO BASELINES, because "better than nothing" is not the bar:

  final_only      <b3> on the last token and nothing else. On text with the
                  punctuation stripped this is *exactly* what a punctuation-only
                  system can still know: the utterance ended. Caesura's own
                  harness computes it.

  punctuation     the breaks you get from the punctuation a careful writer would
                  use, scored against hand-annotated prosody. This is the real
                  question -- not "can a model beat nothing" but "is punctuation
                  the same thing as prosody" -- and it needs the written form of
                  each sentence, which `data/adversarial_punctuated.jsonl`
                  supplies for all 60. Only defined on the adversarial set:
                  on the other two the gold *is* the punctuation, so the
                  baseline would score 100 and measure nothing.

    python eval/eval_breaks.py                  # score, write results
    python eval/eval_breaks.py --check          # fail if numbers moved
    python eval/eval_breaks.py --limit 100      # quick smoke run

Writes `results/breaks.json`. Takes about six minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "breaks.json"
PUNCTUATED = ROOT / "eval" / "data" / "adversarial_punctuated.jsonl"


def caesura_root() -> Path:
    import caesura

    return Path(caesura.__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Scoring. Reuses caesura.evaluate so that there is one implementation of
# precision and recall in the system and the page cannot quote a number this
# file computed differently from the one the repository publishes.
# ---------------------------------------------------------------------------


def prf(predicted: Sequence[str], gold: Sequence[str], label: str) -> dict:
    """Per-boundary precision, recall and F1 for one label.

    `any` collapses b2 and b3 and asks only whether a break was placed at all,
    which is the number to read when two systems disagree about strength but
    agree about position -- a comma where a speaker takes a full stop is a
    different mistake from no break at all.
    """
    if label == "any":
        hit = lambda x: x in ("b2", "b3")  # noqa: E731
    else:
        hit = lambda x: x == label  # noqa: E731

    tp = sum(1 for p, g in zip(predicted, gold) if hit(p) and hit(g))
    fp = sum(1 for p, g in zip(predicted, gold) if hit(p) and not hit(g))
    fn = sum(1 for p, g in zip(predicted, gold) if not hit(p) and hit(g))
    precision = 100.0 * tp / (tp + fp) if tp + fp else 0.0
    recall = 100.0 * tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "p": round(precision, 1),
        "r": round(recall, 1),
        "f1": round(f1, 1),
        "support": tp + fn,
        "predicted": tp + fp,
    }


def score_system(predicted: Sequence[str], gold: Sequence[str]) -> dict:
    out = {label: prf(predicted, gold, label) for label in ("b2", "b3", "any")}
    out["macro_break_f1"] = round((out["b2"]["f1"] + out["b3"]["f1"]) / 2, 1)
    return out


def construction_spans(examples) -> "OrderedDict[str, List[slice]]":
    """Where each construction's boundaries sit in the flattened label stream.

    Computed from the examples in the order they were scored, so a system's
    flat prediction list can be cut up the same way without being re-run.
    """
    spans: "OrderedDict[str, List[slice]]" = OrderedDict()
    cursor = 0
    for example in examples:
        width = len(example.labels)
        spans.setdefault(example.construction, []).append(
            slice(cursor, cursor + width)
        )
        cursor += width
    return spans


def score_by_construction(
    predicted: Sequence[str], gold: Sequence[str], spans
) -> "OrderedDict[str, dict]":
    out: "OrderedDict[str, dict]" = OrderedDict()
    for name, slices in spans.items():
        p = [x for s in slices for x in predicted[s]]
        g = [x for s in slices for x in gold[s]]
        scored = score_system(p, g)
        scored["n_boundaries"] = len(g)
        out[name] = scored
    return out


# ---------------------------------------------------------------------------
# The punctuation baseline
# ---------------------------------------------------------------------------


def load_punctuated() -> Dict[str, str]:
    rows = {}
    for line in PUNCTUATED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "id" in record:
            rows[record["id"]] = record["punctuated"]
    return rows


def punctuation_baseline(examples) -> dict:
    """Breaks from written punctuation, scored against hand-annotated prosody.

    `caesura.text.labels_from_punctuation` is the same function that derived the
    weak labels for training, used here in the one place where it is being
    measured rather than trusted: the gold it is scored against was annotated by
    hand, not read off a comma.

    The token sequence the punctuated form yields has to match the adversarial
    set's own tokens, or the boundaries are not comparable. Any sentence where
    they differ is reported rather than silently dropped.
    """
    from caesura.text import labels_from_punctuation

    punctuated = load_punctuated()
    predicted: List[str] = []
    gold: List[str] = []
    mismatched: List[str] = []
    per_construction: "OrderedDict[str, dict]" = OrderedDict()

    for example in examples:
        written = punctuated.get(example.id)
        if written is None:
            mismatched.append(f"{example.id}: no punctuated form")
            continue
        tokens, labels = labels_from_punctuation(written)
        if tokens != list(example.tokens):
            mismatched.append(
                f"{example.id}: tokens differ\n"
                f"    punctuated -> {tokens}\n"
                f"    gold       -> {list(example.tokens)}"
            )
            continue
        predicted.extend(labels)
        gold.extend(example.labels)

        bucket = per_construction.setdefault(
            example.construction, {"predicted": [], "gold": []}
        )
        bucket["predicted"].extend(labels)
        bucket["gold"].extend(example.labels)

    report = score_system(predicted, gold)
    report["n_boundaries"] = len(gold)
    report["sentences_scored"] = len(gold) and sum(
        1 for e in examples if e.id in punctuated
    )
    report["excluded"] = mismatched
    report["by_construction"] = OrderedDict(
        (name, score_system(b["predicted"], b["gold"]))
        for name, b in per_construction.items()
    )
    return report


# ---------------------------------------------------------------------------
# The systems
# ---------------------------------------------------------------------------


def predict_sets(model_path: Optional[str], limit: int = 0) -> dict:
    """Run every system over every test set, once.

    Both model and rules are run per utterance rather than batched, because that
    is how a front end sees them and because the merge in `api._merge` is the
    one the service uses.
    """
    from caesura.api import _merge
    from caesura.data import load_adversarial, read_jsonl
    from caesura.rules import apply as apply_rules
    from caesura.types import B3, NONE

    data = caesura_root() / "data"
    test_sets = [
        ("libritts_test", data / "test_libritts.jsonl", limit),
        ("switchboard", data / "test_switchboard.jsonl", limit),
        ("adversarial", None, 0),
    ]

    from caesura.model import get_model

    model = get_model(model_path)

    out = {}
    for name, path, cap in test_sets:
        if path is None:
            examples = load_adversarial()
        else:
            examples = read_jsonl(path)
            if cap:
                examples = examples[:cap]

        gold: List[str] = []
        preds = {"final_only": [], "rules": [], "model": [], "both": []}

        for index, example in enumerate(examples, 1):
            tokens = list(example.tokens)
            gold.extend(example.labels)

            final = [NONE] * len(tokens)
            if tokens:
                final[-1] = B3
            preds["final_only"].extend(final)

            rule_labels, rule_names, _ = apply_rules(tokens)
            preds["rules"].extend(rule_labels)

            model_labels, model_scores = model.predict(tokens)
            preds["model"].extend(model_labels)

            merged, _, _ = _merge(rule_labels, rule_names, model_labels, model_scores)
            preds["both"].extend(merged)

            if index % 200 == 0:
                print(f"  {name}: {index}/{len(examples)}", file=sys.stderr, flush=True)

        systems = OrderedDict(
            (system, score_system(labels, gold)) for system, labels in preds.items()
        )
        if name == "adversarial":
            systems["punctuation"] = punctuation_baseline(examples)
            # Per construction, for every system and not only the baseline.
            # The aggregate on this set is misleading on its own: the
            # punctuation baseline wins it, and the reason is that three of the
            # seven constructions are ones written English punctuates correctly.
            # The question the stage exists to answer is what happens in the
            # other four, and only this breakdown answers it.
            spans = construction_spans(examples)
            for system, labels in preds.items():
                systems[system]["by_construction"] = score_by_construction(
                    labels, gold, spans
                )

        out[name] = {
            "n_utterances": len(examples),
            "n_boundaries": len(gold),
            "systems": systems,
        }
        print(f"  {name}: done ({len(examples)} utterances)", file=sys.stderr)
    return out


def build_report(model_path: Optional[str], limit: int = 0) -> dict:
    results = predict_sets(model_path, limit)
    return {
        "stage": "breaks",
        "component": "caesura",
        "base_model": "distilroberta-base",
        "note": (
            "LibriTTS-R and Switchboard gold is derived from punctuation, which "
            "is not prosody. Only the adversarial set is annotated by hand, and "
            "it is the only column where the punctuation baseline is defined."
        ),
        "limit": limit,
        "results": results,
    }


SYSTEM_LABELS = OrderedDict(
    [
        ("final_only", "baseline: b3 on final token"),
        ("punctuation", "baseline: written punctuation"),
        ("rules", "A: parser rules"),
        ("model", "B: distilroberta"),
        ("both", "A+B union"),
    ]
)


def print_report(report: dict) -> None:
    for name, block in report["results"].items():
        print(f"\n{name}  ({block['n_utterances']} utterances, "
              f"{block['n_boundaries']} boundaries)")
        print(f"  {'system':<30} {'b2 P':>6} {'b2 R':>6} {'b2 F1':>6}   "
              f"{'b3 P':>6} {'b3 R':>6} {'b3 F1':>6}   "
              f"{'any P':>6} {'any R':>6} {'any F1':>6}")
        for system in SYSTEM_LABELS:
            s = block["systems"].get(system)
            if not s:
                continue
            print(f"  {SYSTEM_LABELS[system]:<30} "
                  f"{s['b2']['p']:>6.1f} {s['b2']['r']:>6.1f} {s['b2']['f1']:>6.1f}   "
                  f"{s['b3']['p']:>6.1f} {s['b3']['r']:>6.1f} {s['b3']['f1']:>6.1f}   "
                  f"{s['any']['p']:>6.1f} {s['any']['r']:>6.1f} {s['any']['f1']:>6.1f}")

    adversarial = report["results"]["adversarial"]["systems"]
    punct = adversarial.get("punctuation")
    if not punct:
        return
    if punct["excluded"]:
        print(f"\n  punctuation baseline excluded {len(punct['excluded'])} "
              f"sentences whose tokens did not match:")
        for line in punct["excluded"]:
            print(f"    {line}")

    # Minor-break recall per construction, which is the whole argument. The
    # aggregate on this set says punctuation wins; this table says where.
    print("\n  minor-break (b2) recall by construction, hand-annotated gold:\n")
    buckets = list(punct["by_construction"])
    shown = ["punctuation", "rules", "model", "both"]
    header = "  " + "construction".ljust(24) + "".join(
        SYSTEM_LABELS[s].split(": ")[-1][:12].rjust(14) for s in shown
    )
    print(header)
    for name in buckets:
        row = "  " + name.ljust(24)
        for system in shown:
            by = adversarial[system].get("by_construction", {})
            value = by.get(name, {}).get("b2", {}).get("r")
            row += (f"{value:>13.1f}%" if value is not None else " " * 14)
        print(row)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=None, help="local dir or Hub id for System B")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the two corpus sets, for a smoke run")
    ap.add_argument("--check", action="store_true", help="fail if the numbers moved")
    ap.add_argument("--out", type=Path, default=RESULTS)
    args = ap.parse_args(argv)

    if args.check:
        if not args.out.exists():
            print(f"no saved run at {args.out}", file=sys.stderr)
            return 1
        saved = json.loads(args.out.read_text())
        if saved.get("limit"):
            print("saved run was a --limit smoke run; re-run in full first",
                  file=sys.stderr)
            return 1
        report = build_report(args.model, args.limit)
        moved = []
        for set_name, block in saved["results"].items():
            for system, scores in block["systems"].items():
                got = report["results"][set_name]["systems"][system]
                for label in ("b2", "b3", "any"):
                    if abs(got[label]["f1"] - scores[label]["f1"]) > 0.05:
                        moved.append(
                            f"{set_name}/{system}/{label} F1 "
                            f"{scores[label]['f1']} -> {got[label]['f1']}"
                        )
        if moved:
            print("break numbers moved:\n  " + "\n  ".join(moved), file=sys.stderr)
            return 1
        print("breaks reproduce: every F1 within 0.05")
        return 0

    report = build_report(args.model, args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print_report(report)
    print(f"\n  -> {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
