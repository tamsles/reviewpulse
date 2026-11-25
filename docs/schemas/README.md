# Interface contracts

| File | Describes |
|---|---|
| `openapi.v1.yaml` | `/health`, `/ready`, `/v1/recommend` |
| `recommend-request.v1.schema.json` | Recommendation request |
| `normalized-interaction.v1.schema.json` | Normalized interaction row (output of `data prepare`) |
| `monitoring-event.v1.schema.json` | One recommendation request event |
| `champion-state.v1.schema.json` | Export shape of the single-row champion table |
| `release-manifest.v1.2.schema.json` | Release bundle manifest |
| `evaluation-manifest.v1.2.schema.json` | Recommendation evaluation manifest (`evaluation.json`) |

Schemas check structure only. File hashes, mapping shapes, the key fingerprint and end-to-end inference are checked by `src/reviewpulse/release/loader.py` and the tests. See [`../design.md`](../design.md) for the semantics.
