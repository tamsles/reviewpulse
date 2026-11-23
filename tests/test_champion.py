"""T10: champion CAS promotion and rollback (PROM-T01..05)."""

import json
import threading
from pathlib import Path

import jsonschema
import pytest
import yaml

from reviewpulse.registry import champion as champ_mod
from reviewpulse.registry.champion import PromotionError, history, promote, rollback, show
from reviewpulse.registry.state import connect, registry_path
from reviewpulse.release.package import package
from reviewpulse.train import train

SCHEMA = json.loads(
    (Path(__file__).parents[1] / "docs/specs/schemas/champion-state.v1.schema.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
def rels(home_env):
    """Two distinct passing Pop releases (A, B) and a BPR release that fails the gate."""
    a = package(home_env["pop_run"])["release_id"]
    (second,) = train("Pop", "configs/split.yaml", "configs/train.yaml")
    b = package(second["run_id"])["release_id"]
    bpr = package(home_env["bpr_run"])["release_id"]
    assert len({a, b, bpr}) == 3
    return {**home_env, "A": a, "B": b, "BPR": bpr}


def _champion_row():
    conn = connect()
    try:
        return conn.execute("SELECT * FROM champion").fetchall()
    finally:
        conn.close()


# --- PROM-T01: dry-run writes nothing ----------------------------------------------------


def test_prom_t01_dry_run_does_not_write(rels):
    connect().close()
    before = registry_path().read_bytes()
    r = promote(rels["A"], 0)
    assert r.status == "DRY_RUN"
    assert registry_path().read_bytes() == before
    r = promote(rels["BPR"], 0)  # rejected dry-run: still nothing written
    assert r.status == "REJECTED"
    assert registry_path().read_bytes() == before
    assert show()["release_id"] is None


def test_dry_run_without_registry_creates_nothing(rels):
    assert promote(rels["A"], 0).status == "DRY_RUN"
    assert rollback(rels["A"], 0).status == "REJECTED"
    assert not registry_path().exists()


# --- PROM-T02: first promotion -------------------------------------------------------------


def test_prom_t02_first_promotion(rels):
    initial = show()
    jsonschema.validate(initial, SCHEMA)
    assert initial["version_counter"] == 0
    r = promote(rels["A"], 0, execute=True, actor="tester")
    assert r.status == "PROMOTED" and r.version_counter == 1
    state = show()
    jsonschema.validate(state, SCHEMA)
    assert state["release_id"] == rels["A"]
    assert state["previous_release_id"] is None
    assert state["actor"] == "tester"
    assert state["gate_decision_sha256"] == r.gate["decision_sha256"]
    assert history()[0]["status"] == "PROMOTED"


def test_rejected_execute_is_logged_and_champion_unchanged(rels):
    promote(rels["A"], 0, execute=True)
    before = _champion_row()
    r = promote(rels["BPR"], 1, execute=True)
    assert r.status == "REJECTED" and r.reason == "NOT_BETTER_THAN_POP"
    assert _champion_row() == before
    assert history()[0]["status"] == "REJECTED"


def test_execute_refused_without_thresholds(rels):
    path = rels["home"] / "configs" / "quality.yaml"
    q = yaml.safe_load(path.read_text())
    q["quality"]["recommendation"]["min_improvement"] = None
    path.write_text(yaml.safe_dump(q))
    with pytest.raises(PromotionError, match="thresholds"):
        promote(rels["A"], 0, execute=True)
    assert show()["version_counter"] == 0


def test_wrong_expected_version_conflicts(rels):
    r = promote(rels["A"], 5, execute=True)
    assert r.status == "CONFLICT"
    assert show()["version_counter"] == 0


# --- PROM-T03: two concurrent promotions with the same expected version --------------------


def test_prom_t03_concurrent_promotions_only_one_wins(rels, monkeypatch):
    barrier = threading.Barrier(2, timeout=60)
    real_gate = champ_mod.gate

    def gate_then_wait(release_id):
        decision = real_gate(release_id)
        barrier.wait()  # both threads have passed the gate; now race for the pointer
        return decision

    monkeypatch.setattr(champ_mod, "gate", gate_then_wait)
    connect().close()
    results, errors = {}, []

    def worker(name):
        try:
            results[name] = promote(rels[name], 0, execute=True, actor=name)
        except Exception as exc:  # surface thread errors in the main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    statuses = sorted(r.status for r in results.values())
    assert statuses == ["CONFLICT", "PROMOTED"]
    winner = next(n for n, r in results.items() if r.status == "PROMOTED")
    state = show()
    assert state["version_counter"] == 1
    assert state["release_id"] == rels[winner]


# --- PROM-T04: A -> B -> A, then a stale version must conflict ---------------------------------


def test_prom_t04_aba_stale_version_conflicts(rels):
    assert promote(rels["A"], 0, execute=True).status == "PROMOTED"  # v1 = A
    assert promote(rels["B"], 1, execute=True).status == "PROMOTED"  # v2 = B
    r = rollback(rels["A"], 2)
    assert r.status == "DRY_RUN" and show()["release_id"] == rels["B"]
    r = rollback(rels["A"], 2, execute=True)  # v3 = A again
    assert r.status == "ROLLED_BACK" and r.version_counter == 3
    state = show()
    assert state["release_id"] == rels["A"]
    assert state["previous_release_id"] == rels["B"]
    jsonschema.validate(state, SCHEMA)
    # someone still holding version 1 ("champion is A") must not be able to write
    stale = promote(rels["B"], 1, execute=True)
    assert stale.status == "CONFLICT"
    assert show()["version_counter"] == 3


# --- PROM-T05: rollback to a release that was never promoted ---------------------------------


def test_prom_t05_rollback_to_never_promoted_rejected(rels):
    promote(rels["A"], 0, execute=True)
    r = rollback(rels["B"], 1, execute=True)
    assert r.status == "REJECTED" and r.reason == "NEVER_PROMOTED"
    assert show()["release_id"] == rels["A"]
    assert history()[0]["status"] == "REJECTED"


def test_rollback_to_broken_release_rejected(rels):
    promote(rels["A"], 0, execute=True)
    promote(rels["B"], 1, execute=True)
    (rels["home"] / "releases" / rels["A"] / "pop.json").write_text("{}")
    r = rollback(rels["A"], 2, execute=True)
    assert r.status == "REJECTED" and r.reason == "BUNDLE_INVALID"
    assert show()["release_id"] == rels["B"]


def test_rollback_with_stale_version_conflicts(rels):
    promote(rels["A"], 0, execute=True)
    promote(rels["B"], 1, execute=True)
    r = rollback(rels["A"], 1, execute=True)
    assert r.status == "CONFLICT"
    assert show()["release_id"] == rels["B"]


def test_stale_gate_champion_conflicts(rels, monkeypatch):
    """If the champion moves between the gate and the write, the write must fail."""
    promote(rels["A"], 0, execute=True)
    real_gate = champ_mod.gate

    def gate_then_someone_else_promotes(release_id):
        decision = real_gate(release_id)  # compared against champion v1
        champ_mod._cas("PROMOTE", rels["A"], 1, "other", "0" * 64, "PROMOTED")  # now v2
        return decision

    monkeypatch.setattr(champ_mod, "gate", gate_then_someone_else_promotes)
    r = promote(rels["B"], 2, execute=True)  # caller's version matches, gate's does not
    assert r.status == "CONFLICT"
    assert show()["release_id"] == rels["A"]


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_promote_rollback_show(rels, capsys):
    from reviewpulse import cli

    assert cli.main(["promote", "--release-id", rels["A"], "--expected-version", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DRY_RUN"
    argv = ["promote", "--release-id", rels["A"], "--expected-version", "0", "--execute"]
    assert cli.main(argv) == 0
    capsys.readouterr()
    assert cli.main(argv) == 4  # same expected version again -> CONFLICT
    capsys.readouterr()
    assert cli.main(["promote", "--release-id", rels["BPR"], "--expected-version", "1"]) == 3
    capsys.readouterr()
    assert cli.main(["rollback", "--release-id", rels["B"], "--expected-version", "1"]) == 3
    capsys.readouterr()
    assert cli.main(["champion", "show", "--history", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["champion"]["release_id"] == rels["A"]
    assert len(out["history"]) == 2
