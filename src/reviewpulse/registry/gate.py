"""Quality gate (spec 04, GATE-01).

`gate(release_id)` takes no score arguments: every number comes from registered
evaluation files inside release bundles (C-05). Checks run in order and the
first failure rejects. The decision is written to state/gate/<release_id>.json.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from reviewpulse import paths
from reviewpulse.io_utils import sha256_file, utc_now_iso, write_json_atomic
from reviewpulse.registry.state import read_champion
from reviewpulse.release.loader import BundleInvalid, LoadedRelease, load_release
from reviewpulse.release.manifest import MANIFEST_FILE, check_release_id, releases_dir

QUALITY_CONFIG = "configs/quality.yaml"
GATE_DIR = "state/gate"
THRESHOLDS = ("min_abs_ndcg", "min_improvement", "max_regression")
HASH_KEYS = ("split_sha256", "protocol_sha256", "ground_truth_sha256")
METRIC = "ndcg_at_10"


class QualityConfigError(ValueError):
    """Raised for an invalid quality.yaml."""


@dataclass
class GateDecision:
    release_id: str
    status: str  # PASSED | REJECTED
    reason: str | None
    detail: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, float | None] = field(default_factory=dict)
    thresholds_configured: bool = False
    created_at_utc: str = ""
    decision_path: str = ""
    decision_sha256: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_quality(path: Path | None = None) -> dict[str, float | None]:
    path = path or paths.resolve(QUALITY_CONFIG)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rec = raw["quality"]["recommendation"]
    except (OSError, KeyError, TypeError) as exc:
        raise QualityConfigError(f"cannot read quality config {path}: {exc}") from exc
    if rec.get("primary_metric") != f"rec/val/{METRIC}" or rec.get("direction") != "maximize":
        raise QualityConfigError("primary_metric must be rec/val/ndcg_at_10 (maximize)")
    for flag in ("pop_exempt_from_beating_pop", "reject_on_split_mismatch", "reject_dirty_build"):
        if rec.get(flag) is not True:
            raise QualityConfigError(f"quality.recommendation.{flag} must be true in v1.2")
    out: dict[str, float | None] = {}
    for name in THRESHOLDS:
        v = rec.get(name)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)):
            raise QualityConfigError(f"{name} must be a number or null")
        if v is not None and not (math.isfinite(v) and v >= 0):
            raise QualityConfigError(f"{name} must be finite and >= 0")
        out[name] = None if v is None else float(v)
    return out


class _Reject(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def _metrics_ok(evaluation: dict[str, Any]) -> bool:
    m = evaluation.get("metrics") or {}
    vals = [m.get(k) for k in ("ndcg_at_10", "recall_at_10", "coverage_at_10")]
    return all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1
        for v in vals
    )


def find_pop_baseline(candidate: LoadedRelease) -> LoadedRelease | None:
    """A loadable Pop release whose evaluation has the same three hashes."""
    want = {k: candidate.evaluation[k] for k in HASH_KEYS}
    root = releases_dir()
    if not root.is_dir():
        return None
    for d in sorted(root.glob("rel-*")):
        try:
            man = json.loads((d / MANIFEST_FILE).read_text(encoding="utf-8"))
            if man["rec_model"]["kind"] != "Pop":
                continue
            rel = load_release(d.name)
        except (OSError, KeyError, ValueError, BundleInvalid):
            continue
        if all(rel.evaluation.get(k) == v for k, v in want.items()):
            return rel
    return None


def gate(release_id: str) -> GateDecision:
    check_release_id(release_id)  # raises ReleaseIdError before anything is written
    thresholds = load_quality()
    decision = GateDecision(
        release_id=release_id,
        status="PASSED",
        reason=None,
        detail="all checks passed",
        thresholds=thresholds,
        thresholds_configured=all(v is not None for v in thresholds.values()),
        created_at_utc=utc_now_iso(),
    )
    quality_path = paths.resolve(QUALITY_CONFIG)
    decision.inputs["quality_sha256"] = sha256_file(quality_path)

    def record(order: int, name: str, result: str, detail: str) -> None:
        decision.checks.append({"order": order, "check": name, "result": result, "detail": detail})

    try:
        # 1. bundle loads completely (REL-02)
        try:
            cand = load_release(release_id)
        except BundleInvalid as exc:
            raise _Reject("BUNDLE_INVALID", str(exc)) from exc
        decision.inputs["manifest_sha256"] = sha256_file(cand.path / MANIFEST_FILE)
        decision.inputs["evaluation_sha256"] = cand.manifest["files"]["evaluation"]["sha256"]
        decision.inputs.update({k: cand.evaluation[k] for k in HASH_KEYS})
        record(1, "bundle_loads", "PASS", cand.kind)

        # 2. clean build
        if cand.manifest["build"]["git_dirty"]:
            raise _Reject("DIRTY_BUILD", "release was built from a dirty working tree")
        record(2, "clean_build", "PASS", cand.manifest["build"]["git_commit"])

        # 3. validation evaluation with finite metrics
        if cand.evaluation.get("eval_split") != "validation" or not _metrics_ok(cand.evaluation):
            raise _Reject("INVALID_METRICS", "evaluation must be validation with finite metrics")
        ndcg = float(cand.evaluation["metrics"][METRIC])
        record(3, "valid_metrics", "PASS", f"{METRIC}={ndcg:.6f}")

        # 4. absolute floor
        floor = thresholds["min_abs_ndcg"]
        if floor is None:
            record(4, "min_abs_ndcg", "SKIPPED", "threshold not configured")
        elif ndcg < floor:
            raise _Reject("BELOW_MIN", f"{METRIC}={ndcg:.6f} < min_abs_ndcg={floor}")
        else:
            record(4, "min_abs_ndcg", "PASS", f"{ndcg:.6f} >= {floor}")

        # 5. non-Pop must beat a Pop baseline evaluated under the same protocol
        if cand.kind == "Pop":
            record(5, "beats_pop", "SKIPPED", "Pop is the baseline")
        else:
            base = find_pop_baseline(cand)
            if base is None:
                raise _Reject(
                    "PROTOCOL_MISMATCH",
                    "no loadable Pop baseline release with the same split/protocol/ground truth",
                )
            pop_ndcg = float(base.evaluation["metrics"][METRIC])
            decision.inputs["pop_baseline_release_id"] = base.release_id
            decision.inputs["pop_baseline_evaluation_sha256"] = base.manifest["files"][
                "evaluation"
            ]["sha256"]
            margin = thresholds["min_improvement"]
            if margin is None:
                record(5, "beats_pop", "SKIPPED", f"threshold not configured (pop={pop_ndcg:.6f})")
            elif not ndcg > pop_ndcg + margin:
                raise _Reject(
                    "NOT_BETTER_THAN_POP",
                    f"{METRIC}={ndcg:.6f} <= pop {pop_ndcg:.6f} + min_improvement {margin}",
                )
            else:
                record(5, "beats_pop", "PASS", f"{ndcg:.6f} > {pop_ndcg:.6f} + {margin}")

        # 6. no regression against the current champion (same split only)
        champ = read_champion()
        decision.inputs["champion_release_id"] = champ["release_id"]
        decision.inputs["champion_version"] = champ["version_counter"]
        if champ["release_id"] is None:
            record(6, "vs_champion", "SKIPPED", "no champion yet")
        else:
            try:
                cur = load_release(champ["release_id"])
            except BundleInvalid as exc:
                raise _Reject("BUNDLE_INVALID", f"current champion cannot load: {exc}") from exc
            for key in ("split_sha256", "protocol_sha256"):
                if cur.evaluation[key] != cand.evaluation[key]:
                    raise _Reject("SPLIT_MISMATCH", f"{key} differs from champion")
            champ_ndcg = float(cur.evaluation["metrics"][METRIC])
            allowed = thresholds["max_regression"]
            if allowed is None:
                record(6, "vs_champion", "SKIPPED", "threshold not configured")
            elif ndcg < champ_ndcg - allowed:
                raise _Reject(
                    "REGRESSION",
                    f"{METRIC}={ndcg:.6f} < champion {champ_ndcg:.6f} - max_regression {allowed}",
                )
            else:
                record(6, "vs_champion", "PASS", f"{ndcg:.6f} >= {champ_ndcg:.6f} - {allowed}")
    except _Reject as rej:
        decision.status, decision.reason, decision.detail = "REJECTED", rej.reason, rej.detail
        order = len(decision.checks) + 1
        record(order, "rejected", "FAIL", f"{rej.reason}: {rej.detail}")

    out = paths.resolve(GATE_DIR) / f"{release_id}.json"
    body = decision.to_dict()
    body.pop("decision_path")
    body.pop("decision_sha256")
    write_json_atomic(body, out)
    decision.decision_path = str(out)
    decision.decision_sha256 = sha256_file(out)
    return decision
