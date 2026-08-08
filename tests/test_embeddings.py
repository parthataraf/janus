"""Embedding-client robustness.

These lock the failure modes that the corrected bake-off surfaced. Two of them
are the reason the first bake-off produced a wrong answer for two days:

  * an embeddings API may return `data` OUT OF REQUEST ORDER, and pairing by
    position silently gives every chunk a wrong-but-plausible vector;
  * OpenRouter tunnels upstream failures as HTTP 200 with an `{"error": ...}`
    body, so status-code-only handling reads a 429 as success.

No network: `httpx.post` is monkeypatched.
"""

from __future__ import annotations

import pytest

from core import config, embeddings
from core.embeddings import EmbeddingUnavailable


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _vec(fill: float) -> list[float]:
    return [fill] * config.EMBED_DIM


def _ok_row(index: int, fill: float) -> dict:
    return {"index": index, "embedding": _vec(fill)}


@pytest.fixture(autouse=True)
def _api_provider(monkeypatch):
    """Force the API path and a key, whatever the developer's .env says."""
    monkeypatch.setattr(config, "EMBED_PROVIDER", "api")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "EMBED_BACKOFF", 0.0)   # keep tests fast
    monkeypatch.setattr(config, "EMBED_RETRIES", 3)


def _patch_post(monkeypatch, responses):
    """Serve `responses` in order; record the payloads that were sent."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(embeddings.httpx if hasattr(embeddings, "httpx") else __import__("httpx"),
                        "post", fake_post)
    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


# --------------------------------------------------------------------------- #
# Order safety
# --------------------------------------------------------------------------- #
def test_out_of_order_response_is_repaired_by_index(monkeypatch):
    """The API returns rows shuffled; vectors must still pair to their inputs."""
    payload = {"data": [_ok_row(2, 0.3), _ok_row(0, 0.1), _ok_row(1, 0.2)]}
    _patch_post(monkeypatch, [FakeResponse(200, payload)])

    out = embeddings.embed_texts(["a", "b", "c"])

    assert out[0][0] == pytest.approx(0.1)
    assert out[1][0] == pytest.approx(0.2)
    assert out[2][0] == pytest.approx(0.3)


def test_missing_index_coverage_is_rejected(monkeypatch):
    """Duplicate/gapped indices mean we cannot know the pairing — refuse."""
    payload = {"data": [_ok_row(0, 0.1), _ok_row(0, 0.2)]}
    _patch_post(monkeypatch, [FakeResponse(200, payload)])
    with pytest.raises(EmbeddingUnavailable, match="index set"):
        embeddings.embed_texts(["a", "b"])


def test_short_response_is_rejected(monkeypatch):
    payload = {"data": [_ok_row(0, 0.1)]}
    _patch_post(monkeypatch, [FakeResponse(200, payload)])
    with pytest.raises(EmbeddingUnavailable):
        embeddings.embed_texts(["a", "b"])


# --------------------------------------------------------------------------- #
# HTTP 200 with an error body (the OpenRouter case)
# --------------------------------------------------------------------------- #
def test_200_with_transient_error_body_is_retried_then_succeeds(monkeypatch):
    overloaded = FakeResponse(200, {"error": {"code": 429, "message": "overloaded"}})
    good = FakeResponse(200, {"data": [_ok_row(0, 0.5)]})
    calls = _patch_post(monkeypatch, [overloaded, good])

    out = embeddings.embed_texts(["a"])

    assert len(calls) == 2, "a 200-with-error body must be retried, not accepted"
    assert out[0][0] == pytest.approx(0.5)


def test_200_with_permanent_error_body_raises(monkeypatch):
    bad = FakeResponse(200, {"error": {"code": 400, "message": "context too long"}})
    _patch_post(monkeypatch, [bad])
    with pytest.raises(EmbeddingUnavailable, match="error-body"):
        embeddings.embed_texts(["a"])


def test_200_error_body_is_never_read_as_success(monkeypatch):
    """The bug this guards: status==200 alone must not be treated as success."""
    _patch_post(monkeypatch, [FakeResponse(200, {"error": {"code": 502, "message": "bad gw"}})])
    with pytest.raises(EmbeddingUnavailable):
        embeddings.embed_texts(["a"])


# --------------------------------------------------------------------------- #
# Never emit a bad vector
# --------------------------------------------------------------------------- #
def test_wrong_width_vector_is_rejected(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
    _patch_post(monkeypatch, [FakeResponse(200, payload)])
    with pytest.raises(EmbeddingUnavailable, match="width"):
        embeddings.embed_texts(["a"])


def test_all_zero_vector_is_rejected(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [0.0] * config.EMBED_DIM}]}
    _patch_post(monkeypatch, [FakeResponse(200, payload)])
    with pytest.raises(EmbeddingUnavailable, match="all zeros"):
        embeddings.embed_texts(["a"])


def test_non_finite_vector_is_rejected(monkeypatch):
    v = [0.1] * config.EMBED_DIM
    v[7] = float("nan")
    _patch_post(monkeypatch, [FakeResponse(200, {"data": [{"index": 0, "embedding": v}]})])
    with pytest.raises(EmbeddingUnavailable, match="NaN"):
        embeddings.embed_texts(["a"])


# --------------------------------------------------------------------------- #
# Transport / status handling
# --------------------------------------------------------------------------- #
def test_transient_status_is_retried(monkeypatch):
    calls = _patch_post(monkeypatch, [
        FakeResponse(503, None, "unavailable"),
        FakeResponse(200, {"data": [_ok_row(0, 0.4)]}),
    ])
    out = embeddings.embed_texts(["a"])
    assert len(calls) == 2
    assert out[0][0] == pytest.approx(0.4)


def test_permanent_status_raises_immediately(monkeypatch):
    calls = _patch_post(monkeypatch, [FakeResponse(401, None, "unauthorized")])
    with pytest.raises(EmbeddingUnavailable, match="401"):
        embeddings.embed_texts(["a"])
    assert len(calls) == 1, "an auth failure must not be retried"


def test_repeated_transient_status_gives_up_with_the_real_reason(monkeypatch):
    """After the last attempt the specific failure is reported, not a generic one."""
    calls = _patch_post(monkeypatch, [FakeResponse(503, None, "nope")])
    with pytest.raises(EmbeddingUnavailable, match="503"):
        embeddings.embed_texts(["a"])
    assert len(calls) == config.EMBED_RETRIES


def test_repeated_transport_errors_exhaust_retries(monkeypatch):
    import httpx
    _patch_post(monkeypatch, [httpx.ConnectError("no route to host")])
    with pytest.raises(EmbeddingUnavailable, match="exhausted"):
        embeddings.embed_texts(["a"])


def test_missing_api_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(EmbeddingUnavailable, match="OPENAI_API_KEY"):
        embeddings.embed_texts(["a"])


def test_batches_are_split_to_embed_batch(monkeypatch):
    monkeypatch.setattr(config, "EMBED_BATCH", 2)
    payload = {"data": [_ok_row(0, 0.1), _ok_row(1, 0.2)]}
    calls = _patch_post(monkeypatch, [FakeResponse(200, payload)])
    embeddings.embed_texts(["a", "b", "c", "d"])
    assert len(calls) == 2
    assert [len(c["input"]) for c in calls] == [2, 2]


def test_oversized_input_is_clipped(monkeypatch):
    monkeypatch.setattr(config, "EMBED_MAX_CHARS", 10)
    calls = _patch_post(monkeypatch, [FakeResponse(200, {"data": [_ok_row(0, 0.1)]})])
    embeddings.embed_texts(["x" * 500])
    assert len(calls[0]["input"][0]) == 10


def test_empty_input_returns_empty_without_calling(monkeypatch):
    calls = _patch_post(monkeypatch, [FakeResponse(200, {"data": []})])
    assert embeddings.embed_texts([]) == []
    assert calls == []
