#!/usr/bin/env python3
"""Generate web/precomputed.json: stages 2 and 3 for the page's five inputs.

    python eval/make_precomputed.py           # regenerate
    python eval/make_precomputed.py --check   # fail if it would change

The API runs on a free CPU host that idles out, so the first visitor after a
quiet spell waits half a minute for a container to wake. Until that request
lands, stage 1 has already answered in the tab and the last two panels are
empty — the page looks broken at exactly the moment someone is deciding whether
to care.

So the five inputs the page can produce without typing — the flagship sentence
and the four example buttons — ship with their stage 2 and stage 3 output
already computed. The page shows those, labelled as precomputed, while the live
request is in flight, and keeps showing them if it fails.

These are real outputs from the real code, not written by hand: the same sayso
binary, the same `lede.api.run` and `caesura.api.run` the service calls. That
is the point of `--check` in CI. A fallback that drifted from what the service
answers would be worse than no fallback at all, because it would be wrong
without looking wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "precomputed.json"
SAYSO_BINARY = ROOT.parent / "sayso" / "sayso"
PIPELINE_JS = ROOT / "web" / "pipeline.js"

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
_weights = ROOT.parent / "caesura" / "outputs" / "breaks"
if _weights.exists():
    os.environ.setdefault("CAESURA_MODEL", str(_weights))


def page_inputs() -> list[str]:
    """The texts the page can reach with no typing, read from pipeline.js.

    Parsed out of the source rather than copied here, because two lists that
    have to agree and are maintained separately will not agree for long. If
    this stops matching, `--check` fails in CI and says so.
    """
    source = PIPELINE_JS.read_text()

    def js_string(expression: str) -> str:
        """Concatenated single- or double-quoted JS literals, as one string."""
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'', expression)
        joined = "".join(a or b for a, b in parts)
        return joined.encode().decode("unicode_escape")

    flagship_src = re.search(
        r"export const FLAGSHIP\s*=\s*(.*?);", source, re.DOTALL
    )
    if flagship_src is None:
        raise SystemExit("could not find FLAGSHIP in web/pipeline.js")
    flagship = js_string(flagship_src.group(1))

    examples_src = re.search(
        r"export const EXAMPLES\s*=\s*\[(.*?)\n\];", source, re.DOTALL
    )
    if examples_src is None:
        raise SystemExit("could not find EXAMPLES in web/pipeline.js")

    texts: list[str] = []
    for block in examples_src.group(1).split("  {"):
        match = re.search(r"\btext:\s*(.*?)(?:,\n|\n\s*\})", block, re.DOTALL)
        if match is None:
            continue
        body = match.group(1)
        texts.append(flagship if "FLAGSHIP" in body else js_string(body))

    seen: list[str] = []
    for text in [flagship, *texts]:
        if text and text not in seen:
            seen.append(text)
    return seen


def stage1(text: str) -> dict:
    """Sayso, through the native binary.

    tests/test_wasm_parity.py asserts the WebAssembly build the page loads is
    byte-identical to this binary on 20,616 documents, which is what makes the
    native output a legitimate stand-in for what the browser computed.
    """
    if not SAYSO_BINARY.exists():
        raise SystemExit(f"no sayso binary at {SAYSO_BINARY}; run `make` in ../sayso")
    out = subprocess.run(
        [str(SAYSO_BINARY), "normalize", "--style", "phone", "--json"],
        input=text + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def stages23(normalized: str, name_spans: list, pron_hints: dict) -> dict:
    """Exactly what POST /v1/pipeline returns, minus the timings.

    Deliberately a mirror of the handler in api/app.py, including the
    `pronunciation` field that is a property of the StageResult rather than
    part of `as_dict()`. If that handler changes shape, this has to change with
    it, and `--check` against a stale file is what notices.

    Timings are left out: they are a property of the machine that served the
    request, and a precomputed panel must not display a latency it never
    incurred.
    """
    from caesura.api import run as caesura_run
    from lede.api import run as lede_run

    lede_stage = lede_run(
        normalized,
        pron_hints=pron_hints or None,
        name_spans=name_spans or None,
    )
    lede_result = lede_stage.as_dict()
    lede_result["pronunciation"] = lede_stage.pronunciation
    caesura_result = caesura_run(
        normalized, system="both", name_spans=name_spans or None
    )
    return {
        "lede": drop_timings(json.loads(json.dumps(lede_result, default=_plain))),
        "caesura": drop_timings(
            json.loads(json.dumps(caesura_result, default=_plain))
        ),
    }


def drop_timings(obj):
    """Strip every `seconds` field, however deeply nested.

    Two reasons, and either on its own would be enough. A precomputed panel
    must not report how long a request took, because it did not take one. And
    these are the only values that move between two runs on the same input, so
    removing them is what makes `--check` a test of the pipeline's output
    rather than of this laptop's load average.
    """
    if isinstance(obj, dict):
        return {k: drop_timings(v) for k, v in obj.items() if k != "seconds"}
    if isinstance(obj, list):
        return [drop_timings(v) for v in obj]
    return obj


def _plain(obj):
    """Dataclasses and numpy scalars, as JSON."""
    for attr in ("as_dict", "to_json", "to_dict", "_asdict"):
        if hasattr(obj, attr):
            return getattr(obj, attr)()
    if hasattr(obj, "__dict__"):
        return vars(obj)
    if hasattr(obj, "item"):
        return obj.item()
    raise TypeError(f"cannot serialise {type(obj).__name__}")


def build() -> dict:
    entries = {}
    for text in page_inputs():
        one = stage1(text)
        meta = one.get("meta") or {}
        rest = stages23(
            one["output"],
            meta.get("name_spans") or [],
            meta.get("pron_hints") or {},
        )
        entries[text] = {"normalized": one["output"], **rest}
        print(f"  {text[:58]!r:62s} -> {one['output'][:48]!r}", file=sys.stderr)
    return {
        "note": (
            "Stages 2 and 3 for the inputs the page can reach without typing. "
            "Generated by eval/make_precomputed.py from the same code the "
            "service runs; regenerate it when any stage changes."
        ),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed file is not what this produces.",
    )
    args = parser.parse_args()

    fresh = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"{OUT} does not exist", file=sys.stderr)
            return 1
        if OUT.read_text() != fresh:
            print(
                f"{OUT} is out of date: a stage's output has moved since it was "
                f"generated. Run `python eval/make_precomputed.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUT} matches the pipeline.")
        return 0

    OUT.write_text(fresh)
    print(f"{OUT}: {len(json.loads(fresh)['entries'])} inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
