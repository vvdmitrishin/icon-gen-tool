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
from .prompt_compiler import CardSpec, CompiledPrompt, compile_card_prompt
from .prompt_library import PromptLibrary
from .reference_manager import ReferenceError_, ReferenceManager
from .storage import ArtifactStore, new_artifact_id


class GenerateCardRequest(BaseModel):
    collection: str = Field(default="Movie Collection", min_length=1, max_length=80)
    genre: str = Field(min_length=1, max_length=80)
    object_name: str = Field(min_length=1, max_length=80)
    object_description: str = Field(default="", max_length=300)
    category: Literal[1, 2, 3, 4, 5]  # 5 = golden (only inside a set job; the single-card endpoint refuses it)
    wishes: str = Field(default="", max_length=2000)
    provider: str
    model: str
    reference_ids: list[str] = Field(default_factory=list)
    reference_policy_accepted: bool = False
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9"] = "1:1"
    output_size: int = 1024  # delivered icon side in px (24..1024); downscaled from the native image, never upscaled
    confirmed: bool = False  # explicit user confirmation of the shown cost estimate
    context: dict[str, Any] = Field(default_factory=dict)  # e.g. job_id/variant_id/position (provenance)
    repair_note: str = Field(default="", max_length=300)  # regeneration corrections (spec §11.4)

    def card(self) -> CardSpec:
        return CardSpec(
            collection=self.collection,
            genre=self.genre,
            object_name=self.object_name,
            object_description=self.object_description,
            category=self.category,
        )


class GenerationFailure(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


ALLOWED_OUTPUT_SIZES = (24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024)


def deliver_size(png: bytes, width: int, height: int, output_size: int) -> tuple[bytes, int, int, str]:
    """Downscale (LANCZOS, aspect kept) so the longer side == output_size; never upscale.
    -> (png_bytes, width, height, note). note is '' unless the native image was smaller than requested."""
    longer = max(width, height)
    if longer <= output_size:
        note = ("" if longer == output_size else
                f"Native image is {width}x{height}px, smaller than the requested {output_size}px; "
                "delivered at native size (no upscaling).")
        return png, width, height, note
    scale = output_size / longer
    nw, nh = max(1, round(width * scale)), max(1, round(height * scale))
    with Image.open(io.BytesIO(png)) as im:
        im.load()
        out = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        out = out.resize((nw, nh), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
    return buf.getvalue(), nw, nh, ""


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
            texts, versions = self.prompts.active_texts() if self.prompts else ({}, {})
            compiled = compile_card_prompt(req.card(), req.wishes, [r.role for r in refs], texts,
                                           req.repair_note or None)
            compiled.template_versions = versions  # type: ignore[attr-defined]
        except ValueError as exc:
            raise GenerationFailure(422, "invalid_card", str(exc)) from exc
        estimate = self.guard.estimate_usd(model.provider_id, 1, model.model_id)
        return model, compiled, refs, estimate

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
            )
            delivered = deliver_size(result.png_bytes, result.width, result.height, req.output_size)
            metadata = self._metadata(artifact_id, req, model, compiled, refs, result,
                                      estimate, attempts, delivered)
            self.store.save(artifact_id, delivered[0], metadata)
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
