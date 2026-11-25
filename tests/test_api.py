"""The service: its contract, its limits, and the order it applies them in.

Driven in-process through Starlette's test client, so no port is bound and the
suite stays offline. The models are real -- these are not mocks -- because the
thing most worth testing is that the chain the page runs is the chain that was
evaluated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

FLAGSHIP_NORMALISED = (
    "I read the second Doctor Lee lead a live band on Reading Road at ten thirty"
)
NAME_SPANS = [[18, 28], [49, 61]]
HINTS = {"Reading": {"arpabet": "R EH1 D IH0 NG", "respell": "RED-ing"}}


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    try:
        from caesura.model import model_available
    except ImportError:
        pytest.skip("caesura not importable")
    if not model_available():
        pytest.skip("caesura System B weights not present; set CAESURA_MODEL")

    from fastapi.testclient import TestClient

    import app as service

    # A small burst so the rate-limit test does not need 12 real requests.
    service.buckets = service.TokenBuckets(per_minute=60, burst=3, max_entries=16)
    with TestClient(service.app) as test_client:
        yield test_client


def post(client, body, ip="203.0.113.1"):
    return client.post("/v1/pipeline", json=body, headers={"x-forwarded-for": ip})


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------


def test_health_reports_ready_only_when_the_models_are_loaded(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["models_loaded"] is True
    assert body["error"] is None
    # The page reads these to explain itself, so they have to be there.
    assert body["limits"]["chars"] > 0
    assert body["limits"]["requests_per_minute"] > 0


def test_the_root_names_the_repos(client):
    body = client.get("/").json()
    assert "sayso" in body["repos"]["normalize"]
    assert "caesura" in body["repos"]["breaks"]


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flagship(client):
    response = post(
        client,
        {
            "normalized": FLAGSHIP_NORMALISED,
            "name_spans": NAME_SPANS,
            "pron_hints": HINTS,
            "system": "both",
        },
        ip="198.51.100.10",
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_both_stages_come_back(flagship):
    assert flagship["lede"]["stage"] == "lede"
    assert flagship["caesura"]["stage"] == "caesura"
    assert flagship["timings_ms"]["lede"] > 0
    assert flagship["timings_ms"]["caesura"] > 0


def test_the_phoneme_string_is_included(flagship):
    """It is a property of the StageResult object, not part of `as_dict`, and
    the page needs it. Joining the per-token prons client-side would be a second
    copy of the join rule, and an out-of-vocabulary token is exactly where two
    copies would differ."""
    phones = flagship["lede"]["pronunciation"]
    assert phones.startswith("AY1 R EH1 D")
    assert "R EH1 D IH0 NG R OW1 D" in phones


def test_every_heteronym_decision_can_be_explained_to_a_person(flagship):
    """The page turns these fields into a sentence, so all of them have to be
    present and populated: a label for the chosen reading, a label on each
    alternative, and a probability to put a number on it."""
    decisions = flagship["lede"]["decisions"]
    assert len(decisions) == 4
    for decision in decisions:
        assert decision["label"], decision
        assert decision["pron"]
        assert decision["rule"]
        assert len(decision["alternatives"]) >= 2
        for alternative in decision["alternatives"]:
            assert alternative["label"], alternative
            assert "probability" in alternative


def test_the_hints_are_honoured_rather_than_ignored(flagship):
    reading = next(d for d in flagship["lede"]["decisions"] if d["token"] == "Reading")
    assert reading["rule"] == "hint"
    assert reading["pron"] == "R EH1 D IH0 NG"
    assert reading["label"] == "city"


def test_the_name_spans_suppress_the_break_inside_the_name(flagship):
    meta = flagship["caesura"]["meta"]
    doctor = meta["words"].index("doctor")
    assert meta["breaks"][doctor] == "none"
    assert meta["vetoed"][str(doctor)] == "model:name_span"


def test_the_sayso_hint_shape_is_accepted_verbatim(client):
    """The page forwards `meta.pron_hints` exactly as the normaliser emitted it.
    Reshaping it on the client would be the place a bug hides."""
    response = post(
        client,
        {"normalized": "we drove to Reading", "pron_hints": HINTS, "system": "rules"},
        ip="198.51.100.11",
    )
    assert response.status_code == 200
    decision = response.json()["lede"]["decisions"][0]
    assert decision["pron"] == "R EH1 D IH0 NG"


@pytest.mark.parametrize("system", ["rules", "model", "both"])
def test_every_documented_system_works(client, system):
    response = post(
        client,
        {"normalized": "the man in the grey coat is my uncle", "system": system},
        ip=f"198.51.100.2{len(system)}",
    )
    assert response.status_code == 200, response.text
    assert response.json()["caesura"]["meta"]["system"] == system


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def test_too_many_characters_is_refused(client):
    response = post(client, {"normalized": "x " * 400}, ip="198.51.100.30")
    assert response.status_code == 413
    assert "characters" in response.json()["detail"]


def test_too_many_words_is_refused(client):
    import app as service

    response = post(
        client,
        {"normalized": " ".join(["word"] * (service.MAX_WORDS + 1))},
        ip="198.51.100.31",
    )
    assert response.status_code == 413
    assert "words" in response.json()["detail"]


def test_empty_input_is_a_bad_request_not_a_crash(client):
    response = post(client, {"normalized": "   "}, ip="198.51.100.32")
    assert response.status_code == 400


def test_an_unknown_system_is_refused(client):
    response = post(
        client, {"normalized": "hello there", "system": "magic"}, ip="198.51.100.33"
    )
    assert response.status_code == 400


def test_newlines_are_folded_rather_than_read_as_two_sentences(client):
    response = post(
        client, {"normalized": "the phone\nrang loudly", "system": "rules"},
        ip="198.51.100.34",
    )
    assert response.status_code == 200
    assert response.json()["caesura"]["meta"]["words"] == ["the", "phone", "rang", "loudly"]


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_a_burst_is_allowed_then_limited(client):
    ip = "203.0.113.200"
    body = {"normalized": "the phone rang", "system": "rules"}
    codes = [post(client, body, ip=ip).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_a_limited_response_says_when_to_come_back(client):
    ip = "203.0.113.201"
    body = {"normalized": "the phone rang", "system": "rules"}
    for _ in range(4):
        response = post(client, body, ip=ip)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert "limit" in response.json()


def test_the_limit_is_per_address(client):
    body = {"normalized": "the phone rang", "system": "rules"}
    for _ in range(4):
        post(client, body, ip="203.0.113.202")
    assert post(client, body, ip="203.0.113.203").status_code == 200


def test_a_refused_request_costs_no_quota(client):
    """Validation runs before the limiter, deliberately.

    A length check costs nothing to serve, so charging a token for it buys no
    protection -- and does cost something real: a visitor who pastes an
    over-long paragraph three times would be locked out for a minute for three
    requests that never touched a model.
    """
    ip = "203.0.113.204"
    for _ in range(5):
        assert post(client, {"normalized": "x " * 400}, ip=ip).status_code == 413
    body = {"normalized": "the phone rang", "system": "rules"}
    assert post(client, body, ip=ip).status_code == 200


def test_the_limiter_table_cannot_grow_without_bound(client):
    """One forged X-Forwarded-For per request would otherwise be a denial of
    service against the thing meant to resist one."""
    import app as service

    buckets = service.TokenBuckets(per_minute=60, burst=5, max_entries=8)
    for i in range(200):
        buckets.take(f"10.0.0.{i}")
    assert len(buckets._buckets) <= 8
