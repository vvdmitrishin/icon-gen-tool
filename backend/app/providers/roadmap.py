"""Future-integration backlog for the Model Selector (spec v1.8 §12.7).

DATA ONLY. Nothing here downloads weights, installs a runtime or calls an API. Every entry is
visible in the selector but DISABLED (`status="planned"`) until its adapter and runtime
configuration exist, so an unavailable model is never shown as working. Capability values are
EXPECTATIONS marked `verified: false`; `None` means "unknown/unverified" (never assumed true).
Identifiers (`model_id`) are kept apart from the human-readable `label`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LOCAL_PROVIDER_ID = "local"
LOCAL_PROVIDER_LABEL = "ComfyUI API runtime"
LOCAL_RUNTIME_REQUIREMENTS = (
    "Local inference runtime (ComfyUI API) and the model weights: both are not installed, and "
    "the local adapter is not implemented."
)
LOCAL_REASON = (
    "Planned: needs the local adapter, a local ComfyUI runtime and downloaded model weights "
    "(none are installed)."
)

# ERNIE: Prompt Enhancement is an optional CONFIGURATION of the model, not a second model entry.
ERNIE_OPTIONS: list[dict[str, Any]] = [{
    "key": "prompt_enhancement",
    "label": "Prompt Enhancement",
    "default": False,
    "status": "planned",
    "description": "Optional prompt-enhancement step for ERNIE-Image; planned as a setting, not as a separate model.",
}]


@dataclass(frozen=True)
class PlannedModel:
    provider_id: str
    provider_label: str
    model_id: str  # real identifier (never shown as the label)
    label: str  # human-readable UI text
    runtime: str  # cloud_api | local
    runtime_label: str
    text_to_image: bool | None
    image_editing: bool | None
    supports_reference_images: bool
    max_reference_images: int | None  # None = unknown
    notes: str
    reason: str
    runtime_requirements: str | None
    options: list[dict[str, Any]] = field(default_factory=list)
    price_note: str | None = None
    est_cost_per_image_usd: float | None = None
    aspect_ratios: tuple[str, ...] = ("1:1",)

    def capabilities(self) -> dict[str, Any]:
        return {
            "text_to_image": self.text_to_image,
            "image_editing": self.image_editing,
            "supports_reference_images": self.supports_reference_images,
            "max_reference_images": self.max_reference_images,
            "aspect_ratios": list(self.aspect_ratios),
            "notes": self.notes,
            "verified": False,  # expectations only, not verified in this app
        }


def _local(model_id: str, label: str, *, t2i: bool | None, edit: bool | None, refs: bool,
           notes: str, options: list[dict[str, Any]] | None = None) -> PlannedModel:
    return PlannedModel(
        provider_id=LOCAL_PROVIDER_ID, provider_label=LOCAL_PROVIDER_LABEL, model_id=model_id, label=label,
        runtime="local", runtime_label="Local (planned)", text_to_image=t2i, image_editing=edit,
        supports_reference_images=refs, max_reference_images=None, notes=notes, reason=LOCAL_REASON,
        runtime_requirements=LOCAL_RUNTIME_REQUIREMENTS, options=list(options or []),
    )


# ByteDance Seedream 4.5 through Cloudflare. Not enabled and no paid calls until it is explicitly
# approved and billing + credentials are verified.
SEEDREAM_45 = PlannedModel(
    provider_id="cloudflare",
    provider_label="ByteDance Seedream (via Cloudflare)",
    model_id="bytedance/seedream-4.5",
    label="Seedream 4.5",
    runtime="cloud_api",
    runtime_label="Cloud API",
    text_to_image=True,
    image_editing=None,
    supports_reference_images=False,
    max_reference_images=None,
    notes="Planned cloud model reached through Cloudflare. Reference/editing support to be verified when the adapter is built.",
    reason="Planned: not enabled and no paid calls until the integration is explicitly approved and billing and credentials are verified.",
    runtime_requirements="Cloudflare account ID and API token, plus approved billing for this model.",
    price_note="Published price approx. $0.04 per generated image (unverified; no paid calls until approved).",
    est_cost_per_image_usd=0.04,
)

LOCAL_MODELS: tuple[PlannedModel, ...] = (
    _local("flux-2-klein-4b", "FLUX.2 Klein 4B", t2i=True, edit=True, refs=True,
           notes="Expected: text-to-image plus reference-based editing (the hosted Cloudflare version accepts a reference image). Not verified locally."),
    _local("z-image-turbo", "Z-Image-Turbo", t2i=True, edit=False, refs=False,
           notes="Expected: fast text-to-image. Reference input is not assumed."),
    _local("z-image-base", "Z-Image Base", t2i=True, edit=False, refs=False,
           notes="Expected: text-to-image. Reference input is not assumed."),
    _local("ernie-image-turbo", "ERNIE-Image-Turbo", t2i=True, edit=None, refs=False, options=ERNIE_OPTIONS,
           notes="Expected: text-to-image. Prompt Enhancement is an optional setting (planned), not a separate model."),
    _local("ernie-image-base", "ERNIE-Image Base", t2i=True, edit=None, refs=False, options=ERNIE_OPTIONS,
           notes="Expected: text-to-image. Prompt Enhancement is an optional setting (planned), not a separate model."),
    _local("qwen-image-edit-2511", "Qwen-Image-Edit-2511", t2i=None, edit=True, refs=True,
           notes="Editing-capable model: it works on input images and is NOT treated as a standard text-to-image generator until verified."),
    _local("qwen-image-2512", "Qwen-Image-2512", t2i=True, edit=None, refs=False,
           notes="Expected: text-to-image. Editing and reference input are not assumed."),
)

PLANNED_MODELS: tuple[PlannedModel, ...] = (SEEDREAM_45, *LOCAL_MODELS)
