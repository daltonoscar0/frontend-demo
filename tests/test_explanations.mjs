// The prose the page puts in front of a reader.
//
// Every number and every name in an explanation comes from the stage that made
// the decision; these functions only choose words. That makes them worth
// testing separately, because a wrong sentence here misrepresents a correct
// decision, which is the one failure mode a reader cannot detect.
//
// The fixtures are real stage output, captured from the flagship sentence --
// not hand-written shapes. `make fixtures` refreshes them.
//
//   node tests/test_explanations.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  EXAMPLES,
  FLAGSHIP,
  explainBreak,
  explainNormalization,
  explainPronunciation,
  explainVeto,
  precomputed,
  precomputedInputs,
} from "../web/pipeline.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(
  readFileSync(join(here, "fixtures", "flagship.json"), "utf8"),
);

let passed = 0;
function test(name, body) {
  try {
    body();
    passed += 1;
    console.log(`  ok   ${name}`);
  } catch (error) {
    console.log(` FAIL  ${name}\n       ${error.message}`);
    process.exitCode = 1;
  }
}

const bySurface = Object.fromEntries(
  fixtures.sayso.decisions.map((d) => [d.surface, d]),
);
const byToken = Object.fromEntries(
  fixtures.lede.decisions.map((d) => [d.token, d]),
);

// ---- normalisation -------------------------------------------------------

test("names the class a span was read as", () => {
  const prose = explainNormalization(bySurface["10:30"]);
  assert.match(prose, /clock time/);
});

test("names the readings a span beat, with their classes", () => {
  const prose = explainNormalization(bySurface["2nd"]);
  assert.match(prose, /beat/);
  assert.match(prose, /second drive/, "the ADDRESS reading it beat");
  assert.match(prose, /street address/, "and what that reading was");
});

test("explains why a cheaper reading can still lose", () => {
  // The non-obvious part of the weighting, and the thing a reader will ask.
  const prose = explainNormalization(bySurface["2nd"]);
  assert.match(prose, /cheapest path|cheap on its own/);
});

test("says nothing about alternatives when there were none", () => {
  const prose = explainNormalization({
    surface: "Rd",
    result: "Road",
    kind: "address",
    alternatives: [],
  });
  assert.match(prose, /street address/);
  assert.doesNotMatch(prose, /beat/);
});

test("falls back gracefully on a class it has no phrase for", () => {
  const prose = explainNormalization({
    surface: "x",
    result: "y",
    kind: "newclass",
    alternatives: [],
  });
  assert.match(prose, /newclass/);
});

// ---- pronunciation -------------------------------------------------------

test("names the sense in words, not as a wordid", () => {
  const prose = explainPronunciation(byToken.read);
  assert.match(prose, /past tense verb/);
  assert.doesNotMatch(prose, /read_past/, "an identifier is not an explanation");
});

test("gives the confidence and what it beat", () => {
  const prose = explainPronunciation(byToken.read);
  assert.match(prose, /85\.8%|85\.7%|86%/, "the chosen reading's probability");
  assert.match(prose, /present tense verb/, "the reading it beat");
  assert.match(prose, /14\.2%|14%/, "and that reading's probability");
});

test("says the decision came from the context, not a table", () => {
  const prose = explainPronunciation(byToken.live);
  assert.match(prose, /context/);
});

test("credits the earlier stage when a hint decided it", () => {
  const prose = explainPronunciation(byToken.Reading);
  assert.match(prose, /normaliser/);
  assert.match(prose, /city/);
  // It must not claim a probability the probe never produced: on a hinted
  // decision every alternative comes back 0.0, and reporting "0% confidence"
  // for a correct answer would be worse than reporting nothing.
  assert.doesNotMatch(prose, /0\.0% confidence|0% confidence/);
});

test("explains the proper-noun rule when a name span decided it", () => {
  const prose = explainPronunciation({
    token: "Reading",
    wordid: "reading_geo",
    label: "city",
    pron: "R EH1 D IH0 NG",
    rule: "name-span",
    alternatives: [],
  });
  assert.match(prose, /proper name/);
});

// ---- breaks --------------------------------------------------------------

test("names the rule that placed a break, in words", () => {
  const final = fixtures.caesura.decisions.at(-1);
  const prose = explainBreak(final);
  assert.match(prose, /utterance ends here/);
  assert.doesNotMatch(prose, /b3_utterance_final/, "the rule name is not prose");
});

test("says when both systems agree", () => {
  const final = fixtures.caesura.decisions.at(-1);
  assert.match(final.rule, /^consensus:/, "fixture should be a consensus break");
  assert.match(explainBreak(final), /[Bb]oth systems agree/);
});

test("flags a model-only break as the recall-oriented choice", () => {
  const prose = explainBreak({
    rule: "model",
    result: "doctor <b3>",
    score: 0.404,
    note: null,
  });
  assert.match(prose, /recall/);
  assert.match(prose, /40%/, "the confidence, so a reader can discount it");
  assert.match(prose, /most likely to be wrong/);
});

test("distinguishes a major break from a minor one", () => {
  const major = explainBreak({ rule: "model", result: "x <b3>", score: 0.9 });
  const minor = explainBreak({ rule: "model", result: "x <b2>", score: 0.9 });
  assert.match(major, /^A major break/);
  assert.match(minor, /^A minor break/);
});

test("explains a suppressed break and where the cue came from", () => {
  const prose = explainVeto("model:name_span");
  assert.match(prose, /sequence labeller/);
  assert.match(prose, /proper name/);
  assert.match(prose, /audibly broken/);
  assert.match(prose, /normaliser/, "the reader should learn which stage knew");
});

test("explains the vetoes that were already there", () => {
  assert.match(explainVeto("b2_long_subject:det_noun"), /determiner/);
  assert.match(explainVeto("b2_appositive:aux_verb"), /auxiliary/);
});

test("falls back on a veto reason it has never seen", () => {
  assert.match(explainVeto("something_new"), /suppressed/);
});

// ---- every decision in the flagship sentence gets prose ------------------

test("every decision in the flagship sentence produces a real sentence", () => {
  const all = [
    ...fixtures.sayso.decisions.map(explainNormalization),
    ...fixtures.lede.decisions.map(explainPronunciation),
    ...fixtures.caesura.decisions.map(explainBreak),
    ...Object.values(fixtures.caesura.meta.vetoed).map(explainVeto),
  ];
  assert.equal(all.length, 10, "4 spans, 4 heteronyms, 1 break, 1 suppressed break");
  for (const prose of all) {
    assert.ok(prose.length > 30, `too short: ${prose}`);
    assert.match(prose, /\.$/, `not a sentence: ${prose}`);
    assert.doesNotMatch(prose, /undefined|NaN|\[object/, `leaked a value: ${prose}`);
  }
});


// --------------------------------------------------------------------------
// The cold-start fallback
//
// Node runs this file, so these also prove pipeline.js still loads without a
// bundler: the table is passed in rather than imported, and a bare JSON import
// here would be ERR_IMPORT_ATTRIBUTE_MISSING.
// --------------------------------------------------------------------------

const precomputedTable = JSON.parse(
  readFileSync(join(here, "..", "web", "precomputed.json"), "utf8"),
);

test("the flagship sentence has a saved answer", () => {
  const saved = precomputed(precomputedTable, FLAGSHIP);
  assert.ok(saved, "the page loads with this sentence; it must never be empty");
  assert.ok(saved.lede.pronunciation.length > 40);
  assert.ok(saved.caesura.output.includes("reading road"));
});

test("every example button has one", () => {
  for (const example of EXAMPLES) {
    assert.ok(
      precomputed(precomputedTable, example.text),
      `no saved answer for the "${example.label}" button`,
    );
  }
});

test("a saved answer is flagged as one", () => {
  assert.equal(precomputed(precomputedTable, FLAGSHIP).precomputed, true);
});

test("a saved answer reports no latency", () => {
  const saved = precomputed(precomputedTable, FLAGSHIP);
  assert.ok(!("timings_ms" in saved), "nothing was timed, so nothing is timed");
});

test("a sentence nobody ran gets nothing rather than something close", () => {
  assert.equal(precomputed(precomputedTable, "I read the 2nd Dr. Lee"), null);
  assert.equal(precomputed(precomputedTable, `${FLAGSHIP} `), null);
  assert.equal(precomputed(precomputedTable, ""), null);
});

test("a missing or malformed table is not a crash", () => {
  assert.equal(precomputed(null, FLAGSHIP), null);
  assert.equal(precomputed({}, FLAGSHIP), null);
  assert.equal(precomputed({ entries: null }, FLAGSHIP), null);
  assert.deepEqual(precomputedInputs(null), []);
});

test("the saved inputs are exactly the ones the page can reach", () => {
  const reachable = new Set([FLAGSHIP, ...EXAMPLES.map((e) => e.text)]);
  assert.deepEqual(
    new Set(precomputedInputs(precomputedTable)),
    reachable,
  );
});

console.log(`\n${passed} checks passed`);
