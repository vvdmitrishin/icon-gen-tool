"""Single-card generation orchestration: validate → compile prompt → guard cost → provider →
store PNG + provenance metadata. One request = one image; no batch endpoint exists yet."""
from __future__ import annotations

import asyncio
import hashlib
import io
from datetime import datetime, timezone
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings
from ..providers.base import GenerationRequest, ProviderDisabled, ProviderError
from ..providers.model_registry import ModelInfo, ModelRegistry
from .cost_guard import BudgetExceeded, CostGuard
from .prompt_compiler import MAX_WISHES, CardSpec, CompiledPrompt, check_overrides, compile_card_prompt
from .prompt_library import LibraryError, PromptLibrary
from .reference_manager import ReferenceError_, ReferenceManager
from .storage import ArtifactStore, new_artifact_id


class GenerateCardRequest(BaseModel):
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(default="", max_length=300)
    category: Literal[1, 2, 3, 4, 5]  # 5 = golden (only inside a set job; the single-card endpoint refuses it)
    wishes: str = Field(default="", max_length=MAX_WISHES)
    provider: str
    model: str
    reference_ids: list[str] = Field(default_factory=list)
    reference_policy_accepted: bool = False
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"] = "1:1"
    output_size: int = 1024  # EXACT delivered icon side in px (24..1024); the native image is up/downscaled to it
    confirmed: bool = False  # explicit user confirmation of the shown cost estimate
    context: dict[str, Any] = Field(default_factory=dict)  # e.g. job_id/variant_id/position (provenance)
    repair_note: str = Field(default="", max_length=300)  # regeneration corrections (spec §11.4)
    # Session-only prompt overrides (template name -> text) sent by the browser for single-card calls and previews.
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    # The job's immutable prompt snapshot (set by the job runner only): name -> {text, source, version, base_version}.
    prompt_bundle: dict[str, Any] | None = None
    # Shared Set context from the plan (variant art direction); never overrides the subject or the category rules.
    variant_title: str = Field(default="", max_length=120)
    variant_concept: str = Field(default="", max_length=400)
    rationale: str = Field(default="", max_length=300)

    def card(self) -> CardSpec:
        return CardSpec(
            collection=self.collection,
            genre=self.genre,
            object_name=self.object_name,
            object_description=self.object_description,
            category=self.category,
            variant_title=self.variant_title,
            variant_concept=self.variant_concept,
            rationale=self.rationale,
        )


class GenerationFailure(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


ALLOWED_OUTPUT_SIZES = (24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)


def deliver_size(png: bytes, width: int, height: int, output_size: int,
                 square: bool = True) -> tuple[bytes, int, int, str]:
    """Return the delivered PNG with EXACT dimensions (LANCZOS up- or downscale).
    square=True (1:1 requests): a non-square native image is centre-cropped to a square first, then resized to
    output_size x output_size. square=False (other aspects): aspect is kept and the longer side == output_size.
    -> (png_bytes, width, height, note). The note describes what was done (kept in the provenance sidecar only)."""
    if square and width == height == output_size:
        return png, width, height, "Native size already matches the requested size; delivered unchanged."
    with Image.open(io.BytesIO(png)) as im:
        im.load()
        out = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
    steps = []
    if square:
        if width != height:
            side = min(width, height)
            left, top = (width - side) // 2, (height - side) // 2
            out = out.crop((left, top, left + side, top + side))
            steps.append(f"centre-cropped {width}x{height} to {side}x{side}")
        nw = nh = output_size
    else:
        scale = output_size / max(width, height)
        nw, nh = max(1, round(width * scale)), max(1, round(height * scale))
        if width >= height:
            nw = output_size
        else:
            nh = output_size
    if out.size != (nw, nh):
        verb = "upscaled" if max(out.size) < output_size else "downscaled"
        out = out.resize((nw, nh), Image.LANCZOS)
        steps.append(f"{verb} to {nw}x{nh} (LANCZOS)")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), nw, nh, "; ".join(steps) or "Delivered unchanged."


class GenerationService:
    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        cost_guard: CostGuard,
        store: ArtifactStore,
        refs: ReferenceManager,
        prompts: PromptLibrary | None = None,
    ) -> None:
        self.s, self.registry, self.guard, self.store, self.refs = (
            settings, registry, cost_guard, store, refs,
        )
        self.prompts = prompts
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ prepare
    def _prepare(self, req: GenerateCardRequest) -> tuple[ModelInfo, CompiledPrompt, list, float]:
        try:
            model = self.registry.resolve(req.provider, req.model)
        except ProviderDisabled as exc:
            raise GenerationFailure(400, "model_unavailable", exc.message) from exc

        if len(req.reference_ids) != len(set(req.reference_ids)):
            raise GenerationFailure(400, "duplicate_references", "Duplicate reference ids.")
        if len(req.reference_ids) > self.s.max_refs_per_request:
            raise GenerationFailure(
                400, "too_many_references",
                f"At most {self.s.max_refs_per_request} references per request.",
            )
        cap_max = model.capabilities.get("max_reference_images", 0)
        if req.reference_ids and len(req.reference_ids) > cap_max:
            raise GenerationFailure(
                400, "references_unsupported",
                f"{model.label} accepts at most {cap_max} reference image(s).",
            )
        if req.output_size not in ALLOWED_OUTPUT_SIZES:
            raise GenerationFailure(422, "bad_output_size",
                                    f"Output size must be one of {', '.join(str(x) for x in ALLOWED_OUTPUT_SIZES)} px.")
        if req.aspect_ratio not in model.capabilities.get("aspect_ratios", ["1:1"]):
            raise GenerationFailure(
                400, "aspect_unsupported", f"{model.label} does not support {req.aspect_ratio}."
            )
        refs = []
        try:
            for rid in req.reference_ids:
                refs.append(self.refs.load(rid))
        except ReferenceError_ as exc:
            raise GenerationFailure(400, exc.code, exc.message) from exc
        if refs and model.provider_id not in ("mock",) and not req.reference_policy_accepted:
            raise GenerationFailure(
                400, "reference_policy_required",
                "Confirm that you are allowed to send these reference images to the external "
                "AI provider before generating (spec §6.5).",
            )
        try:
            bundle = self._bundle_for(req)
            texts = {n: b["text"] for n, b in bundle.items()}
            session = {n: b["text"] for n, b in bundle.items() if b["source"] == "session_override"}
            compiled = compile_card_prompt(req.card(), req.wishes, [r.role for r in refs], texts,
                                           req.repair_note or None, protected=bool(session))
            compiled.bundle = bundle
            compiled.warnings = [*compiled.warnings, *check_overrides(session)]
            compiled.template_versions = {n: b.get("version") for n, b in bundle.items()}  # type: ignore[attr-defined]
        except LibraryError as exc:
            raise GenerationFailure(422, exc.code, exc.message) from exc
        except ValueError as exc:
            raise GenerationFailure(422, "invalid_card", str(exc)) from exc
        estimate = self.guard.estimate_usd(model.provider_id, 1, model.model_id)
        return model, compiled, refs, estimate

    def _bundle_for(self, req: GenerateCardRequest) -> dict[str, dict[str, Any]]:
        """The effective prompt bundle of one request: the job's immutable snapshot when it has one, otherwise the permanent
        library plus the request's session overrides. Without a library (unit tests) the built-in defaults apply."""
        if self.prompts is None:
            return {}
        if req.prompt_bundle:
            return self.prompts.normalize_bundle(req.prompt_bundle)
        return self.prompts.resolve_bundle(req.prompt_overrides)

    def render_prompt(self, req: GenerateCardRequest) -> tuple[ModelInfo, CompiledPrompt, list]:
        """Compile the final prompt exactly as generation would, WITHOUT calling any provider (free)."""
        model, compiled, refs, _ = self._prepare(req)
        return model, compiled, refs

    def preview(self, req: GenerateCardRequest) -> dict[str, Any]:
        """Effective-prompt + cost preview. Free, never calls a provider."""
        model, compiled, refs, estimate = self._prepare(req)
        blocked = None
        try:
            self.guard.check(model.provider_id, 1, **({"model_id": model.model_id} if model.provider_id == "cloudflare" else {}))
        except BudgetExceeded as exc:
            blocked = {"code": exc.code, "message": exc.reason}
        return {
            "effective_prompt": compiled.text,
            "layers": compiled.layers,
            "warnings": compiled.warnings,
            "template_version": compiled.template_version,
            "bundle": {n: {k: v for k, v in b.items() if k != "text"} for n, b in compiled.bundle.items()},
            "session_overrides": sorted(n for n, b in compiled.bundle.items() if b["source"] == "session_override"),
            "reference_count": len(refs),
            "estimated_cost_usd": estimate,
            "cost_is_estimate": True,
            "budget": self.guard.summary().__dict__,
            "blocked": blocked,
        }

    # ----------------------------------------------------------------- generate
    async def generate(self, req: GenerateCardRequest, *, allow_golden: bool = False) -> dict[str, Any]:
        if req.category == 5 and not allow_golden:
            raise GenerationFailure(422, "invalid_card", "Category 5 (golden) cards are generated only inside a set job, "
                                    "not through the single-card endpoint.")
        model, compiled, refs, estimate = self._prepare(req)
        if estimate > 0 and not req.confirmed:
            raise GenerationFailure(
                400, "confirmation_required",
                f"This call is estimated at ${estimate:.3f}. Confirm the estimate to proceed.",
                {"estimated_cost_usd": estimate},
            )
        if self._lock.locked():
            raise GenerationFailure(
                409, "busy", "Another generation is already running; batches are not allowed."
            )
        async with self._lock:
            try:
                self.guard.check(model.provider_id, 1, **({"model_id": model.model_id} if model.provider_id == "cloudflare" else {}))
            except BudgetExceeded as exc:
                raise GenerationFailure(402, exc.code, exc.reason) from exc

            provider = self.registry.provider(model.provider_id)
            greq = GenerationRequest(
                prompt=compiled.text, model_id=model.model_id,
                references=tuple(refs), aspect_ratio=req.aspect_ratio,
            )
            artifact_id = new_artifact_id()
            attempts = 0
            max_attempts = 1 + max(self.s.max_card_retries, 0)
            result = None
            while True:
                attempts += 1
                try:
                    result = await provider.generate(greq)
                    break
                except ProviderDisabled as exc:
                    raise GenerationFailure(400, exc.code, exc.message) from exc
                except ProviderError as exc:
                    if exc.billable:
                        self.guard.record(
                            artifact_id=None, provider_id=model.provider_id,
                            model_id=model.model_id, est_cost_usd=estimate,
                            usage=exc.usage, outcome=f"failed:{exc.code}",
                            kind="image", job_id=(req.context or {}).get("job_id"),
                        )
                    if exc.retryable and not exc.billable and attempts < max_attempts:
                        try:
                            self.guard.check(model.provider_id, 1, **({"model_id": model.model_id} if model.provider_id == "cloudflare" else {}))
                        except BudgetExceeded as be:
                            raise GenerationFailure(402, be.code, be.reason) from be
                        continue
                    raise GenerationFailure(
                        502, exc.code, exc.message,
                        {"attempts": attempts, "billable": exc.billable},
                    ) from exc

            self.guard.record(
                artifact_id=artifact_id, provider_id=model.provider_id,
                model_id=model.model_id, est_cost_usd=estimate,
                usage=result.usage, outcome="ok",
                kind="image", job_id=(req.context or {}).get("job_id"),
            )
            delivered = deliver_size(result.png_bytes, result.width, result.height, req.output_size,
                                     square=req.aspect_ratio == "1:1")
            metadata = self._metadata(artifact_id, req, model, compiled, refs, result,
                                      estimate, attempts, delivered)
            metadata["image"]["original_file"] = f"{artifact_id}.original.png"
            metadata["image"]["original_sha256"] = hashlib.sha256(result.png_bytes).hexdigest()
            metadata["image"]["original_bytes"] = len(result.png_bytes)
            self.store.save(artifact_id, delivered[0], metadata, original=result.png_bytes)
            if model.provider_id != "mock":
                self.registry.mark_live(model.provider_id, model.model_id)
            return metadata

    def _metadata(self, artifact_id, req, model, compiled, refs, result, estimate, attempts, delivered):
        png, dw, dh, size_note = delivered
        return {
            "schema_version": 1,
            "artifact_id": artifact_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": __version__,
            "origin": model.origin_badge,
            "is_mock": model.provider_id == "mock",
            "image": {
                "file": f"{artifact_id}.png",
                "url": f"/api/artifacts/{artifact_id}/image",
                "width": dw,
                "height": dh,
                "bytes": len(png),
                "sha256": hashlib.sha256(png).hexdigest(),
                "aspect_ratio_requested": req.aspect_ratio,
                "output_size_requested": req.output_size,
                "native_width": result.width,
                "native_height": result.height,
                "output_size_note": size_note,
            },
            "card": req.card().model_dump(),
            "context": req.context,
            "provider": {
                "id": model.provider_id,
                "model": model.model_id,
                "label": model.label,
                "model_status": model.status,
                "finish_reason": result.finish_reason,
                "model_notes": result.text_notes,
                "request_summary": result.request_summary,
            },
            "prompt": {
                "effective": compiled.text,
                "layers": compiled.layers,
                "template_version": compiled.template_version,
                "style_bible_version": compiled.style_bible_version,
                "category_rules_version": compiled.category_rules_version,
                "template_versions": getattr(compiled, "template_versions", {}),
                # exact bundle used for this image: origin (permanent library vs session override) and permanent versions
                "bundle": {n: {k: v for k, v in b.items() if k != "text"} for n, b in compiled.bundle.items()},
                "session_overrides": sorted(n for n, b in compiled.bundle.items() if b["source"] == "session_override"),
                "mandatory_block_appended": compiled.protected,
                "effective_texts": ({
                    "system_style": compiled.bundle["system_style"]["text"],
                    "category_rule": compiled.bundle[f"category_{req.category}"]["text"],
                    "image_closing": compiled.bundle["image_closing"]["text"],
                } if compiled.bundle else {}),
                "planning_context": {"collection": req.collection, "set": req.genre, "variant_title": req.variant_title,
                                     "variant_concept": req.variant_concept, "rationale": req.rationale},
                "repair_note": req.repair_note,
                "free_wishes": req.wishes,
                "warnings": compiled.warnings,
            },
            "references": [
                {"ref_id": r.ref_id, "role": r.role, "sha256": r.sha256} for r in refs
            ],
            "reference_policy_accepted": req.reference_policy_accepted,
            "cost": {
                "estimated_usd": estimate,
                "is_estimate": True,
                "usage_metadata": result.usage,
                "note": ("Local estimate; the Cloudflare dashboard is the source of truth (Workers Free allocation "
                         "10,000 Neurons/day; this app never changes the plan)."
                         if model.provider_id == "cloudflare"
                         else "Local estimate; Google Billing is the source of truth."),
            },
            "timing": {"latency_ms": result.latency_ms, "attempts": attempts},
            "review": {
                "technical_qa": "not_run",
                "art_review": "pending",
            },
        }
