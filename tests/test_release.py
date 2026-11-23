"""T08: release packaging and verified loading (REL-T01..05)."""

import hashlib
import json
import shutil
import subprocess
import sys

import jsonschema
import pytest

from conftest import OTHER_KEY, short
from reviewpulse.release.loader import BundleInvalid, load_release
from reviewpulse.release.manifest import compute_release_id, release_manifest_schema
from reviewpulse.release.package import PackageError, package


def _pkg(env, which="pop_run", **kw):
    return package(env[which], **kw)


def _dir(env, manifest):
    return env["home"] / "releases" / manifest["release_id"]


def _rewrite_manifest(path, mutate):
    m = json.loads((path / "manifest.json").read_text())
    mutate(m)
    (path / "manifest.json").write_text(json.dumps(m))
    return m


# --- happy path -----------------------------------------------------------------


@pytest.mark.parametrize("which,kind", [("pop_run", "Pop"), ("bpr_run", "BPR")])
def test_package_and_load(home_env, which, kind):
    m = _pkg(home_env, which)
    jsonschema.validate(m, release_manifest_schema())
    assert m["rec_model"]["kind"] == kind
    assert m["release_id"] == compute_release_id(m)
    assert m["dataset"]["history_cutoff_utc"] == "2023-01-01T00:00:00Z"
    assert m["build"] == {"git_commit": "abc123", "git_dirty": False}
    rel = load_release(m["release_id"])
    assert rel.kind == kind
    # golden: u1 has seen a, b -> recommended c, d
    assert short(rel.recommend("synthetic-u1").item_ids) in (["c", "d"], ["d", "c"])
    assert rel.recommend("never-seen-user").fallback is True
    assert rel.recommend("synthetic-u1").fallback is False


def test_package_is_idempotent(home_env):
    a = _pkg(home_env)
    b = _pkg(home_env)
    assert a["release_id"] == b["release_id"]
    assert len([p for p in (home_env["home"] / "releases").iterdir()]) == 1


def test_seen_items_include_low_ratings_and_stop_at_train_end(home_env):
    m = _pkg(home_env)
    rel = load_release(m["release_id"])
    seen = {k: short(v) for k, v in rel.seen.items()}
    assert seen[rel.keyer("synthetic-u2")] == ["a", "c"]  # c was a 2-star review
    assert seen[rel.keyer("synthetic-u1")] == ["a", "b"]  # valid-window c not included
    assert rel.keyer("synthetic-u4") not in seen  # u4 only appears in test
    fallback = json.loads((_dir(home_env, m) / "pop_fallback.json").read_text())
    assert [[short([i])[0], s] for i, s in fallback] == [["a", 2], ["b", 1], ["c", 1], ["d", 0]]


# --- REL-T01: two fresh processes give identical results ------------------------

SCRIPT = """
import json, sys
from reviewpulse.release.loader import load_release
rel = load_release(sys.argv[1])
out = {}
for u in json.loads(sys.argv[2]):
    r = rel.recommend(u)
    out[u] = [r.item_ids, r.scores, r.fallback]
print(json.dumps(out))
"""


def test_rel_t01_two_processes_same_recommendations(home_env):
    m = _pkg(home_env, "bpr_run")
    users = json.dumps(["synthetic-u1", "synthetic-u2", "synthetic-u3", "nobody"])
    outs = [
        subprocess.run(
            [sys.executable, "-c", SCRIPT, m["release_id"], users],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for _ in range(2)
    ]
    assert outs[0] == outs[1]
    assert json.loads(outs[0])["nobody"][2] is True


# --- REL-T02: swapped item mapping ------------------------------------------------


def test_rel_t02_swapped_item_mapping_fails(home_env):
    m = _pkg(home_env, "bpr_run")
    d = _dir(home_env, m)
    items = json.loads((d / "item_mapping.json").read_text())
    items["synthetic-item-z"] = len(items)
    (d / "item_mapping.json").write_text(json.dumps(items))
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "HASH_MISMATCH"


def test_rel_t02_rehashed_mapping_still_fails(home_env):
    m = _pkg(home_env, "bpr_run")
    d = _dir(home_env, m)
    items = json.loads((d / "item_mapping.json").read_text())
    items["synthetic-item-z"] = len(items)
    (d / "item_mapping.json").write_text(json.dumps(items))
    new_sha = hashlib.sha256((d / "item_mapping.json").read_bytes()).hexdigest()

    def fix_hash(man):
        man["files"]["item_mapping"]["sha256"] = new_sha

    _rewrite_manifest(d, fix_hash)
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "RELEASE_ID_MISMATCH"

    # even a full forgery (new release_id + renamed dir) is caught by the shape check
    def fix_id(man):
        man["release_id"] = compute_release_id(man)

    forged = _rewrite_manifest(d, fix_id)
    d.rename(d.parent / forged["release_id"])
    with pytest.raises(BundleInvalid) as exc:
        load_release(forged["release_id"])
    assert exc.value.reason == "MAPPING_MISMATCH"


# --- REL-T03: model file hash mismatch --------------------------------------------


@pytest.mark.parametrize("which,model_file", [("bpr_run", "model.pt"), ("pop_run", "pop.json")])
def test_rel_t03_model_hash_mismatch(home_env, which, model_file):
    m = _pkg(home_env, which)
    f = _dir(home_env, m) / model_file
    f.write_bytes(f.read_bytes() + b" ")
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "HASH_MISMATCH"


def test_missing_file_fails(home_env):
    m = _pkg(home_env)
    (_dir(home_env, m) / "seen_items.json").unlink()
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "FILE_MISSING"


def test_unknown_release_fails(home_env):
    with pytest.raises(BundleInvalid) as exc:
        load_release("rel-000000000000")
    assert exc.value.reason == "FILE_MISSING"


# --- REL-T04: missing or wrong user key -------------------------------------------


def test_rel_t04_missing_user_key(home_env, monkeypatch):
    m = _pkg(home_env)
    monkeypatch.delenv("RP_USER_KEY")
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "USER_KEY_UNAVAILABLE"


def test_rel_t04_wrong_user_key(home_env, monkeypatch):
    m = _pkg(home_env)
    monkeypatch.setenv("RP_USER_KEY", OTHER_KEY)
    with pytest.raises(BundleInvalid) as exc:
        load_release(m["release_id"])
    assert exc.value.reason == "USER_KEY_UNAVAILABLE"
    assert OTHER_KEY not in str(exc.value)


# --- REL-T05: no raw user ids anywhere in the release -------------------------------


@pytest.mark.parametrize("which", ["pop_run", "bpr_run"])
def test_rel_t05_no_raw_user_ids(home_env, which):
    m = _pkg(home_env, which)
    files = list(_dir(home_env, m).iterdir())
    assert len(files) == 8
    for f in files:
        data = f.read_bytes()
        assert b"synthetic-u" not in data, f.name
        assert b"test-only-secret" not in data, f.name  # nor the HMAC secret


# --- packaging failure paths --------------------------------------------------------


def test_package_refuses_dirty_tree(home_env, monkeypatch):
    from reviewpulse import tracking

    dirty = {"git_commit": "x", "git_dirty": "true"}
    monkeypatch.setattr(tracking, "git_info", lambda cwd=None: dirty)
    with pytest.raises(PackageError, match="dirty"):
        _pkg(home_env)
    m = _pkg(home_env, allow_dirty=True)
    assert m["build"]["git_dirty"] is True
    load_release(m["release_id"])  # loadable, but the gate must reject it


def test_package_refuses_other_user_key(home_env, monkeypatch):
    monkeypatch.setenv("RP_USER_KEY", OTHER_KEY)
    with pytest.raises(PackageError, match="user_key_id"):
        _pkg(home_env)


def test_package_refuses_unknown_run(home_env):
    with pytest.raises(PackageError, match="not found"):
        package("0" * 32)


def test_package_refuses_changed_split(home_env):
    split = home_env["home"] / "data" / "split" / "manifest.json"
    m = json.loads(split.read_text())
    m["split_sha256"] = "f" * 64
    split.write_text(json.dumps(m))
    with pytest.raises(PackageError, match="split_sha256"):
        _pkg(home_env)
    assert not any((home_env["home"] / "releases").glob("rel-*"))


def test_package_leaves_no_staging_dir_on_failure(home_env, monkeypatch):
    import reviewpulse.release.package as pkg

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pkg, "seen_items_payload", boom)
    with pytest.raises(RuntimeError):
        _pkg(home_env)
    assert list((home_env["home"] / "releases").iterdir()) == []


def test_packaged_schema_copy_matches_spec():
    from pathlib import Path

    from reviewpulse.release.manifest import SCHEMA_DIR

    spec = Path(__file__).parents[1] / "docs" / "specs" / "schemas"
    for f in SCHEMA_DIR.glob("*.json"):
        assert f.read_bytes() == (spec / f.name).read_bytes(), f.name


def test_cli_package(home_env, capsys):
    from reviewpulse import cli

    assert cli.main(["package", "--run-id", home_env["pop_run"]]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["release_id"].startswith("rel-")
    assert cli.main(["package", "--run-id", "missing-run"]) == 1
    shutil.rmtree(home_env["home"] / "releases")
