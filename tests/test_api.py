"""T11: FastAPI service (API-T01..07) on releases built from the synthetic fixture."""

import importlib
import json
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import OTHER_KEY, short
from reviewpulse.registry.champion import promote
from reviewpulse.release.package import package
from reviewpulse.serving.app import create_app
from reviewpulse.train import train


def _client(**kw):
    return TestClient(create_app(background_load=False, **kw))


def _rec(client, body, **headers):
    return client.post("/v1/recommend", json=body, headers=headers)


@pytest.fixture
def served(home_env):
    """Home with a promoted Pop champion (A) and a second passing Pop release (B)."""
    a = package(home_env["pop_run"])["release_id"]
    assert promote(a, 0, execute=True).status == "PROMOTED"
    (second,) = train("Pop", "configs/split.yaml", "configs/train.yaml")
    b = package(second["run_id"])["release_id"]
    return {**home_env, "A": a, "B": b}


def _assert_api_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"request_id", "error"}
    uuid.UUID(body["request_id"])
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body


# --- API-T01: importing the module does not load anything --------------------------------


def test_api_t01_import_does_not_load(monkeypatch):
    import reviewpulse.registry.state as state_mod
    import reviewpulse.release.loader as loader_mod

    calls = []
    monkeypatch.setattr(loader_mod, "load_release", lambda *a, **k: calls.append("load"))
    monkeypatch.setattr(state_mod, "read_champion", lambda *a, **k: calls.append("champion"))
    sys.modules.pop("reviewpulse.serving.app", None)
    try:
        mod = importlib.import_module("reviewpulse.serving.app")
        assert calls == []
        assert mod.app.state.service.readiness() == {
            "ready": False,
            "loaded_release_id": None,
            "reason": "LOADING",
        }
    finally:
        sys.modules.pop("reviewpulse.serving.app", None)  # drop the module bound to mocks


# --- API-T02: no champion -------------------------------------------------------------------


def test_api_t02_no_champion(home_env):
    with _client() as c:
        assert c.get("/health").json() == {"status": "alive"}
        r = c.get("/ready")
        assert r.status_code == 503
        assert r.json() == {"ready": False, "loaded_release_id": None, "reason": "NO_CHAMPION"}
        _assert_api_error(_rec(c, {"user_id": "synthetic-u1", "top_k": 3}), 503, "MODEL_NOT_READY")


def test_background_load_reports_loading_then_ready(served):
    with TestClient(create_app(background_load=True)) as c:
        assert c.get("/health").status_code == 200
        r = c.get("/ready")
        assert r.json()["reason"] in ("LOADING", None)
        import time

        for _ in range(200):
            if c.get("/ready").status_code == 200:
                break
            time.sleep(0.05)
        assert c.get("/ready").json()["loaded_release_id"] == served["A"]


# --- API-T03: a real release becomes ready and recommends ----------------------------------


def test_api_t03_ready_and_recommends(served):
    with _client() as c:
        r = c.get("/ready")
        assert r.status_code == 200
        assert r.json() == {"ready": True, "loaded_release_id": served["A"], "reason": None}
        resp = _rec(c, {"user_id": "synthetic-u2", "top_k": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "request_id",
            "loaded_release_id",
            "items",
            "fallback_used",
            "fusion_used",
            "candidate_count",
            "empty_reason",
        }
        uuid.UUID(body["request_id"])
        assert body["loaded_release_id"] == served["A"]
        assert body["fusion_used"] is False
        assert short([i["item_id"] for i in body["items"]]) == ["b", "d"]  # Pop minus seen {a, c}
        assert [i["rank"] for i in body["items"]] == [1, 2]
        assert body["candidate_count"] == 2
        assert body["empty_reason"] is None


def test_top_k_truncates(served):
    with _client() as c:
        body = _rec(c, {"user_id": "brand-new-user", "top_k": 2}).json()
    assert short([i["item_id"] for i in body["items"]]) == ["a", "b"]
    assert body["candidate_count"] == 4


# --- API-T04: promotion without restart does not change the loaded release -----------------


def test_api_t04_promotion_needs_restart(served):
    with _client() as c:
        assert c.get("/ready").json()["loaded_release_id"] == served["A"]
        assert promote(served["B"], 1, execute=True).status == "PROMOTED"
        assert c.get("/ready").json()["loaded_release_id"] == served["A"]
        body = _rec(c, {"user_id": "synthetic-u1", "top_k": 3}).json()
        assert body["loaded_release_id"] == served["A"]
    with _client() as c2:  # "restart"
        assert c2.get("/ready").json()["loaded_release_id"] == served["B"]


# --- API-T05: invalid arguments -> 422 ApiError ---------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "   ", "top_k": 3},
        {"user_id": "", "top_k": 3},
        {"user_id": "synthetic-u1", "top_k": 0},
        {"user_id": "synthetic-u1", "top_k": 51},
        {"user_id": "synthetic-u1", "top_k": "3"},
        {"user_id": "synthetic-u1", "top_k": True},
        {"user_id": "synthetic-u1", "top_k": 3.0},
        {"user_id": "x" * 129, "top_k": 3},
        {"user_id": 123, "top_k": 3},
        {"top_k": 3},
        {"user_id": "synthetic-u1"},
        {"user_id": "synthetic-u1", "top_k": 3, "debug": True},
        [],
    ],
)
def test_api_t05_invalid_arguments(served, body):
    with _client() as c:
        resp = _rec(c, body)
    err = _assert_api_error(resp, 422, "INVALID_ARGUMENT")
    assert "synthetic-u1" not in json.dumps(err)  # never echo the raw user_id


@pytest.mark.parametrize("raw", [b"{not json", b"", b'{"user_id": "synthetic-u1", "top_k": 3'])
def test_api_t05_bad_json(served, raw):
    with _client() as c:
        resp = c.post("/v1/recommend", content=raw, headers={"Content-Type": "application/json"})
    _assert_api_error(resp, 422, "INVALID_ARGUMENT")


def test_api_t05_validation_before_readiness(home_env):
    # with no champion, a bad request is still a 422, not a 503
    with _client() as c:
        _assert_api_error(_rec(c, {"user_id": " ", "top_k": 3}), 422, "INVALID_ARGUMENT")


# --- API-T06: golden case -------------------------------------------------------------------


def test_api_t06_golden_u1_excludes_seen(served):
    with _client() as c:
        body = _rec(c, {"user_id": "synthetic-u1", "top_k": 10}).json()
    items = short([i["item_id"] for i in body["items"]])
    assert "a" not in items and "b" not in items
    assert items == ["c", "d"]
    assert body["fallback_used"] is False


def test_user_id_is_stripped_before_keying(served):
    with _client() as c:
        a = _rec(c, {"user_id": "  synthetic-u1 ", "top_k": 10}).json()
        b = _rec(c, {"user_id": "synthetic-u1", "top_k": 10}).json()
    assert a["items"] == b["items"]


# --- API-T07: unknown users fall back to Pop ------------------------------------------------


def test_api_t07_unknown_user_fallback_with_bpr(home_env):
    from reviewpulse.registry import champion

    pop = package(home_env["pop_run"])["release_id"]
    bpr = package(home_env["bpr_run"])["release_id"]
    # put BPR in place directly (it does not pass the gate on this fixture)
    champion._cas("PROMOTE", bpr, 0, "test", "0" * 64, "PROMOTED")
    with _client() as c:
        assert c.get("/ready").json()["loaded_release_id"] == bpr
        known = _rec(c, {"user_id": "synthetic-u1", "top_k": 5}).json()
        unknown = _rec(c, {"user_id": "never-seen-user", "top_k": 5}).json()
    assert known["fallback_used"] is False
    assert unknown["fallback_used"] is True
    assert short([i["item_id"] for i in unknown["items"]]) == ["a", "b", "c", "d"]
    assert pop != bpr


# --- load failures: report the reason, never fall back to another release ------------------


def test_broken_champion_is_bundle_invalid(served):
    (served["home"] / "releases" / served["A"] / "pop.json").write_text("{}")
    with _client() as c:
        r = c.get("/ready")
        assert r.status_code == 503
        assert r.json() == {"ready": False, "loaded_release_id": None, "reason": "BUNDLE_INVALID"}
        _assert_api_error(_rec(c, {"user_id": "synthetic-u1", "top_k": 3}), 503, "MODEL_NOT_READY")


def test_wrong_key_is_user_key_unavailable(served, monkeypatch):
    monkeypatch.setenv("RP_USER_KEY", OTHER_KEY)
    with _client() as c:
        assert c.get("/ready").json()["reason"] == "USER_KEY_UNAVAILABLE"


def test_no_candidates_returns_empty_list(served, monkeypatch):
    with _client() as c:
        rel = c.app.state.service.release
        key = rel.keyer("synthetic-u1")
        rel.seen[key] = list(rel.space.items)  # user has seen everything
        body = _rec(c, {"user_id": "synthetic-u1", "top_k": 3}).json()
    assert body["items"] == [] and body["empty_reason"] == "NO_CANDIDATES"
    assert body["candidate_count"] == 0


def test_internal_error_is_500_api_error(served, monkeypatch):
    with TestClient(create_app(background_load=False), raise_server_exceptions=False) as c:
        rel = c.app.state.service.release

        def boom(*a, **k):
            raise RuntimeError("secret detail")

        monkeypatch.setattr(rel, "recommend_key", boom)
        resp = _rec(c, {"user_id": "synthetic-u1", "top_k": 3})
    body = _assert_api_error(resp, 500, "INTERNAL_ERROR")
    assert "secret detail" not in json.dumps(body)


def test_api_t07_unknown_user_fallback_with_pop_champion(served):
    with _client() as c:
        known = _rec(c, {"user_id": "synthetic-u2", "top_k": 5}).json()
        unknown = _rec(c, {"user_id": "never-seen-user", "top_k": 5}).json()
        test_only = _rec(c, {"user_id": "synthetic-u4", "top_k": 5}).json()  # appears only in test
    assert known["fallback_used"] is False
    assert unknown["fallback_used"] is True
    assert test_only["fallback_used"] is True
    # same Pop ranking either way, minus seen items for the known user
    assert short([i["item_id"] for i in unknown["items"]]) == ["a", "b", "c", "d"]
