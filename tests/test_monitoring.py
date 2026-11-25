"""T12: request events and monitor summary (MON-T01..04)."""

import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from reviewpulse.monitoring.events import EventStore, read_events
from reviewpulse.monitoring.summary import INSUFFICIENT, summarize
from reviewpulse.registry.champion import promote
from reviewpulse.release.package import package
from reviewpulse.serving.app import create_app

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "docs/schemas/monitoring-event.v1.schema.json").read_text(
        encoding="utf-8"
    )
)
GOOD = {"user_id": "synthetic-u1", "top_k": 3}


@pytest.fixture
def champion(home_env):
    rid = package(home_env["pop_run"])["release_id"]
    assert promote(rid, 0, execute=True).status == "PROMOTED"
    return {**home_env, "release_id": rid}


def _events_db(home):
    return home / "state" / "events.db"


# --- MON-T01: exactly one event per request (200 / 422 / 503) -------------------------------


def test_mon_t01_one_event_per_request(champion, home_env):
    with TestClient(create_app()) as c:
        assert c.post("/v1/recommend", json=GOOD).status_code == 200
        assert c.post("/v1/recommend", json={"user_id": " ", "top_k": 3}).status_code == 422
        c.get("/health")
        c.get("/ready")  # non-recommend endpoints do not create events
    # a 503: same home, champion bundle broken
    (champion["home"] / "releases" / champion["release_id"] / "pop.json").write_text("{}")
    with TestClient(create_app()) as c:
        assert c.post("/v1/recommend", json=GOOD).status_code == 503

    events = read_events()
    assert [e["http_status"] for e in events] == [200, 422, 503]
    for e in events:
        jsonschema.validate(e, SCHEMA)
    ok, bad, down = events
    assert ok["error_code"] is None and ok["returned_item_count"] == 2
    assert ok["fallback_used"] is False and ok["fusion_used"] is False
    assert bad["error_code"] == "INVALID_ARGUMENT" and bad["user_key_hash"] is None
    assert bad["top_k"] is None and bad["candidate_count"] is None
    assert down["error_code"] == "MODEL_NOT_READY" and down["loaded_release_id"] is None
    assert len({e["request_id"] for e in events}) == 3


def test_event_request_id_matches_response(champion):
    with TestClient(create_app()) as c:
        body = c.post("/v1/recommend", json=GOOD).json()
    assert read_events()[0]["request_id"] == body["request_id"]


def test_synthetic_demo_source_header(champion):
    with TestClient(create_app()) as c:
        c.post("/v1/recommend", json=GOOD, headers={"X-RP-Source": "synthetic_demo"})
        c.post("/v1/recommend", json=GOOD, headers={"X-RP-Source": "something-else"})
    assert [e["source"] for e in read_events()] == ["synthetic_demo", "api"]


# --- MON-T02: no raw user ids in the event store ----------------------------------------------


def test_mon_t02_no_raw_user_ids(champion):
    users = ["synthetic-u1", "synthetic-u2", "never-seen-user-xyz"]
    with TestClient(create_app()) as c:
        for u in users:
            c.post("/v1/recommend", json={"user_id": u, "top_k": 3})
        c.post("/v1/recommend", json={"user_id": "synthetic-u1", "top_k": 99})  # 422
    data = _events_db(champion["home"]).read_bytes()
    for u in users:
        assert u.encode() not in data
    keyed = [e["user_key_hash"] for e in read_events() if e["http_status"] == 200]
    assert all(k and len(k) == 64 for k in keyed)
    # the 422 happened after body parsing but before keying: no key recorded
    assert read_events()[-1]["user_key_hash"] is None


# --- MON-T03: event release id equals /ready ----------------------------------------------------


def test_mon_t03_event_release_matches_ready(champion):
    with TestClient(create_app()) as c:
        ready = c.get("/ready").json()["loaded_release_id"]
        c.post("/v1/recommend", json=GOOD)
    assert ready == champion["release_id"]
    assert read_events()[0]["loaded_release_id"] == ready


# --- MON-T04: event write failure does not break recommendations --------------------------------


def test_mon_t04_event_failure_does_not_affect_response(champion, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    store = EventStore(blocker / "events.db")  # parent is a file: every write fails
    with TestClient(create_app(events=store)) as c:
        r1 = c.post("/v1/recommend", json=GOOD)
        r2 = c.post("/v1/recommend", json={"user_id": " ", "top_k": 3})
    assert r1.status_code == 200 and r1.json()["items"]
    assert r2.status_code == 422
    assert store.failures == 2


def test_event_store_failure_on_locked_db_is_counted(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.db")

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", broken)
    ev = {"request_id": "r", "source": "api", "latency_ms": 1, "http_status": 200}
    assert store.write(ev) is False
    assert store.failures == 1


# --- summary --------------------------------------------------------------------------------------


def _ev(release="rel-a", source="api", status=200, latency=10.0, fallback=False, returned=3):
    return {
        "loaded_release_id": release,
        "source": source,
        "http_status": status,
        "latency_ms": latency,
        "fallback_used": fallback if status == 200 else None,
        "returned_item_count": returned if status == 200 else None,
    }


def test_summary_groups_rates_and_percentiles():
    events = [
        _ev(latency=float(i), fallback=i % 4 == 0, returned=0 if i == 7 else 3)
        for i in range(1, 21)
    ]
    events += [_ev(status=422, latency=1.0), _ev(status=503, latency=1.0)]
    events += [_ev(source="synthetic_demo") for _ in range(3)]
    out = summarize(events, min_events=20)
    assert out["online_recommendation_quality"]["status"] == "UNAVAILABLE"
    api, demo = out["groups"]
    assert (api["source"], demo["source"]) == ("api", "synthetic_demo")
    assert api["requests"] == 22 and api["http_4xx"] == 1 and api["http_5xx"] == 1
    assert api["status"] == "OK"
    assert api["fallback_rate"] == pytest.approx(5 / 20)  # i = 4, 8, 12, 16, 20
    assert api["empty_rate"] == pytest.approx(1 / 20)
    import numpy as np

    lat = [float(i) for i in range(1, 21)] + [1.0, 1.0]
    assert api["latency_p95_ms"] == pytest.approx(float(np.percentile(lat, 95)))
    assert demo["status"] == INSUFFICIENT and demo["latency_p95_ms"] == INSUFFICIENT
    assert demo["requests"] == 3  # counts are still shown


def test_summary_separates_releases():
    out = summarize([_ev(release="rel-a")] * 2 + [_ev(release=None, status=503)], min_events=1)
    assert [(g["loaded_release_id"], g["requests"]) for g in out["groups"]] == [
        (None, 1),
        ("rel-a", 2),
    ]


def test_summary_empty():
    out = summarize([], min_events=20)
    assert out["groups"] == [] and out["total_events"] == 0


def test_cli_monitor_summary(champion, capsys):
    from reviewpulse import cli

    with TestClient(create_app()) as c:
        for _ in range(3):
            c.post("/v1/recommend", json=GOOD)
    assert cli.main(["monitor", "summary", "--min-events", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    (group,) = out["groups"]
    assert group["requests"] == 3 and group["status"] == "OK"
    assert cli.main(["monitor", "summary", "--since", "2999-01-01T00:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["total_events"] == 0
