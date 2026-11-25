---
title: TTS Front End API
emoji: 🗣
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Heteronym disambiguation and phrase-break prediction for TTS
---

# TTS front end — stages 2 and 3

Backend for the live demo at **[oscarpdalton.com/frontend](https://oscarpdalton.com/frontend)**.

A text front end for speech synthesis has three jobs. This service does two of
them. The third, normalisation, is compiled to WebAssembly and runs in the page.

| stage | what it does | where it runs |
|---|---|---|
| 1. normalise | `2nd` → `second`, `Dr. Lee` → `Doctor Lee`, `10:30` → `ten thirty` | the browser, as WebAssembly (112 KB gzipped) |
| 2. pronounce | every word to phones, heteronyms resolved from context | **here** |
| 3. phrase breaks | where a speaker pauses, from the words alone | **here** |

Stages 2 and 3 each need an 82M-parameter encoder and stage 3 also needs a
dependency parser, which is why they are a service and stage 1 is not.

## Use it

```bash
curl -s https://daltonoscar0-tts-front-end-api.hf.space/v1/pipeline \
  -H 'content-type: application/json' \
  -d '{
    "normalized": "I read the second Doctor Lee lead a live band on Reading Road at ten thirty",
    "name_spans": [[18, 28], [49, 61]],
    "pron_hints": {"Reading": {"arpabet": "R EH1 D IH0 NG", "respell": "RED-ing"}},
    "system": "both"
  }'
```

The request body is stage 1's output. `normalized` is the normalised text;
`name_spans` and `pron_hints` are what the normaliser knows and the next two
stages cannot work out for themselves — that "Doctor Lee" is a name, and that
"Reading" here is the Berkshire town and not the gerund.

The response carries both stages' `StageResult` documents in full: the chosen
reading for every heteronym with its alternatives and their probabilities, and
every break with the rule that placed it.

`GET /health` reports readiness and the current limits. `GET /` lists the repos.

## Limits

| | |
|---|---|
| input | 500 characters and 80 words |
| rate | 30 requests a minute per IP, burst of 12 |
| memory | 778 MB resident with both models loaded; the Swarm stack caps the container at 1.5 GB |
| latency | ~100 ms warm for both stages; ~15 s cold, paid once at startup, not on a request |

Both models are loaded before the first request is accepted, so a container
reporting healthy is a container that answers in 100 ms. Nothing is downloaded
at runtime: the weights, the encoder, CMUdict, the homograph inventory and the
spaCy pipeline are all baked into the image, and `HF_HUB_OFFLINE=1` is set so a
Hub outage cannot become a request failure.

Validation runs before the rate limiter, so an over-long paste costs no quota.

## What's underneath

| repo | stage | headline |
|---|---|---|
| [sayso](https://github.com/daltonoscar0/sayso) | normalise | bidirectional weighted FST, zero dependencies, 93.9% on a held-out Google TN shard |
| [Lede](https://github.com/daltonoscar0/Contextual-Homograph-Disambiguation-for-TTS) | pronounce | 0.989 micro on Wikipedia Homograph Data, a linear probe on frozen distilroberta |
| [caesura](https://github.com/daltonoscar0/caesura) | breaks | 83.1 any-break F1 in domain, 78.8 on hand-annotated prosody |
| [frontend-demo](https://github.com/daltonoscar0/frontend-demo) | the chain | this service, the WASM build, the evaluation and the page |

Every number on the demo page comes from a saved evaluation run in
`frontend-demo/results/`, and the failure tables are published next to the
scores. Read those before believing any of it.

MIT, © Oscar Dalton 2026.
