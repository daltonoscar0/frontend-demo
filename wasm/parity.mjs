// Print one StageResult JSON document per line of stdin, using the WASM build.
//
// `tests/test_wasm_parity.py` runs this and the native `sayso normalize --json`
// over the same input and requires the two streams to be identical. Keeping the
// comparison at the level of the whole document, rather than the output string,
// means a divergence in a score or an alternative fails the test too.
//
//   node parity.mjs [--style phone|plain] [--date-order canonical|preserve] [--text-only]

import { createInterface } from "node:readline";
import { raw, normalize, STYLE, DATE_ORDER } from "./sayso.mjs";

const argv = process.argv.slice(2);
function flag(name, fallback) {
  const i = argv.indexOf(`--${name}`);
  return i === -1 ? fallback : argv[i + 1];
}

const style = STYLE[flag("style", "plain")] ?? STYLE.plain;
const dateOrder = DATE_ORDER[flag("date-order", "canonical")] ?? DATE_ORDER.canonical;
const textOnly = argv.includes("--text-only");

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
const out = [];
for await (const line of lines) {
  // `raw`, not `run`: the document is printed exactly as C++ wrote it, so the
  // comparison against the native binary is byte for byte and does not go
  // through a JavaScript number formatter on the way.
  out.push(textOnly ? await normalize(line, { style, dateOrder })
                    : await raw(line, { style, dateOrder }));
}
process.stdout.write(out.join("\n") + (out.length ? "\n" : ""));
