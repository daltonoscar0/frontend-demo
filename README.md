# frontend-demo

A text front end for speech synthesis: three stages chained into one pipeline,
with a live demo at **[oscarpdalton.com/frontend](https://oscarpdalton.com/frontend)**.

```
Input   →  "I read the 2nd Dr. Lee lead a live band on Reading Rd at 10:30"

normalise  →  "I read the second Doctor Lee lead a live band on Reading Road at ten thirty"
pronounce  →  AY1 R EH1 D DH AH0 S EH1 K AH0 N D D AA1 K T ER0 L IY1 L IY1 D AH0
              L AY1 V B AE1 N D AA1 N R EH1 D IH0 NG R OW1 D AE1 T T EH1 N TH ER1 D IY2
breaks     →  i read the second doctor lee lead a live band on reading road at ten *thirty* <b3>
```

Before a voice can say a sentence, something has to decide how to say it:
what `2nd` and `10:30` are in words, whether `read` is past or present,
whether `Reading` is a town or something you do, and where a speaker would
pause when the text gives them no commas to go on.

Three repositories answer those questions. This one chains them, shows every
decision, and measures where each one breaks.

| stage | repository | what it does | where it runs |
|---|---|---|---|
| 1 | [sayso](https://github.com/daltonoscar0/sayso) | bidirectional weighted-FST normalisation | **the browser**, as WebAssembly |
| 2 | [Lede](https://github.com/daltonoscar0/Contextual-Homograph-Disambiguation-for-TTS) | heteronym disambiguation, grapheme-to-phoneme | the API |
| 3 | [caesura](https://github.com/daltonoscar0/caesura) | phrase-break and emphasis prediction | the API |

## Results

Every number here comes from a saved run in [`results/`](results/), and every
script has a `--check` mode that fails if the numbers move.

### Stage 1 — normalisation

**73.8%** on 122 hard cases written by hand for this demo
([`eval/data/normalization_cases.tsv`](eval/data/normalization_cases.tsv)),
scored through the WebAssembly build the page loads.

That number is lower than sayso's own 93.9% on a held-out shard of the Google
text normalisation corpus, and it should be: this set is **adversarial by
construction**. Three of its categories exist because probing found a weakness
and the set was then written to measure it. Read it as a map of what is weak,
not as a quality score.

| strong | weak |
|---|---|
| TIME 100%, ORDINAL 100%, FRACTION 100%, CARDINAL/DECIMAL 100%, MONEY 91% | ABBREV 50%, ADDRESS 43%, PHONE 63%, NAME 62% |

Thirty-two failures, and they are **eleven causes, not thirty-two problems**.
Seven are documented decisions rather than defects. The three largest:

| n | cause |
|---:|---|
| 8 | **At most one abbreviation per span is expanded.** A span goes to one class's grammar, which rewrites the field it was written for; a second abbreviation in the same span has no second grammar to claim it. `St Paul St` → `Saint Paul St`, `Mt Vernon Ave` → `Mount Vernon Ave`, `Lt. Col. Baker` → `Lieutenant Col. Baker`. |
| 7 | **The abbreviation is not in the table.** `Ft`, `Rt`, `No.`, `approx`. A coverage gap, cheap to close. |
| 3 | **A house number is read as a quantity.** `1234 W Main St` → "one thousand two hundred thirty-four", where a voice says "twelve thirty-four". The grouped reading exists — `PO Box 1234` gets it right — ADDRESS just does not ask for it. |

Five rows of the case file were **adjudicated against me**: I wrote the expected
column before running anything, and on `3:00`, `the 2nd of May`, `$1,234`, `Jr`
and `Sr` sayso was right and I was wrong. Those are marked in the file. A case
file whose author never loses is a case file that was written afterwards.

### Stage 2 — heteronyms

**0.9895 micro, 17 errors on 1,615 sentences**, 162 curated homographs, on the
Wikipedia Homograph Data evaluation split published with Gorman, Mazovetskiy and
Nikolaev (LREC 2018).

| system | micro | errors |
|---|---:|---:|
| most likely reading | 0.840 | 258 |
| part-of-speech rules | 0.954 | 74 |
| Gorman et al. 2018, best hybrid | 0.990 | ~16 |
| **this probe** (distilroberta-base, frozen) | **0.9895** | **17** |

The baseline worth beating is 0.954, not 0.840: 78 of the 162 homographs are
morphosyntactic, so a tagger with a majority vote is already a strong system.
The 95% interval is [0.983, 0.993] and contains the paper's 0.990, so this is a
consistent lead rather than a separated one.

17 of 162 words are below 100%. The rest are perfect, so the failure table is
the whole of what is wrong, and the useful column is how many training examples
the correct reading has:

| n | bucket |
|---:|---|
| 1 | the correct reading has **zero** training examples, so no classifier fit on this corpus can emit it |
| 1 | one or two examples against dozens for the competing reading |
| 15 | both readings well supported, and the probe chose wrong — the only bucket where a better model is the answer |

### Stage 3 — phrase breaks

Per-boundary precision, recall and F1, on punctuation-stripped text.

**Read this first.** The gold for two of the three test sets is derived from
punctuation, and punctuation is not prosody. A reader pauses after a long
subject where written English puts no comma at all, and every one of those
boundaries is labelled "no break" in training. Only the adversarial set is
annotated by hand.

| any-break F1 | LibriTTS-R | Switchboard | adversarial (hand) |
|---|---:|---:|---:|
| break on last word only | 21.2 | 78.7 | 64.9 |
| **written punctuation** | — | — | **85.8** |
| A: parser rules | 46.1 | 74.0 | 78.8 |
| B: distilroberta | **83.1** | **81.4** | 75.0 |
| A+B union | 77.9 | 75.7 | 81.8 |

The ranking inverts between the two kinds of gold. A project reporting only the
corpus numbers would conclude the sequence labeller is the answer; on hand
annotation it loses to seven rules over a dependency parse.

**And the punctuation baseline beats all three.** That is worth stating plainly
because it is the opposite of a flattering result. I wrote the written-English
punctuation for all 60 adversarial sentences
([`eval/data/adversarial_punctuated.jsonl`](eval/data/adversarial_punctuated.jsonl))
and turned it into breaks with the same function that derived the training
labels. Here is why it wins, and where it runs out — minor-break recall against
hand-annotated gold:

| construction | punctuation | A: rules | B: model | A+B |
|---|---:|---:|---:|---:|
| np/z garden path | **100.0%** | 0.0% | 25.0% | 25.0% |
| appositive | **100.0%** | 60.0% | 46.7% | 66.7% |
| fronted PP | **100.0%** | 62.5% | 12.5% | 62.5% |
| coordination | 25.0% | **75.0%** | 0.0% | **75.0%** |
| reduced relative | **0.0%** | 30.0% | 10.0% | 30.0% |
| long subject | **0.0%** | **87.5%** | 0.0% | **87.5%** |
| break at no punctuation site | **0.0%** | **70.0%** | 0.0% | **70.0%** |

Where written English licenses a comma, punctuation is a near-perfect prosody
predictor and both models are worse than it. Where it licenses nothing — 28 of
the 60 sentences — punctuation recovers **none** of the minor breaks, by
construction, and the dependency rules recover 30% to 87% of them.

That is the whole case for the stage existing, and it is not the case the
aggregate makes. A front end with reliable punctuation should mostly trust it.
One reading a phone transcript has nothing to trust.

## What the chain adds

The stages are chained, not stacked. Stage 1 knows things stages 2 and 3 cannot
recover, because it still had the capital letters and the abbreviations when it
decided: it tagged `Dr. Lee` a `NAME` and `Reading Rd` an `ADDRESS`, and it
passes both forward as character spans into its own output.

Stage 2 uses them to give `Reading` the Berkshire town's pronunciation. Stage 3
uses them to refuse a break it would otherwise place:

```console
$ python -m caesura --system both "I read the second Doctor Lee lead a live band ..."
i read the second *doctor* <b3> lee lead a live band on reading road at ten *thirty* <b3>

$ python -m caesura --system both --name-spans "[[18,28],[49,61]]" "..."
i read the second doctor lee lead a live band on reading road at ten *thirty* <b3>
```

The sequence labeller puts a major break between "doctor" and "lee" at 0.404
confidence, and the recall-oriented union inherits it because a `b3` beats the
rules' no-break. A pause inside a name is audibly broken however the rest of the
utterance is phrased. By the time stage 3 runs, "Doctor Lee" is `doctor lee` and
nothing in the text distinguishes it from two ordinary nouns — so the cue has to
come from upstream. That is `caesura.run(..., name_spans=...)`, added for this
and defaulting to off so none of caesura's published numbers move.

**What the chain does not fix.** caesura's hand annotation wants `<b2>` after
"lee" and after "band" in the flagship sentence. Neither system places either,
and the chain does not help: the parse makes "the second doctor lee" a
four-token subject, one below the five-token floor on `b2_long_subject`.
Lowering that floor would produce the break on this one sentence, which is
exactly why it has not been lowered. `tests/test_flagship.py` asserts the miss
as it stands, so when it is genuinely fixed the test fails, which is the correct
way for it to break.

## How it runs

Stage 1 is zero-dependency C++, so it compiles to WebAssembly and runs in the
tab: **112 KB gzipped**, 0.2 ms a sentence once warm, no network. Its output is
**byte-identical to the native binary on 20,616 documents** across both styles
and five test sets (`tests/test_wasm_parity.py`) — which is what makes "the demo
is the evaluated system" a fact rather than a hope.

Stages 2 and 3 are a service, because each needs an 82M-parameter encoder and
stage 3 also needs a dependency parser. Measured, with both models resident:

| | |
|---|---|
| peak resident memory | 778 MB |
| warm request, both stages | ~100 ms |
| cold start | 14 s, paid once at startup, never on a request |

It is deployed as a [Hugging Face Space](api/README.md). Nothing is downloaded
at runtime — weights, encoder, CMUdict, homograph inventory and spaCy pipeline
are all in the image, with `HF_HUB_OFFLINE=1` set, so a Hub outage cannot become
a request failure. Per-IP rate limiting (30/min, burst 12) and a 500-character
cap are in the application; validation runs *before* the limiter so an over-long
paste costs no quota. [`deploy/stack.yml`](deploy/stack.yml) moves it onto
`oscarpdalton.com/api/frontend` as a Swarm service when that is arranged.

No substitute model is used anywhere, so there is no accuracy lost to the split:
the service runs the exact code the evaluation scored.

## Layout

```
wasm/       sayso -> WebAssembly: the C API, the Makefile, the JS wrapper
api/        the FastAPI service for stages 2 and 3, and its container
web/        the page: component, stylesheet, client, and the results it reads
eval/       three evaluation scripts and their data
results/    saved runs; the page reads these and no number on it is typed
tests/      end-to-end, parity, service, reproduction, and the browser check
deploy/     Swarm stack file and the request to the people who run the host
```

## Running it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r api/requirements.txt
.venv/bin/pip install -e ../sayso -e ../lede -e ../caesura
.venv/bin/python -m spacy download en_core_web_sm

make wasm        # stage 1 -> wasm/dist/, needs emscripten
make eval        # all three evaluations -> results/
make test        # the offline suite
make check       # re-run every evaluation and fail if a number moved (~10 min)
make sync-site   # copy the page into ../oscar-site
make pins        # the commits the API image is built from
```

`make test` and `make eval` need no network. caesura's System B weights come
from [`daltonoscar0/caesura-breaks`](https://huggingface.co/daltonoscar0/caesura-breaks);
point `CAESURA_MODEL` at a local copy and the model tests run, or leave it unset
and they skip rather than reach for the Hub.

The browser check needs a server and is separate for that reason:

```bash
(cd ../oscar-site && VITE_FRONTEND_API=http://127.0.0.1:7860 npm run dev)
node tests/verify_page.mjs
```

## The page

One box prefilled with the flagship sentence, four example buttons, and four
panels: what you typed, normalised, phonemes, breaks. Every word a stage changed
is highlighted, and hovering or tapping it explains the decision in plain
language — which rule fired, which sense was chosen, how confident it was, and
what it beat.

The explanation appears in a fixed row under its stage rather than in a floating
tooltip, which is a choice made for the phone: a tooltip has to be positioned,
can leave the viewport, and cannot be read while the thing it describes is under
a thumb.

The evaluation tables below the demo, including every failure, come from
`results/`. No analytics, no tracking. Stage 1 never leaves the tab; stages 2
and 3 send the normalised text to the service and nothing is stored.

## Licence

MIT, © Oscar Dalton 2026.
