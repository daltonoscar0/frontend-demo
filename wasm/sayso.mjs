// The JavaScript face of the Sayso WASM module.
//
// This file does three things and deliberately nothing else: instantiate the
// module once, fold vertical whitespace the way the Python package does, and
// hand back the parsed StageResult. Every field in that document was produced
// by the C++ side. If a decision, a score or an alternative were computed here
// it would be a second copy of the weighting scheme, free to drift from the one
// that actually decided — which is the reasoning in Sayso's own `report.h`, and
// it applies with more force across a language boundary, not less.

import createSayso from "./dist/sayso.js";

/** @typedef {{plain: 0, phone: 1}} Style */
export const STYLE = { plain: 0, phone: 1 };
export const DATE_ORDER = { canonical: 0, preserve: 1 };

let modulePromise = null;

/**
 * Instantiate the module, once per page. Safe to call repeatedly and from
 * several places at once: every caller awaits the same promise.
 */
export function load(options = {}) {
  if (modulePromise === null) modulePromise = createSayso(options);
  return modulePromise;
}

/** Discard the instance. Tests use this; the page does not. */
export function unload() {
  modulePromise = null;
}

function foldVertical(text) {
  // A newline reaches the tokeniser as the start of a second sentence, and a
  // carriage return as a character inside one. The contract passes a single
  // utterance, so anything vertical becomes a space rather than an error.
  return typeof text === "string" ? text.split(/\r\n|\r|\n/).join(" ") : "";
}

function call(module, symbol, text, style, dateOrder) {
  const line = foldVertical(text);
  // stringToNewUTF8 rather than ccall's automatic marshalling, because the
  // automatic path allocates on the stack and a long paste would overflow it.
  const pointer = module.stringToNewUTF8(line);
  try {
    const out = module[symbol](pointer, style, dateOrder);
    return module.UTF8ToString(out);
  } finally {
    module._free(pointer);
  }
}

/**
 * Normalise `text` and report what was decided along the way.
 *
 * @param {string} text one utterance
 * @param {{style?: 0|1, dateOrder?: 0|1}} [opts]
 * @returns {Promise<object>} the pipeline contract's StageResult
 */
export async function run(text, opts = {}) {
  return JSON.parse(await raw(text, opts));
}

/**
 * The StageResult as the C++ side printed it, unparsed.
 *
 * The parity harness compares this against `sayso normalize --json` byte for
 * byte. Going through `JSON.parse` first would reformat the numbers and turn a
 * real comparison into a comparison of two JavaScript serialisers.
 *
 * @param {string} text
 * @param {{style?: 0|1, dateOrder?: 0|1}} [opts]
 * @returns {Promise<string>}
 */
export async function raw(text, opts = {}) {
  const module = await load();
  const style = opts.style ?? STYLE.plain;
  const dateOrder = opts.dateOrder ?? DATE_ORDER.canonical;
  return call(module, "_sayso_stage_result", text, style, dateOrder);
}

/** The normalised text alone. */
export async function normalize(text, opts = {}) {
  const module = await load();
  const style = opts.style ?? STYLE.plain;
  const dateOrder = opts.dateOrder ?? DATE_ORDER.canonical;
  return call(module, "_sayso_normalize", text, style, dateOrder);
}
