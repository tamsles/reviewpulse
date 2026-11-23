import json
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_JSONL = FIXTURES / "synthetic_reviews.jsonl"


def read_fixture_rows() -> list[dict]:
    return [json.loads(x) for x in FIXTURE_JSONL.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def source_dict() -> dict:
    return yaml.safe_load((FIXTURES / "source.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def make_source(tmp_path, source_dict):
    """Build a SourceConfig pointing at a tmp copy of the given rows."""
    from reviewpulse.data.contracts import parse_source_config

    def _make(rows: list[dict], name: str = "raw.jsonl", **overrides):
        raw = write_jsonl(tmp_path / name, rows)
        d = json.loads(json.dumps(source_dict))
        d["source"]["raw_path"] = str(raw)
        d["source"].update(overrides)
        return parse_source_config(d)

    return _make
