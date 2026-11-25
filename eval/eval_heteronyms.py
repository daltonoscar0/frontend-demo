#!/usr/bin/env python3
"""Score stage 2 on Lede's evaluation split, by word, with the failures.

The split is the Wikipedia Homograph Data evaluation set: 1,615 sentences over
162 curated homographs, published by Gorman, Mazovetskiy and Nikolaev with their
LREC 2018 paper, so the numbers here are comparable to a published system rather
than to this project's own conventions.

Everything is computed through Lede's own modules -- `serve.embed`,
`Probes.probabilities`, `evaluate.score` -- and not reimplemented. Scoring runs
the *serving* path, the same code the API calls on a request, so an agreement
between this number and the page is not a coincidence of two similar
implementations.

    python eval/eval_heteronyms.py                # score, write results
    python eval/eval_heteronyms.py --check        # fail if numbers moved
    python eval/eval_heteronyms.py --baseline     # add the POS-rule baseline

Writes `results/heteronyms.json`: micro and macro accuracy with intervals, the
per-word table, and every misclassified sentence with the reading it should have
had and how many training examples that reading has. That last column is the
point: most of what is left is a corpus problem, not a model problem, and the
count is how you tell.

Takes about four minutes of CPU forward passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "heteronyms.json"

#: How many errors the model is allowed to gain or lose before --check
#: complains. Zero: the probe is deterministic given the weights, so any
#: movement at all means the weights, the split or the encoder changed, and all
#: three are things the page's claims depend on.
TOLERANCE = 0


def homograph_types() -> Dict[str, str]:
    """The corpus's own type for each homograph: morphosyntactic, lexical, both.

    Worth reporting per word because it explains the shape of the table: 78 of
    the 162 are morphosyntactic, which is why a part-of-speech tagger alone
    reaches 0.954 and why the probe has to beat that number to be interesting.
    """
    from lede.data import load_wordids

    types: Dict[str, str] = {}
    for entry in load_wordids().values():
        types.setdefault(entry.homograph.lower(), entry.homograph_type or "")
    return types


def training_support() -> Counter:
    """How many training examples each reading has.

    A reading with zero of them cannot be produced by any classifier fit on this
    data, however good. Three of the eval errors are that case and no amount of
    modelling reaches them, so the failure table reports the count next to every
    miss rather than leaving the reader to assume otherwise.
    """
    from lede.data import load_split

    return Counter(example.wordid for example in load_split("train"))


def predict_all(model: str, verbose: bool = True):
    from lede import serve
    from lede.data import load_split

    examples = load_split("eval")
    probes = serve.load_probes(model)
    if verbose:
        print(
            f"scoring {len(examples)} sentences with {probes.encoder} "
            f"({probes.layer_mode}) from the shipped weights",
            file=sys.stderr,
        )

    predictions: List[str] = []
    for index, example in enumerate(examples, 1):
        vectors, _ = serve.embed(
            example.sentence, [(example.start, example.end)], model, probes.layer_mode
        )
        posterior = probes.probabilities(example.homograph, vectors[0])
        predictions.append(max(posterior, key=posterior.get) if posterior else "")
        if verbose and (index % 200 == 0 or index == len(examples)):
            print(f"  {index}/{len(examples)}", file=sys.stderr, flush=True)
    return examples, predictions, probes


def build_report(model: Optional[str] = None, with_baseline: bool = False) -> dict:
    from lede import serve
    from lede.evaluate import score

    model = model or serve.active_model()
    examples, predictions, probes = predict_all(model)
    scores = score(predictions, examples)
    support = training_support()
    types = homograph_types()

    per_word = OrderedDict()
    for homograph in sorted(scores.per_homograph):
        per_word[homograph] = {
            "n": scores.n_per_homograph[homograph],
            "accuracy": round(scores.per_homograph[homograph], 4),
            "type": types.get(homograph, ""),
        }

    failures = []
    for example, predicted in zip(examples, predictions):
        if predicted == example.wordid:
            continue
        failures.append(
            {
                "homograph": example.homograph,
                "surface": example.sentence[example.start : example.end],
                "gold": example.wordid,
                "gold_train_examples": support[example.wordid],
                "predicted": predicted,
                "predicted_train_examples": support[predicted],
                "sentence": example.sentence,
            }
        )
    failures.sort(key=lambda f: (f["gold_train_examples"], f["homograph"]))

    # The three buckets the failure table divides into, which is the finding:
    # a reading absent from training is unreachable, a reading that is nearly
    # absent is close to it, and the rest is the model's own business.
    unwinnable = [f for f in failures if f["gold_train_examples"] == 0]
    starved = [f for f in failures if 0 < f["gold_train_examples"] <= 2]
    genuine = [f for f in failures if f["gold_train_examples"] > 2]

    report = {
        "stage": "pronounce",
        "component": "lede",
        "encoder": probes.encoder,
        "layer_mode": probes.layer_mode,
        "corpus": "Wikipedia Homograph Data, evaluation split",
        "reference": "Gorman, Mazovetskiy and Nikolaev, LREC 2018",
        "n": len(examples),
        "n_homographs": len(per_word),
        "errors": len(failures),
        "micro": round(scores.micro, 4),
        "micro_ci": [round(x, 4) for x in scores.micro_ci],
        "macro": round(scores.macro, 4),
        "macro_ci": [round(x, 4) for x in scores.macro_ci],
        "per_word": per_word,
        "failures": failures,
        "failure_buckets": {
            "gold_reading_absent_from_training": {
                "n": len(unwinnable),
                "description": "The correct reading has zero training "
                               "examples, so no classifier fit on this corpus "
                               "can emit it. Unreachable by any model.",
            },
            "gold_reading_nearly_absent": {
                "n": len(starved),
                "description": "One or two training examples against dozens "
                               "for the competing reading. Reachable in "
                               "principle, not on this data.",
            },
            "model_error": {
                "n": len(genuine),
                "description": "Both readings are well supported in training "
                               "and the probe chose wrong. The only bucket "
                               "where a better model is the answer.",
            },
        },
        "words_below_100_percent": OrderedDict(
            (word, info) for word, info in per_word.items() if info["accuracy"] < 1.0
        ),
    }

    if with_baseline:
        report["pos_baseline"] = pos_baseline(probes.encoder)
    return report


def pos_baseline(encoder: str) -> dict:
    """The no-encoder baseline, for scale, from Lede's own saved run.

    Reported because 0.989 means little without knowing that a part-of-speech
    tagger plus a majority vote already reaches 0.954 on this corpus. The probe
    has to beat *that*, not the 0.840 most-likely-reading baseline.

    Read from Lede's checked-in `results/per_homograph_<encoder>.csv` rather
    than recomputed, because recomputing it means fitting the rules on the
    training split with a tagger, and that is a different and much longer run
    than this script does. Reusing the saved run is also the honest option: it
    is the number Lede's README publishes, so the comparison on the page is
    against a published figure and not against a reimplementation of it.
    """
    import csv

    path = (
        Path(__import__("lede").__file__).resolve().parent.parent
        / "results"
        / f"per_homograph_{encoder.replace('/', '_')}.csv"
    )
    if not path.exists():
        return {"available": False, "reason": f"no saved baseline at {path}"}

    weighted = 0.0
    total = 0
    mle_weighted = 0.0
    with path.open() as fh:
        for row in csv.DictReader(fh):
            n = int(row["n_eval"])
            total += n
            weighted += n * float(row["pos_baseline_acc"])
            mle_weighted += n * float(row["mle_acc"])
    if not total:
        return {"available": False, "reason": "saved baseline is empty"}
    return {
        "available": True,
        "source": path.name,
        "pos_rule_micro": round(weighted / total, 4),
        "pos_rule_errors": round(total - weighted),
        "mle_micro": round(mle_weighted / total, 4),
        "mle_errors": round(total - mle_weighted),
        "paper_best_hybrid_micro": 0.990,
        "paper_reference": "Gorman et al. 2018, Table 3, server rules + ML",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=None, help="encoder; default is Lede's serving default")
    ap.add_argument("--baseline", action="store_true", help="also score the POS-rule baseline")
    ap.add_argument("--check", action="store_true", help="fail if the numbers moved")
    ap.add_argument("--out", type=Path, default=RESULTS)
    args = ap.parse_args(argv)

    if args.check:
        if not args.out.exists():
            print(f"no saved run at {args.out}", file=sys.stderr)
            return 1
        saved = json.loads(args.out.read_text())
        report = build_report(args.model)
        drift = abs(saved["errors"] - report["errors"])
        if drift > TOLERANCE or saved["n"] != report["n"]:
            print(
                f"heteronym numbers moved: {saved['errors']} -> {report['errors']} "
                f"errors on {report['n']} sentences",
                file=sys.stderr,
            )
            return 1
        print(f"heteronyms reproduce: {report['errors']} errors, micro {report['micro']}")
        return 0

    report = build_report(args.model, with_baseline=args.baseline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"\nstage 2, heteronyms -- {report['encoder']}, {report['corpus']}\n")
    print(f"  sentences        {report['n']}")
    print(f"  homographs       {report['n_homographs']}")
    print(f"  micro            {report['micro']:.4f}  "
          f"[{report['micro_ci'][0]:.3f}, {report['micro_ci'][1]:.3f}]")
    print(f"  macro            {report['macro']:.4f}  "
          f"[{report['macro_ci'][0]:.3f}, {report['macro_ci'][1]:.3f}]")
    print(f"  errors           {report['errors']}")
    baseline = report.get("pos_baseline", {})
    if baseline.get("available"):
        print(f"  POS baseline     {baseline['pos_rule_micro']:.4f}  "
              f"({baseline['pos_rule_errors']} errors)   <- the number to beat")
        print(f"  MLE baseline     {baseline['mle_micro']:.4f}  "
              f"({baseline['mle_errors']} errors)")
        print(f"  paper best       {baseline['paper_best_hybrid_micro']:.4f}  "
              f"({baseline['paper_reference']})")

    print("\n  failures by bucket:")
    for name, info in report["failure_buckets"].items():
        print(f"    {info['n']:>3}  {name}")

    print(f"\n  words below 100%: {len(report['words_below_100_percent'])} of "
          f"{report['n_homographs']}")
    for word, info in report["words_below_100_percent"].items():
        print(f"    {word:<16} {info['n']:>3} eval  {info['accuracy']:>6.1%}  {info['type']}")

    print("\n  every error, lowest training support first:\n")
    for f in report["failures"]:
        print(f"    {f['homograph']}: want {f['gold']} ({f['gold_train_examples']} train), "
              f"got {f['predicted']} ({f['predicted_train_examples']} train)")
        print(f"      {f['sentence'][:150]}")
    print(f"\n  -> {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
