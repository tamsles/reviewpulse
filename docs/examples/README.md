# Synthetic examples

Everything here is hand-made. None of it is real model output or an experiment result.

| File | Describes |
|---|---|
| `synthetic_reviews.jsonl` | 13 synthetic reviews, identical to `tests/fixtures/`; the golden case is in [`../design.md`](../design.md#golden-case-hand-computed-tested-exactly) |
| `source.example.yaml` | Shape of the data source config |
| `split.example.yaml` | Time boundaries and evaluation policy; these dates are used with the synthetic data |
| `recommend-request.example.json` / `recommend-response.example.json` | u1 has seen a and b, so the only candidates are c and d (`candidate_count=2`); scores are illustrative |
| `champion-state.example.json` | Initial state before any champion (`version_counter=0`) |
| `quality.example.yaml` | Shape of the gate config (thresholds left null) |
| `release_manifest.example.json` | Shape of a release manifest; the all-zero hashes are placeholders and will not pass loading |
| `event.example.json` | A pseudonymized request event from a synthetic source |
