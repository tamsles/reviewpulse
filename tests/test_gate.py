"""T09: quality gate (GATE-T01..04) plus check-order, C-05 and threshold handling."""

import inspect
import json
import sqlite3

import pytest
import yaml

from reviewpulse.registry import gate as gate_mod
from reviewpulse.registry.gate import QualityConfigError, gate
from reviewpulse.registry.state import connect, registry_path
from reviewpulse.release.manifest import ReleaseIdError
from reviewpulse.release.package import package


def _quality(home, **overrides):
    path = home / "configs" / "quality.yaml"
    q = yaml.safe_load(path.read_text())
    q["quality"]["recommendation"].update(overrides)
    path.write_text(yaml.safe_dump(q))


def _set_champion(release_id, version=1):
    conn = connect()
    conn.execute(
        "UPDATE champion SET release_id=?, version_counter=?,"
        " promoted_at_utc='2026-11-23T00:00:00Z', actor='test', gate_decision_sha256=?"
        " WHERE id=1",
        (release_id, version, "0" * 64),
    )
    conn.close()


@pytest.fixture
def releases(home_env):
    pop = package(home_env["pop_run"])
    bpr = package(home_env["bpr_run"])
    return {**home_env, "pop": pop["release_id"], "bpr": bpr["release_id"]}


# --- C-05: the gate has no score inputs ---------------------------------------------


def test_c05_gate_accepts_only_release_id():
    assert list(inspect.signature(gate).parameters) == ["release_id"]


# --- GATE-T03: Pop passes, BPR that does not beat Pop is rejected --------------------


def test_gate_t03_pop_passes(releases):
    d = gate(releases["pop"])
    assert d.passed and d.reason is None
    results = {c["order"]: c["result"] for c in d.checks}
    assert results == {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS", 5: "SKIPPED", 6: "SKIPPED"}
    assert d.thresholds_configured


def test_gate_t03_bpr_not_better_than_pop(releases):
    d = gate(releases["bpr"])
    assert d.status == "REJECTED"
    assert d.reason == "NOT_BETTER_THAN_POP"
    assert d.inputs["pop_baseline_release_id"] == releases["pop"]


def test_gate_bpr_passes_when_margin_allows_and_it_beats_pop(releases, monkeypatch):
    # force the candidate to look better than the baseline by lowering Pop's numbers
    real = gate_mod.find_pop_baseline

    def weaker_pop(cand):
        base = real(cand)
        base.evaluation = {**base.evaluation, "metrics": {**base.evaluation["metrics"]}}
        base.evaluation["metrics"]["ndcg_at_10"] = 0.0
        return base

    monkeypatch.setattr(gate_mod, "find_pop_baseline", weaker_pop)
    _quality(releases["home"], min_abs_ndcg=0.0)
    d = gate(releases["bpr"])
    assert d.passed, d.detail


# --- GATE-T01: below threshold is rejected and the champion table does not change -------


def test_gate_t01_below_min_rejected_champion_bytes_unchanged(releases):
    connect().close()  # initialise registry with the empty champion row
    before = registry_path().read_bytes()
    _quality(releases["home"], min_abs_ndcg=1.01)  # golden Pop ndcg is exactly 1.0
    d = gate(releases["pop"])
    assert d.reason == "BELOW_MIN"
    assert registry_path().read_bytes() == before
    with sqlite3.connect(registry_path()) as conn:
        row = conn.execute("SELECT release_id, version_counter FROM champion").fetchone()
    assert row == (None, 0)


# --- GATE-T02: protocol differs from the Pop baseline ------------------------------------


def test_gate_t02_protocol_mismatch(home_env):
    home = home_env["home"]
    bpr = package(home_env["bpr_run"])["release_id"]
    # change the evaluation protocol, retrain + package Pop under it
    split_yaml = home / "configs" / "split.yaml"
    cfg = yaml.safe_load(split_yaml.read_text())
    cfg["evaluation"]["cold_start_report"] = "none"
    split_yaml.write_text(yaml.safe_dump(cfg))
    from reviewpulse.train import train

    (pop_run,) = train("Pop", "configs/split.yaml", "configs/train.yaml")
    pop = package(pop_run["run_id"])
    assert (
        pop["dataset"]["protocol_sha256"]
        != json.loads((home / "releases" / bpr / "manifest.json").read_text())["dataset"][
            "protocol_sha256"
        ]
    )
    d = gate(bpr)
    assert d.reason == "PROTOCOL_MISMATCH"


def test_gate_no_pop_baseline_is_protocol_mismatch(home_env):
    bpr = package(home_env["bpr_run"])["release_id"]
    d = gate(bpr)
    assert d.reason == "PROTOCOL_MISMATCH"
    assert "no loadable Pop baseline" in d.detail


# --- GATE-T04: dirty build -----------------------------------------------------------------


def test_gate_t04_dirty_build_rejected(home_env, monkeypatch):
    from reviewpulse import tracking

    dirty = {"git_commit": "x", "git_dirty": "true"}
    monkeypatch.setattr(tracking, "git_info", lambda cwd=None: dirty)
    rid = package(home_env["pop_run"], allow_dirty=True)["release_id"]
    d = gate(rid)
    assert d.reason == "DIRTY_BUILD"


# --- other checks ------------------------------------------------------------------------


def test_gate_bundle_invalid(releases):
    (releases["home"] / "releases" / releases["pop"] / "eligible_items.json").write_text("[]")
    d = gate(releases["pop"])
    assert d.reason == "BUNDLE_INVALID"
    assert d.checks[-1]["result"] == "FAIL"


def test_gate_rejects_malformed_release_id_without_writing(releases):
    with pytest.raises(ReleaseIdError):
        gate("../../etc")
    assert not (releases["home"] / "state" / "gate").exists()


def test_gate_same_release_vs_itself_is_not_a_regression(releases):
    _set_champion(releases["pop"])
    d = gate(releases["pop"])
    assert d.passed
    assert {c["order"]: c["result"] for c in d.checks}[6] == "PASS"


def _patch_champion_load(monkeypatch, mutate):
    """Second load_release call inside gate() is the champion; mutate its evaluation."""
    real_load = gate_mod.load_release
    calls = []

    def load(rid):
        rel = real_load(rid)
        calls.append(rid)
        if len(calls) == 2:
            rel.evaluation = mutate(json.loads(json.dumps(rel.evaluation)))
        return rel

    monkeypatch.setattr(gate_mod, "load_release", load)


def test_gate_regression_detected(releases, monkeypatch):
    _set_champion(releases["pop"])

    def better_champion(ev):
        ev["metrics"]["ndcg_at_10"] += 1e-6  # champion slightly better, max_regression = 0
        return ev

    _patch_champion_load(monkeypatch, better_champion)
    d = gate(releases["pop"])
    assert d.reason == "REGRESSION"


def test_gate_split_mismatch_vs_champion(releases, monkeypatch):
    _set_champion(releases["pop"])
    _patch_champion_load(monkeypatch, lambda ev: {**ev, "split_sha256": "f" * 64})
    d = gate(releases["pop"])
    assert d.reason == "SPLIT_MISMATCH"


def test_gate_decision_file_written(releases):
    d = gate(releases["bpr"])
    path = releases["home"] / "state" / "gate" / f"{releases['bpr']}.json"
    body = json.loads(path.read_text())
    assert body["reason"] == "NOT_BETTER_THAN_POP"
    assert len(d.decision_sha256) == 64
    assert body["inputs"]["quality_sha256"]


# --- thresholds ----------------------------------------------------------------------------


def test_null_thresholds_are_skipped_and_flagged(releases):
    _quality(releases["home"], min_abs_ndcg=None, min_improvement=None, max_regression=None)
    d = gate(releases["pop"])
    assert d.passed
    assert not d.thresholds_configured
    assert {c["order"]: c["result"] for c in d.checks}[4] == "SKIPPED"


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_abs_ndcg": -0.1},
        {"min_improvement": "high"},
        {"max_regression": float("nan")},
        {"primary_metric": "rec/val/recall_at_10"},
        {"reject_dirty_build": False},
    ],
)
def test_invalid_quality_config_raises(releases, overrides):
    _quality(releases["home"], **overrides)
    with pytest.raises(QualityConfigError):
        gate(releases["pop"])


def test_cli_gate_exit_codes(releases, capsys):
    from reviewpulse import cli

    assert cli.main(["gate", "--release-id", releases["pop"]]) == 0
    assert cli.main(["gate", "--release-id", releases["bpr"]]) == 3
    assert cli.main(["gate", "--release-id", "nope"]) == 1
