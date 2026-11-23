"""Recommendation API (spec 05, API-01..03).

Importing this module never reads the champion pointer or loads a model (C-08):
loading happens in the FastAPI lifespan. The service serves exactly the release it
loaded at startup; promoting a new champion takes effect only after a restart (C-07).
Recommendations go through the same rank() used by offline evaluation.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from reviewpulse import paths
from reviewpulse.monitoring.events import EventStore
from reviewpulse.registry.state import read_champion
from reviewpulse.release.loader import BundleInvalid, LoadedRelease, load_release

log = logging.getLogger("reviewpulse.api")

RUNTIME_CONFIG = "configs/runtime.yaml"
RECOMMEND_PATH = "/v1/recommend"
CONTRACT_MAX_TOP_K = 50
LOAD_REASONS = {"USER_KEY_UNAVAILABLE", "SMOKE_FAILED"}  # anything else -> BUNDLE_INVALID


def load_runtime(path: Path | None = None) -> dict[str, Any]:
    path = path or paths.resolve(RUNTIME_CONFIG)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("runtime") or {}
    max_top_k = int(raw.get("max_top_k", CONTRACT_MAX_TOP_K))
    if not 1 <= max_top_k <= CONTRACT_MAX_TOP_K:
        raise ValueError(f"runtime.max_top_k must be within 1..{CONTRACT_MAX_TOP_K}")
    return {
        "max_top_k": max_top_k,
        "min_events": int(raw.get("min_events", 20)),
        "host": str(raw.get("host", "127.0.0.1")),
        "port": int(raw.get("port", 8000)),
    }


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: str = Field(min_length=1, max_length=128)
    top_k: int

    @field_validator("user_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user_id must not be blank")
        return v.strip()


class ApiException(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class ServiceState:
    ready: bool = False
    loaded_release_id: str | None = None
    reason: str | None = "LOADING"
    release: LoadedRelease | None = None
    runtime: dict[str, Any] = field(default_factory=dict)

    def readiness(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "loaded_release_id": self.loaded_release_id,
            "reason": None if self.ready else self.reason,
        }


def load_champion_into(state: ServiceState) -> None:
    """Load exactly the release the champion pointer names; never fall back to another."""
    state.ready, state.reason = False, "LOADING"
    champion = read_champion()
    release_id = champion["release_id"]
    if release_id is None:
        state.reason = "NO_CHAMPION"
        return
    try:
        release = load_release(release_id)
    except BundleInvalid as exc:
        state.reason = exc.reason if exc.reason in LOAD_REASONS else "BUNDLE_INVALID"
        log.warning("champion %s failed to load: %s", release_id, exc.reason)
        return
    state.release = release
    state.loaded_release_id = release.release_id
    state.ready, state.reason = True, None
    log.info("loaded champion %s (%s)", release.release_id, release.kind)


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    rid = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    body = {"request_id": rid, "error": {"code": code, "message": message}}
    return JSONResponse(status_code=status, content=body)


def create_app(background_load: bool = False, events: EventStore | None = None) -> FastAPI:
    state = ServiceState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.runtime = load_runtime()
        app.state.events = events or EventStore.default()
        if background_load:
            # /health answers while the bundle loads; /ready reports LOADING meanwhile
            app.state.load_task = asyncio.create_task(asyncio.to_thread(load_champion_into, state))
        else:
            load_champion_into(state)
        yield

    app = FastAPI(title="ReviewPulse Recommendation API", version="1.2.0", lifespan=lifespan)
    app.state.service = state

    @app.middleware("http")
    async def recommend_events(request: Request, call_next):
        if request.url.path != RECOMMEND_PATH:
            return await call_next(request)
        request.state.request_id = str(uuid.uuid4())
        request.state.event = {}
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error in %s", RECOMMEND_PATH)
            response = _error(request, 500, "INTERNAL_ERROR", "internal error")
            request.state.event["error_code"] = "INTERNAL_ERROR"
        latency_ms = (time.perf_counter() - start) * 1000.0
        source = (
            "synthetic_demo" if request.headers.get("X-RP-Source") == "synthetic_demo" else "api"
        )
        ev = request.state.event
        app.state.events.write(
            {
                "request_id": request.state.request_id,
                "loaded_release_id": state.loaded_release_id,
                "source": source,
                "user_key_hash": ev.get("user_key_hash"),
                "top_k": ev.get("top_k"),
                "candidate_count": ev.get("candidate_count"),
                "returned_item_count": ev.get("returned_item_count"),
                "fallback_used": ev.get("fallback_used"),
                "fusion_used": ev.get("fusion_used"),
                "latency_ms": latency_ms,
                "http_status": response.status_code,
                "error_code": ev.get("error_code"),
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        # never echo the request body: it may contain the raw user_id
        fields = sorted(
            {".".join(str(p) for p in e.get("loc", ())[1:]) or "body" for e in exc.errors()}
        )
        if hasattr(request.state, "event"):
            request.state.event["error_code"] = "INVALID_ARGUMENT"
        return _error(request, 422, "INVALID_ARGUMENT", f"invalid fields: {', '.join(fields)}")

    @app.exception_handler(ApiException)
    async def _api_error(request: Request, exc: ApiException):
        if hasattr(request.state, "event"):
            request.state.event["error_code"] = exc.code
        return _error(request, exc.status, exc.code, exc.message)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        return JSONResponse(status_code=200 if state.ready else 503, content=state.readiness())

    @app.post(RECOMMEND_PATH)
    def recommend(body: RecommendRequest, request: Request) -> dict[str, Any]:
        max_top_k = state.runtime.get("max_top_k", CONTRACT_MAX_TOP_K)
        if not 1 <= body.top_k <= max_top_k:
            raise ApiException(422, "INVALID_ARGUMENT", f"top_k must be within 1..{max_top_k}")
        ev = request.state.event
        ev["top_k"] = body.top_k
        release = state.release
        if not state.ready or release is None:
            raise ApiException(503, "MODEL_NOT_READY", f"no release loaded ({state.reason})")
        user_key = release.keyer(body.user_id)
        ev["user_key_hash"] = user_key
        result = release.recommend_key(user_key, k=body.top_k)
        items = [
            {"item_id": i, "rank": r, "score": s}
            for i, r, s in zip(result.item_ids, result.ranks, result.scores, strict=True)
        ]
        ev.update(
            candidate_count=result.candidate_count,
            returned_item_count=len(items),
            fallback_used=result.fallback,
            fusion_used=False,
        )
        return {
            "request_id": request.state.request_id,
            "loaded_release_id": state.loaded_release_id,
            "items": items,
            "fallback_used": result.fallback,
            "fusion_used": False,
            "candidate_count": result.candidate_count,
            "empty_reason": None if items else "NO_CANDIDATES",
        }

    return app


app = create_app(background_load=True)
