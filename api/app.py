"""The two model stages of the front end, over HTTP.

Stage 1 (Sayso, normalisation) is not here. It is compiled to WebAssembly and
runs in the page, so normalisation is instant and costs this service nothing.
What arrives here is Sayso's output: the normalised text, the proper-name spans
it tagged, and the pronunciation hints it emitted. Stages 2 and 3 need an 82M
parameter encoder each and a dependency parser, so they run here.

The request body is therefore Sayso's StageResult, narrowed to the three fields
the next two stages consume. The client is the authority on stage 1 and this
service does not second-guess it, which is also why `normalized` is accepted as
free text rather than re-derived: there is exactly one normaliser in the system
and it is the one the user watched run.

Both models are loaded at import and kept resident. A warm request is about
100 ms for both stages; a cold one pays 14 s to bring distilroberta off disk,
which is why `warm()` runs before the first request rather than during it.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration. Every limit is an environment variable so the Space and the
# Swarm stack can differ without a code change.
# ---------------------------------------------------------------------------

#: Hard cap on input length. The demo's longest example is 180 characters and
#: System B truncates at 512 subwords, so this is generous for the page and
#: still bounds the work a single request can ask for. Rejected with 413.
MAX_CHARS = int(os.environ.get("MAX_CHARS", "500"))
#: A second cap, on words, because 500 characters of "a a a a ..." is 250 tokens
#: and the cost of both stages is per token, not per character.
MAX_WORDS = int(os.environ.get("MAX_WORDS", "80"))
#: Per-IP token bucket: `RATE_BURST` requests available at once, refilled at
#: `RATE_PER_MINUTE` a minute. A human poking at the demo never notices; a
#: script scraping it gets 429 with a Retry-After.
RATE_PER_MINUTE = float(os.environ.get("RATE_PER_MINUTE", "30"))
RATE_BURST = float(os.environ.get("RATE_BURST", "12"))
#: Bound on the rate-limiter's own memory. Without it, one request per forged
#: X-Forwarded-For would grow the table without limit, which is a denial of
#: service against a service that exists to resist one.
RATE_TABLE_MAX = int(os.environ.get("RATE_TABLE_MAX", "4096"))
#: Trust X-Forwarded-For. True in deployment, where this always sits behind the
#: Space's router or Traefik; set to 0 if ever exposed directly, because the
#: header is caller-controlled and would otherwise defeat the rate limit.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "1") not in ("0", "false", "False")
#: Origins allowed to call this from a browser. The page is the only client.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "https://oscarpdalton.com,https://www.oscarpdalton.com,"
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]
#: Keep CPU use predictable. Two threads is faster than one on the Space's two
#: vCPUs and avoids torch spawning as many threads as the host has cores, which
#: on a shared box is antisocial and on a capped container is slower, not faster.
TORCH_THREADS = int(os.environ.get("TORCH_THREADS", "2"))

CAESURA_SYSTEMS = ("rules", "model", "both")


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TokenBuckets:
    """Per-IP token bucket with a bounded table.

    Thread-safe because uvicorn runs the sync endpoint in a worker thread pool,
    so two requests really can be here at once.
    """

    def __init__(self, per_minute: float, burst: float, max_entries: int):
        self._refill_per_second = per_minute / 60.0
        self._burst = burst
        self._max_entries = max_entries
        self._buckets: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def take(self, key: str) -> Optional[float]:
        """Spend one token. Returns None if allowed, else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._refill_per_second)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                self._buckets.move_to_end(key)
                return (1.0 - tokens) / self._refill_per_second
            self._buckets[key] = (tokens - 1.0, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_entries:
                # Evict the least recently seen. An evicted caller gets a full
                # bucket back, which is the right way to be wrong: the table is
                # a fairness device, not a security boundary.
                self._buckets.popitem(last=False)
            return None


buckets = TokenBuckets(RATE_PER_MINUTE, RATE_BURST, RATE_TABLE_MAX)


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Models, loaded once
# ---------------------------------------------------------------------------

_warm_lock = threading.Lock()
_warm_state: Dict[str, Any] = {"ready": False, "error": None, "seconds": None}

WARM_TEXT = "I read the second Doctor Lee lead a live band on Reading Road at ten thirty"


def warm() -> Dict[str, Any]:
    """Load both models by running one sentence through each.

    Loading by *using* rather than by calling a loader is deliberate: it also
    warms the CMUdict parse, the spaCy pipeline, the probe weights and torch's
    own lazy kernels, so the first real request is as fast as the second.
    """
    with _warm_lock:
        if _warm_state["ready"]:
            return _warm_state
        started = time.monotonic()
        try:
            import torch

            torch.set_num_threads(TORCH_THREADS)
            from caesura.api import run as caesura_run
            from lede.api import run as lede_run

            lede_run(WARM_TEXT)
            caesura_run(WARM_TEXT, system="both", name_spans=[[18, 28]])
            _warm_state["ready"] = True
            _warm_state["error"] = None
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            _warm_state["error"] = f"{type(exc).__name__}: {exc}"
        _warm_state["seconds"] = round(time.monotonic() - started, 2)
        return _warm_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm()
    yield


app = FastAPI(
    title="TTS front end",
    description=(
        "Heteronym disambiguation and phrase-break prediction for a TTS front "
        "end. Normalisation runs in the browser as WebAssembly and is not here."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
    max_age=86400,
)


# ---------------------------------------------------------------------------
# Request and response
# ---------------------------------------------------------------------------


class PipelineRequest(BaseModel):
    """Sayso's output, narrowed to what stages 2 and 3 consume."""

    normalized: str = Field(
        ...,
        description="Sayso's `output`: the normalised text, which is what the "
                    "next two stages take as input.",
    )
    name_spans: List[List[int]] = Field(
        default_factory=list,
        description="Sayso's `meta.name_spans`: character offsets into "
                    "`normalized` that are proper names. Lede gives a heteronym "
                    "inside one its proper-noun reading; Caesura places no "
                    "break inside one.",
    )
    pron_hints: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sayso's `meta.pron_hints`, forwarded verbatim: "
                    '`{"Reading": {"arpabet": "R EH1 D IH0 NG", "respell": '
                    '"RED-ing"}}`. Overrides the dictionary for words the '
                    "normaliser already knows. A phone string or a list of "
                    "phones is accepted too; Lede reads all three shapes.",
    )
    system: str = Field(
        default="both",
        description="Caesura system: rules (A), model (B) or both (the union).",
    )


def too_big(text: str) -> Optional[JSONResponse]:
    if len(text) > MAX_CHARS:
        return JSONResponse(
            status_code=413,
            content={
                "error": "input too long",
                "detail": f"{len(text)} characters; the limit is {MAX_CHARS}",
                "limit": {"chars": MAX_CHARS, "words": MAX_WORDS},
            },
        )
    words = len(text.split())
    if words > MAX_WORDS:
        return JSONResponse(
            status_code=413,
            content={
                "error": "input too long",
                "detail": f"{words} words; the limit is {MAX_WORDS}",
                "limit": {"chars": MAX_CHARS, "words": MAX_WORDS},
            },
        )
    return None


@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness and readiness. The page calls this to decide what to show."""
    return {
        "ok": bool(_warm_state["ready"]),
        "models_loaded": bool(_warm_state["ready"]),
        "warm_seconds": _warm_state["seconds"],
        "error": _warm_state["error"],
        "limits": {
            "chars": MAX_CHARS,
            "words": MAX_WORDS,
            "requests_per_minute": RATE_PER_MINUTE,
            "burst": RATE_BURST,
        },
        "stages": {
            "normalize": "runs in the browser (WebAssembly), not here",
            "pronounce": "lede",
            "breaks": "caesura",
        },
    }


@app.post("/v1/pipeline")
def pipeline(
    body: PipelineRequest,
    request: Request,
    user_agent: str = Header(default=""),
) -> Any:
    """Run stages 2 and 3 over Sayso's output.

    One call rather than two, so the page has one loading state and one failure
    mode instead of two of each.
    """
    # Validation before rate limiting, deliberately. These three checks are a
    # length test and a set membership and cost nothing to serve, so charging a
    # token for them buys no protection and does cost something real: a user who
    # pastes an over-long paragraph three times would be locked out for a minute
    # for three requests that never touched a model. The bucket guards the
    # expensive path, which is everything below `warm()`.
    text = " ".join(body.normalized.splitlines()).strip()
    if not text:
        return JSONResponse(
            status_code=400, content={"error": "empty input", "detail": "nothing to say"}
        )
    oversize = too_big(text)
    if oversize is not None:
        return oversize
    if body.system not in CAESURA_SYSTEMS:
        return JSONResponse(
            status_code=400,
            content={
                "error": "unknown system",
                "detail": f"system must be one of {CAESURA_SYSTEMS}",
            },
        )

    wait = buckets.take(client_ip(request))
    if wait is not None:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(max(1, int(wait) + 1))},
            content={
                "error": "rate limited",
                "detail": f"try again in {wait:.0f}s",
                "limit": {"requests_per_minute": RATE_PER_MINUTE, "burst": RATE_BURST},
            },
        )

    state = warm()
    if not state["ready"]:
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "20"},
            content={
                "error": "models not loaded",
                "detail": state["error"] or "still loading",
            },
        )

    from caesura.api import run as caesura_run
    from lede.api import run as lede_run

    started = time.perf_counter()
    lede_stage = lede_run(
        text,
        pron_hints=body.pron_hints or None,
        name_spans=body.name_spans or None,
    )
    lede_result = lede_stage.as_dict()
    # The whole-utterance phone string is a property of the StageResult object
    # and not part of `as_dict`. Joining the per-token prons on the client
    # instead would be a second copy of the join rule, and the one place it
    # differs -- an out-of-vocabulary token, emitted as `<oov:WORD>` -- is
    # exactly where the two copies would disagree.
    lede_result["pronunciation"] = lede_stage.pronunciation
    lede_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    caesura_result = caesura_run(
        text,
        system=body.system,
        name_spans=body.name_spans or None,
    )
    caesura_ms = (time.perf_counter() - started) * 1000

    return {
        "lede": lede_result,
        "caesura": caesura_result,
        "timings_ms": {
            "lede": round(lede_ms, 1),
            "caesura": round(caesura_ms, 1),
        },
    }


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "TTS front end, stages 2 and 3",
        "demo": "https://oscarpdalton.com/frontend",
        "repos": {
            "integration": "https://github.com/daltonoscar0/frontend-demo",
            "normalize": "https://github.com/daltonoscar0/sayso",
            "pronounce": "https://github.com/daltonoscar0/Contextual-Homograph-Disambiguation-for-TTS",
            "breaks": "https://github.com/daltonoscar0/caesura",
        },
        "endpoints": {"POST /v1/pipeline": "run stages 2 and 3", "GET /health": "readiness"},
    }
