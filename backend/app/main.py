"""FastAPI app for AI Collection Studio (Phase 1 vertical slice)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import __version__
from .config import Settings, load_settings
from .providers.base import ProviderError
from .providers.model_registry import ModelRegistry
from .services.category_distribution import (
    CategoryDistribution,
    DistributionError,
    assign_slots,
    default_counts,
    resolve_distribution,
)
from .services.cost_guard import CostGuard
from .services.generation import GenerateCardRequest, GenerationFailure, GenerationService
from .services.jobs import ContinueRequest, JobCreateRequest, JobFailure, JobService, JobStore
from .services.planning import supported_genres
from .services.prompt_compiler import check_wishes
from .services.reference_manager import REF_TYPES, ReferenceError_, ReferenceManager, is_valid_ref_id
from .services.storage import ArtifactStore, is_valid_id


def _err(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def create_app(
    settings: Settings | None = None,
    *,
    gemini_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    s = settings or load_settings()
    registry = ModelRegistry(s, gemini_transport=gemini_transport)
    guard = CostGuard(s)
    store = ArtifactStore(s.artifact_root)
    refs = ReferenceManager(s.reference_root, s.max_upload_bytes)
    service = GenerationService(s, registry, guard, store, refs)
    registry.seed_live_from(store.list(200))
    job_store = JobStore(s.database_path)
    jobs = JobService(s, service, guard, refs, job_store)
    job_store.recover_interrupted()  # never auto-resumes paid work after a restart

    app = FastAPI(title="AI Collection Studio", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "gemini_configured": s.gemini_configured,
            "openai_enabled": s.openai_image_enabled,
            "mock_enabled": s.enable_mock_provider,
        }

    # ------------------------------------------------------------------ models
    @app.get("/api/models")
    def models() -> dict[str, Any]:
        return {
            "models": [asdict(m) for m in registry.models()],
            "defaults": {
                "provider": s.default_provider,
                "model": s.default_model,
                "category_distribution": {str(k): v for k, v in default_counts().items()},
            },
            "budget": asdict(guard.summary()),
            "availability": {
                "checked_at": registry.availability_checked_at,
                "error": registry.availability_error,
            },
            "limits": {
                "max_refs_per_request": s.max_refs_per_request,
                "max_upload_bytes": s.max_upload_bytes,
                "ref_types": list(REF_TYPES),
            },
        }

    @app.post("/api/models/check-availability")
    async def check_availability() -> dict[str, Any]:
        if not s.gemini_configured:
            raise _err(400, "not_configured", "GEMINI_API_KEY is not set in your local .env.")
        try:
            return await registry.check_gemini_availability()
        except ProviderError as exc:
            raise _err(502, exc.code, exc.message) from exc

    # -------------------------------------------------------------- references
    @app.post("/api/references")
    async def upload_reference(file: UploadFile = File(...), ref_type: str = Form("style")):
        data = await file.read(s.max_upload_bytes + 1)
        try:
            return refs.save_upload(file.filename or "upload", data, ref_type)
        except ReferenceError_ as exc:
            raise _err(400, exc.code, exc.message) from exc

    @app.get("/api/references")
    def list_references() -> list[dict[str, Any]]:
        return refs.list()

    @app.get("/api/references/{ref_id}/thumbnail")
    def reference_thumb(ref_id: str):
        if not is_valid_ref_id(ref_id) or (p := refs.image_path(ref_id, thumb=True)) is None:
            raise _err(404, "not_found", "Reference not found.")
        return FileResponse(p, media_type="image/png")

    @app.get("/api/references/{ref_id}/image")
    def reference_image(ref_id: str):
        if not is_valid_ref_id(ref_id) or (p := refs.image_path(ref_id)) is None:
            raise _err(404, "not_found", "Reference not found.")
        return FileResponse(p, media_type="image/png")

    @app.delete("/api/references/{ref_id}")
    def delete_reference(ref_id: str) -> dict[str, bool]:
        if not is_valid_ref_id(ref_id):
            raise _err(404, "not_found", "Reference not found.")
        return {"deleted": refs.delete(ref_id)}

    # ------------------------------------------------------------- generation
    @app.post("/api/prompt/preview")
    def prompt_preview(req: GenerateCardRequest) -> dict[str, Any]:
        try:
            return service.preview(req)
        except GenerationFailure as exc:
            raise _err(exc.status, exc.code, exc.message, **exc.extra) from exc

    @app.post("/api/category-distribution/validate")
    def validate_distribution(dist: CategoryDistribution) -> dict[str, Any]:
        """Authoritative Card Category Distribution check (spec §3.4); free, no provider call."""
        try:
            counts = resolve_distribution(dist)
        except DistributionError as exc:
            raise _err(422, exc.code, exc.message) from exc
        return {
            "mode": dist.mode,
            "counts": {str(k): v for k, v in counts.items()},
            "slots": assign_slots(counts),
            "note": "[2,2,3,3] is our design assumption, not a Playrix-mandated ratio."
            if dist.mode == "auto_balanced" else "Custom producer distribution.",
        }

    @app.post("/api/wishes/check")
    def wishes_check(body: dict[str, str]) -> dict[str, list[str]]:
        return {"warnings": check_wishes(body.get("wishes", ""))}

    @app.post("/api/generate-card")
    async def generate_card(req: GenerateCardRequest) -> dict[str, Any]:
        try:
            return await service.generate(req)
        except GenerationFailure as exc:
            raise _err(exc.status, exc.code, exc.message, **exc.extra) from exc

    # -------------------------------------------------------------------- jobs
    def _job_err(exc: JobFailure) -> HTTPException:
        return _err(exc.status, exc.code, exc.message, **exc.extra)

    @app.get("/api/planner/genres")
    def planner_genres() -> list[dict[str, Any]]:
        return supported_genres()

    @app.post("/api/jobs/preview")
    def jobs_preview(req: JobCreateRequest) -> dict[str, Any]:
        try:
            return jobs.preview(req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs")
    async def jobs_create(req: JobCreateRequest) -> dict[str, Any]:
        try:
            return await jobs.create(req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/jobs")
    def jobs_list(limit: int = 30) -> list[dict[str, Any]]:
        return jobs.list(min(max(limit, 1), 100))

    @app.get("/api/jobs/{job_id}")
    def jobs_get(job_id: str) -> dict[str, Any]:
        try:
            return jobs.detail(job_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/jobs/{job_id}/variants")
    def jobs_variants(job_id: str) -> list[dict[str, Any]]:
        try:
            return jobs.detail(job_id)["variants"]
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/jobs/{job_id}/events")
    def jobs_events(job_id: str, after: int = 0) -> list[dict[str, Any]]:
        if job_store.get_job(job_id) is None:
            raise _err(404, "not_found", "Job not found.")
        return job_store.events(job_id, after)

    @app.post("/api/jobs/{job_id}/continue")
    async def jobs_continue(job_id: str, req: ContinueRequest) -> dict[str, Any]:
        try:
            return await jobs.continue_job(job_id, req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/cancel")
    def jobs_cancel(job_id: str) -> dict[str, Any]:
        try:
            return jobs.cancel(job_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    app.state.jobs = jobs  # exposed for tests

    # --------------------------------------------------------------- artifacts
    @app.get("/api/artifacts")
    def list_artifacts(limit: int = 50) -> list[dict[str, Any]]:
        return store.list(min(max(limit, 1), 200))

    @app.get("/api/artifacts/{artifact_id}")
    def artifact_meta(artifact_id: str) -> dict[str, Any]:
        if not is_valid_id(artifact_id) or (m := store.metadata(artifact_id)) is None:
            raise _err(404, "not_found", "Artifact not found.")
        return m

    @app.get("/api/artifacts/{artifact_id}/image")
    def artifact_image(artifact_id: str):
        if not is_valid_id(artifact_id) or (p := store.image_path(artifact_id)) is None:
            raise _err(404, "not_found", "Artifact not found.")
        return FileResponse(p, media_type="image/png")

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
