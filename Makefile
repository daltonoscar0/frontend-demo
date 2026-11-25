# The chain: build, evaluate, test, and hand the page to the site.
#
# Nothing here reaches the network except `api-image`, which needs the Hub for
# the weights. `test` and `eval` run offline.

SAYSO   ?= ../sayso
LEDE    ?= ../lede
CAESURA ?= ../caesura
SITE    ?= ../oscar-site
PY      ?= .venv/bin/python

export CAESURA_MODEL ?= $(abspath $(CAESURA))/outputs/breaks

.PHONY: all wasm eval test check sync-site verify-page fixtures space serve pins clean

all: wasm eval test

# ---- stage 1, to the browser ------------------------------------------
wasm:
	$(MAKE) -C wasm SAYSO=$(abspath $(SAYSO)) SCALPEL=$(abspath $(SAYSO))/../Scalpel
	$(MAKE) -C wasm size

# ---- the numbers ------------------------------------------------------
# Each script writes results/<stage>.json and has a --check mode that fails if
# the numbers moved. The page reads those files; no number on it is typed.
eval: eval-normalization eval-heteronyms eval-breaks precomputed

eval-normalization: wasm
	$(PY) eval/eval_normalization.py

eval-heteronyms:
	$(PY) eval/eval_heteronyms.py --baseline

eval-breaks:
	$(PY) eval/eval_breaks.py

# The page's cold-start fallback: stages 2 and 3 for the five inputs the
# buttons set, so the last two panels are not empty while the free host wakes.
precomputed:
	$(PY) eval/make_precomputed.py

check: wasm
	$(PY) eval/eval_normalization.py --check
	$(PY) eval/eval_heteronyms.py --check
	$(PY) eval/eval_breaks.py --check
	$(PY) eval/make_precomputed.py --check

# ---- tests ------------------------------------------------------------
test: wasm
	$(PY) -m pytest tests -q

# ---- the site ---------------------------------------------------------
# The site repo gets only what it needs to serve the page: the component, its
# stylesheet, the WebAssembly build and the saved results. This repo stays the
# source of truth, and `git status` in the site shows exactly what moved.
sync-site: wasm
	@mkdir -p $(SITE)/src/frontend/results $(SITE)/public/frontend
	cp web/Frontend.jsx web/pipeline.js $(SITE)/src/frontend/
	cp web/frontend.css $(SITE)/src/frontend/
	cp web/precomputed.json $(SITE)/src/frontend/
	cp results/*.json $(SITE)/src/frontend/results/
	cp wasm/dist/sayso.js wasm/dist/sayso.wasm $(SITE)/public/frontend/
	@echo "synced to $(SITE); add the route and commit there"

# ---- the browser check ------------------------------------------------
# Needs a server running; not part of `test`, which is offline.
#   (cd ../oscar-site && VITE_FRONTEND_API=http://127.0.0.1:7860 npm run dev)
#   make verify-page
verify-page:
	CHROME_CHANNEL=$${CHROME_CHANNEL:-chrome} node tests/verify_page.mjs $(ARGS)

fixtures:
	$(PY) eval/make_fixtures.py
	node tests/test_explanations.mjs

# ---- the model service ------------------------------------------------
space:
	deploy/space.sh

serve:
	cd api && ../$(PY) -m uvicorn app:app --host 127.0.0.1 --port 7860

# ---- the pins the API image is built from -----------------------------
pins:
	@printf 'LEDE_REF=%s\n'    "$$(git -C $(LEDE) rev-parse HEAD)"
	@printf 'CAESURA_REF=%s\n' "$$(git -C $(CAESURA) rev-parse HEAD)"
	@printf 'SAYSO_REF=%s\n'   "$$(git -C $(SAYSO) rev-parse HEAD)"

clean:
	$(MAKE) -C wasm clean
