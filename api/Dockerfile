# Stages 2 and 3 of the front end, as one container.
#
# Everything the service needs at runtime is baked in at build time: both sets
# of model weights, the encoder, CMUdict, the homograph inventory and the spaCy
# pipeline. A request never reaches the network, which is what makes the warm
# path 100 ms and the service's behaviour independent of whether the Hub is up.
#
# The three component repos are cloned at pinned commits rather than installed
# from PyPI, because two of them are not on PyPI and because `lede` resolves its
# data directory relative to its own checkout (`data/vendor/...`), so it has to
# be a checkout and not a wheel in site-packages.

FROM python:3.12-slim

# Pins. Bump deliberately: the evaluation numbers on the page were measured
# against these commits, and the page claims the service is the evaluated
# system. `make pins` in the repo root prints the current values.
ARG LEDE_REF=main
ARG CAESURA_REF=main
ARG SAYSO_REF=main

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    # Keep CPU use predictable. See TORCH_THREADS in app.py.
    OMP_NUM_THREADS=2 \
    TORCH_THREADS=2 \
    CAESURA_MODEL=/app/caesura/outputs/breaks \
    PORT=7860

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a change to the service code does not re-download
# torch. The CPU wheel index is load-bearing: the default index serves the CUDA
# build, which is about 2 GB of nvidia libraries this service will never call.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m spacy download en_core_web_sm

# The two model stages, at pinned commits.
RUN git clone -q https://github.com/daltonoscar0/Contextual-Homograph-Disambiguation-for-TTS.git lede \
 && git -C lede checkout -q "$LEDE_REF" \
 && git clone -q https://github.com/daltonoscar0/caesura.git caesura \
 && git -C caesura checkout -q "$CAESURA_REF" \
 && pip install --no-cache-dir -e ./lede -e ./caesura

# Lede's data: CMUdict for ordinary words, the Wikipedia Homograph Data
# `wordids.tsv` for which readings each heteronym has. Both are fetched by the
# repo's own script, so this cannot drift from what the evaluation used. The
# homograph clone is 40 MB of per-word TSVs and only `wordids.tsv` plus the eval
# split are needed to serve, but keeping the checkout whole means `--eval` runs
# in this image too, which is how the numbers get re-checked.
RUN bash lede/scripts/fetch_data.sh

# Caesura's System B weights, from the Hub, into the path CAESURA_MODEL names.
# Downloaded at build so the first request does not pay for it and so the
# service starts with no network at all.
RUN python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    "daltonoscar0/caesura-breaks",
    local_dir="/app/caesura/outputs/breaks",
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
)
PY

# Lede's encoder. `distilroberta-base` is the serving default and the one every
# number on the page was measured with; roberta-large is one env var away and
# deliberately not in this image, at 1.3 GB for four sentences in 1,615.
RUN python - <<'PY'
from transformers import AutoModel, AutoTokenizer
AutoTokenizer.from_pretrained("distilroberta-base")
AutoModel.from_pretrained("distilroberta-base", output_hidden_states=True)
PY

# Offline from here on. Everything above is now on disk, and without this a
# transient Hub lookup on the first request would show up as latency none of the
# measurements saw -- or as a failure, if the Hub is down when a visitor arrives.
# Set after the downloads, not before, or they would be the thing it blocked.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY app.py .

# Hugging Face Spaces runs the container as uid 1000 and the cache has to be
# writable by it. Harmless elsewhere.
RUN useradd -m -u 1000 service \
 && chown -R service:service /app
USER service

EXPOSE 7860

# One worker, deliberately. Two would double the resident set to hold a second
# copy of both models for no throughput a demo can use, and the whole point of
# keeping them loaded is that they are loaded once.
#
# The warm-up runs in the lifespan hook before the first request is accepted,
# so a container that reports healthy is a container that answers in 100 ms.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" | grep -q '"ok":true'

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 20"]
