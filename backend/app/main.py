"""FastAPI app for AI Collection Studio (Phase 1 vertical slice)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import REPO_ROOT, Settings, load_settings
from .providers.base import ProviderError
from .providers.model_registry import ModelRegistry
from .services.category_distribution import (
    CategoryDistribution,
    DistributionError,
    assign_slots,
    compliance,
    default_counts,
    resolve_distribution,
)
from .services.connection_check import ConnectionInputError, ConnectionService
from .services.cost_guard import CostGuard
from .services.credentials import CredentialStore, KeyringCredentialStore, NullCredentialStore
from .services.generation import GenerateCardRequest, GenerationFailure, GenerationService
from .services.comparison import ComparisonService
from .services.db import Db
from .services.exporter import ExportError, Exporter
from .services.imports import ImportError_, import_sample
from .services.jobs import (
    ApproveVariantRequest, ContinueRequest, JobCreateRequest, JobFailure, JobService, JobStore,
    PatchCardRequest, PlanOnlyRequest, RegenerateRequest, ReviewRequest,
)
from .services.prompt_library import LibraryError, PromptLibrary
from .services.presets import PresetError, PresetStore
from .services.planning import supported_genres
from .services.prompt_compiler import check_wishes
from .services.security import RateLimiter, install_log_redaction, is_allowed_host, is_allowed_origin, new_session_token, token_matches
from .services.reference_manager import REF_TYPES, ReferenceError_, ReferenceManager, is_valid_ref_id
from .services.storage import ArtifactStore, is_valid_id


class PromptSaveRequest(BaseModel):
    name: str
    text: str
    author: str = "Local User"
    note: str = Field(default="", max_length=200)


class PromptRestoreRequest(BaseModel):
    version: int
    author: str = "Local User"


class TemplateCreateRequest(BaseModel):
    name: str
    payload: dict[str, Any]


class ComparisonRefRequest(BaseModel):
    ref_id: str | None = None


class ExportRequest(BaseModel):
    job_id: str
    variant_id: str | None = None
    include_unapproved: bool = False
    allow_mock: bool = False


class SelectionItem(BaseModel):
    job_id: str
    card_id: str
    artifact_id: str


class SelectionExportRequest(BaseModel):
    collection: str = Field(default="", max_length=80)
    genre: str = Field(default="", max_length=80)
    set_type: Literal["standard", "grand"] = "standard"
    items: list[SelectionItem] = Field(default_factory=list, max_length=200)


class CollectionExportRequest(BaseModel):
    collection: str = "Movie Collection"
    include_unapproved: bool = False
    allow_mock: bool = False
    allow_partial: bool = False


class ConnectionSaveRequest(BaseModel):
    values: dict[str, Any]
    test: bool = False


class BatchJobRequest(JobCreateRequest):
    genre: str = "batch"  # ignored: one job is created per entry of `genres`
    genres: list[str]


def _err(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def create_app(
    settings: Settings | None = None,
    *,
    gemini_transport: httpx.AsyncBaseTransport | None = None,
    cloudflare_transport: httpx.AsyncBaseTransport | None = None,
    credentials: CredentialStore | None = None,
    enforce_local: bool = False,
) -> FastAPI:
    install_log_redaction()
    s = settings or load_settings()
    creds: CredentialStore = credentials if credentials is not None else NullCredentialStore()  # tests never touch the real vault
    registry = ModelRegistry(s, gemini_transport=gemini_transport, cloudflare_transport=cloudflare_transport,
                             credentials=creds)
    guard = CostGuard(s)
    store = ArtifactStore(s.artifact_root)
    refs = ReferenceManager(s.reference_root, s.max_upload_bytes)
    shared_db = Db(s.database_path)
    library = PromptLibrary(shared_db)
    presets = PresetStore(shared_db)
    service = GenerationService(s, registry, guard, store, refs, library)
    registry.seed_live_from(store.list(200))
    job_store = JobStore(s.database_path)
    jobs = JobService(s, service, guard, refs, job_store)
    job_store.recover_interrupted()  # never auto-resumes paid work after a restart
    comparison = ComparisonService(job_store, refs)
    exporter = Exporter(s, jobs, shared_db)

    app = FastAPI(title="AI Collection Studio", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if enforce_local:
        # Whole-API guard for the real app (the test client is exempt): a DNS-rebinding page could otherwise reach paid
        # endpoints under its own Host name, and a cross-site form could post to them. Host must be loopback; mutating
        # requests that carry an Origin must come from a loopback origin.
        @app.middleware("http")
        async def _local_only_api(request: Request, call_next):
            if not is_allowed_host(request.headers.get("host")):
                return JSONResponse(status_code=403, content={"detail": {"code": "forbidden_host",
                                    "message": "This application only answers requests addressed to the local computer."}})
            if request.method not in ("GET", "HEAD", "OPTIONS") and not is_allowed_origin(request.headers.get("origin")):
                return JSONResponse(status_code=403, content={"detail": {"code": "forbidden_origin",
                                    "message": "Requests from other websites are not accepted."}})
            return await call_next(request)

    # Never echo submitted values (a pasted secret) back in 422 responses.
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    # ------------------------------------------------------------ connections
    session_token = new_session_token()
    conn_db = Db(s.database_path)
    connections = ConnectionService(s, creds, conn_db, registry, cloudflare_transport=cloudflare_transport)
    test_limiter = RateLimiter(limit=6, window_s=60.0)

    def local_only(request: Request) -> None:
        if not is_allowed_host(request.headers.get("host")):
            raise _err(403, "forbidden_host", "This endpoint is only available on localhost.")
        if not is_allowed_origin(request.headers.get("origin")):
            raise _err(403, "forbidden_origin", "Cross-origin requests are not allowed here.")

    def local_mutating(request: Request) -> None:
        local_only(request)
        if not token_matches(session_token, request.headers.get("x-studio-token")):
            raise _err(403, "bad_token", "Missing or invalid session token. Reload the page and try again.")

    def _known(provider: str) -> None:
        if not connections.known(provider):
            raise _err(404, "unknown_provider", "Unknown provider.")

    @app.get("/api/session", dependencies=[Depends(local_only)])
    def session() -> dict[str, str]:
        return {"token": session_token}

    @app.get("/api/connections", dependencies=[Depends(local_only)])
    def connections_get() -> dict[str, Any]:
        return connections.overview()

    @app.put("/api/connections/{provider}", dependencies=[Depends(local_mutating)])
    async def connections_put(provider: str, req: ConnectionSaveRequest) -> dict[str, Any]:
        _known(provider)
        if req.test and not test_limiter.allow(provider):
            raise _err(429, "rate_limited", "Too many connection tests; wait a minute and try again.")
        try:
            connections.save(provider, req.values)
        except ConnectionInputError as exc:
            raise _err(503 if exc.code == "storage_unavailable" else 422, exc.code, exc.message) from None
        if not req.test:
            return connections.provider_view(provider)
        result = await connections.test(provider)
        return {**connections.provider_view(provider), "test": result}

    @app.delete("/api/connections/{provider}", dependencies=[Depends(local_mutating)])
    def connections_delete(provider: str) -> dict[str, Any]:
        _known(provider)
        connections.remove(provider)
        view = connections.provider_view(provider)
        view["removal_note"] = (
            "Removed from Windows Credential Manager on this PC. The same credential is still in .env, "
            "so the provider stays connected." if view["configured"] and view["source"] == "env" else
            "Removed from this PC. This did not revoke the key at the provider; delete it in the provider "
            "dashboard if it was exposed.")
        return view

    @app.post("/api/connections/{provider}/test", dependencies=[Depends(local_mutating)])
    async def connections_test(provider: str) -> dict[str, Any]:
        _known(provider)
        if not test_limiter.allow(provider):
            raise _err(429, "rate_limited", "Too many connection tests; wait a minute and try again.")
        return await connections.test(provider)

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "gemini_configured": registry.effective.gemini_configured,
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
        if not registry.effective.gemini_configured:
            raise _err(400, "not_configured", "No Gemini API key is configured. Add one in Settings > API Connections.")
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
            counts = resolve_distribution(dist, set_type=dist.set_type)
        except DistributionError as exc:
            raise _err(422, exc.code, exc.message) from exc
        compliant, comp_note = compliance(counts, dist.set_type)
        return {
            "mode": dist.mode,
            "compliant": compliant, "compliance_note": comp_note,
            "counts": {str(k): v for k, v in counts.items()},
            "slots": assign_slots(counts),
            "number_of_cards": dist.number_of_cards,
            "gold_cards": counts[5], "set_type": dist.set_type,
            "note": "[2,2,3,3] (scaled for other set sizes) is our design assumption, not a Playrix-mandated ratio."
            if dist.mode == "auto_balanced" else "Custom producer distribution (Cat.1-4; golden count = gold_cards).",
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
    @app.get("/api/genres")
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

    # ---- Phase 4: QA, review, approval, regeneration, card edits
    @app.get("/api/jobs/{job_id}/qa")
    def jobs_qa(job_id: str) -> dict[str, Any]:
        try:
            return jobs.qa_report(job_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/qa")
    def jobs_qa_run(job_id: str) -> dict[str, Any]:
        try:
            return jobs.run_qa(job_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/cards/{card_id}/review")
    def card_review(job_id: str, card_id: str, req: ReviewRequest) -> dict[str, Any]:
        try:
            return jobs.review_card(job_id, card_id, req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/cards/{card_id}/regenerate")
    async def card_regenerate(job_id: str, card_id: str, req: RegenerateRequest) -> dict[str, Any]:
        try:
            return await jobs.regenerate_card(job_id, card_id, req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.patch("/api/jobs/{job_id}/cards/{card_id}")
    def card_patch(job_id: str, card_id: str, req: PatchCardRequest) -> dict[str, Any]:
        try:
            return jobs.patch_card(job_id, card_id, req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/variants/{variant_id}/approve")
    def variant_approve(job_id: str, variant_id: str, req: ApproveVariantRequest | None = None) -> dict[str, Any]:
        try:
            return jobs.approve_variant(job_id, variant_id, req or ApproveVariantRequest())
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/jobs/{job_id}/variants/{variant_id}/unapprove")
    def variant_unapprove(job_id: str, variant_id: str) -> dict[str, Any]:
        try:
            return jobs.unapprove_variant(job_id, variant_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    # ---- Phase 5: Prompt Library, Templates, Reference Compare, Settings
    @app.get("/api/prompts")
    def prompts_list() -> list[dict[str, Any]]:
        return library.listing()

    @app.get("/api/prompts/{name}/versions")
    def prompts_versions(name: str) -> list[dict[str, Any]]:
        try:
            return library.versions(name)
        except LibraryError as exc:
            raise _err(404, exc.code, exc.message) from exc

    @app.post("/api/prompts")
    def prompts_save(req: PromptSaveRequest) -> dict[str, Any]:
        try:
            library.save(req.name, req.text, req.author, req.note)
        except LibraryError as exc:
            raise _err(422, exc.code, exc.message) from exc
        return next(x for x in library.listing() if x["name"] == req.name)

    @app.post("/api/prompts/{name}/restore")
    def prompts_restore(name: str, req: PromptRestoreRequest) -> dict[str, Any]:
        try:
            library.restore(name, req.version, req.author)
        except LibraryError as exc:
            raise _err(404 if exc.code.startswith("unknown") else 422, exc.code, exc.message) from exc
        return next(x for x in library.listing() if x["name"] == name)

    @app.get("/api/templates")
    def templates_list() -> list[dict[str, Any]]:
        return presets.list()

    @app.post("/api/templates")
    def templates_create(req: TemplateCreateRequest) -> dict[str, Any]:
        try:
            return presets.create(req.name, req.payload)
        except PresetError as exc:
            raise _err(409 if exc.code == "duplicate_name" else 422, exc.code, exc.message) from exc

    @app.delete("/api/templates/{preset_id}")
    def templates_delete(preset_id: int) -> dict[str, bool]:
        try:
            return {"deleted": presets.delete(preset_id)}
        except PresetError as exc:
            raise _err(409, exc.code, exc.message) from exc

    @app.get("/api/jobs/{job_id}/comparison")
    def comparison_grid(job_id: str) -> dict[str, Any]:
        try:
            return comparison.grid(job_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/jobs/{job_id}/cards/{card_id}/comparison")
    def comparison_card(job_id: str, card_id: str) -> dict[str, Any]:
        try:
            return comparison.card(job_id, card_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.patch("/api/jobs/{job_id}/cards/{card_id}/comparison-reference")
    def comparison_set(job_id: str, card_id: str, req: ComparisonRefRequest) -> dict[str, Any]:
        try:
            return comparison.set_reference(job_id, card_id, req.ref_id)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/settings")
    def settings_view() -> dict[str, Any]:
        s = registry.effective  # credential manager > .env
        def rel(path: Any) -> str:
            try:
                return path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                return path.name
        return {
            "version": __version__, "spec_version": "1.9",
            "providers": {
                "gemini": {"enabled": s.gemini_api_enabled, "key_configured": bool(s.gemini_api_key.strip()),
                           "default_model": s.default_model, "est_cost_per_image_usd": s.gemini_est_cost_per_image_usd},
                "cloudflare": {"token_set": bool(s.cloudflare_api_token.strip()), "account_id_set": bool(s.cloudflare_account_id.strip()),
                               "configured": s.cloudflare_configured},
                "openai": {"enabled": s.openai_image_enabled, "paid_calls_enabled": s.openai_paid_calls_enabled,
                           "key_configured": bool(s.openai_api_key.strip())},
                "mock": {"enabled": s.enable_mock_provider},
            },
            "budget": asdict(guard.summary()),
            "limits": {"max_card_retries": s.max_card_retries, "max_regenerations_per_card": s.max_regenerations_per_card,
                       "max_parallel_generations": 1, "max_images_per_job": s.max_images_per_job,
                       "max_refs_per_request": s.max_refs_per_request, "max_upload_mb": s.max_upload_bytes // (1024 * 1024)},
            "storage": {"data": rel(s.data_root), "artifacts": rel(s.artifact_root), "references": rel(s.reference_root),
                        "database": rel(s.database_path), "exports": rel(s.data_root / "exports")},
            "how_to_change": "Connect providers in Settings > API Connections (kept in Windows Credential Manager); for development you can still use the .env file. API keys are never shown after saving.",
        }

    # ---- Phase 6: collection workflow, export, imports
    def _export_err(exc: ExportError) -> HTTPException:
        return _err(exc.status, exc.code, exc.message, **exc.extra)

    @app.get("/api/collection/overview")
    def collection_overview(collection: str = "Movie Collection") -> dict[str, Any]:
        return exporter.overview(collection)

    @app.post("/api/collection/batch")
    async def collection_batch(req: BatchJobRequest) -> list[dict[str, Any]]:
        try:
            base = JobCreateRequest(**{k: v for k, v in req.model_dump().items() if k != "genres"})
            return await jobs.create_batch(base, req.genres)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.post("/api/collection/export")
    def collection_export(req: CollectionExportRequest) -> dict[str, Any]:
        try:
            return exporter.export_collection(req.collection, include_unapproved=req.include_unapproved,
                                              allow_mock=req.allow_mock, allow_partial=req.allow_partial)
        except ExportError as exc:
            raise _export_err(exc) from exc

    @app.post("/api/exports")
    def exports_create(req: ExportRequest) -> dict[str, Any]:
        try:
            return exporter.export_variant(req.job_id, req.variant_id, include_unapproved=req.include_unapproved,
                                           allow_mock=req.allow_mock)
        except JobFailure as exc:
            raise _job_err(exc) from exc
        except ExportError as exc:
            raise _export_err(exc) from exc

    @app.post("/api/exports/selection")
    def exports_selection(req: SelectionExportRequest) -> dict[str, Any]:
        """Export exactly the starred images of 'My Selection' (may span several jobs). Warns, never blocks, on compliance."""
        try:
            return exporter.export_selection(req.collection, req.genre, req.set_type, [i.model_dump() for i in req.items])
        except ExportError as exc:
            raise _export_err(exc) from exc

    @app.post("/api/plan/test")
    def plan_test(req: PlanOnlyRequest) -> dict[str, Any]:
        """Planner-only test: the real semantic planner, no image provider, no job. A real text call is budgeted."""
        try:
            return jobs.plan_only(req)
        except JobFailure as exc:
            raise _job_err(exc) from exc

    @app.get("/api/exports")
    def exports_list() -> list[dict[str, Any]]:
        return exporter.list()

    @app.get("/api/exports/{export_id}/download")
    def exports_download(export_id: str):
        found = exporter.path(export_id)
        if found is None:
            raise _err(404, "not_found", "Export not found.")
        return FileResponse(found[0], media_type="application/zip", filename=found[1])

    @app.post("/api/imports")
    async def imports_create(file: UploadFile = File(...), provider_label: str = Form(...), model_label: str = Form(...),
                             prompt_note: str = Form(""), object_name: str = Form("")) -> dict[str, Any]:
        data = await file.read(s.max_upload_bytes + 1)
        try:
            return import_sample(store, data, provider_label=provider_label, model_label=model_label,
                                 prompt_note=prompt_note, object_name=object_name, max_bytes=s.max_upload_bytes)
        except ImportError_ as exc:
            raise _err(400, exc.code, exc.message) from exc

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

    dist = REPO_ROOT / "frontend" / "dist"
    if dist.is_dir():  # single-process mode: start.bat builds the UI and serves it from the backend
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise _err(404, "not_found", "Unknown API route.")
            target = (dist / full_path).resolve()
            if full_path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})  # hashed assets cache; the shell must not

    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app(credentials=KeyringCredentialStore(), enforce_local=True)
