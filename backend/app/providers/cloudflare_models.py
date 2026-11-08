"""Per-model knowledge for Cloudflare Workers AI image models (single source of truth).

Sources (public documentation only; nothing here was verified with a live call - `verified` is False):
  * FLUX.2 Klein 4B model page: https://developers.cloudflare.com/workers-ai/models/flux-2-klein-4b/
    and its input guide (multipart): prompt, input_image_0..input_image_3 (binary, each must be
    smaller than 512x512, at most 4), guidance (float), width/height (256-1920, defaults 1024/768),
    seed; steps fixed at 4 (distilled). Output field `image` = base64 string (REST wraps it in
    {"result": {"image": ...}, "success": true}).
  * FLUX.1 Schnell model page: https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/
    JSON input {prompt (1-2048 chars, required), steps (int, max 8, default 4), seed}; output
    {"image": "<base64 JPEG>"} (examples treat it as image/jpeg). No width/height/reference input.
  * Pricing: https://developers.cloudflare.com/workers-ai/platform/pricing/
    10,000 Neurons/day free, $0.011 per 1,000 Neurons on Workers Paid; Klein: 26.05 Neurons per output
    512x512 tile and 5.37 per input 512x512 tile; Schnell: 4.80 per 512x512 tile + 9.60 per step.
  * Errors: https://developers.cloudflare.com/workers-ai/platform/errors/
    3036 (HTTP 429) = daily free allocation of 10,000 neurons used up; 3040 (429) = temporary
    out-of-capacity (retry later); 5035 (403) = model requires Workers Paid.

This module never changes a Cloudflare plan and never describes a model as unconditionally free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KLEIN_ID = "@cf/black-forest-labs/flux-2-klein-4b"
SCHNELL_ID = "@cf/black-forest-labs/flux-1-schnell"

FREE_NEURONS_PER_DAY = 10_000
USD_PER_1000_NEURONS = 0.011
PRICING_URL = "https://developers.cloudflare.com/workers-ai/platform/pricing/"

# Klein: reference images must be "smaller than 512x512"; we downscale to fit this box (multiple of 8, < 512).
KLEIN_REF_MAX_SIDE = 504

# Cloudflare error codes that mean "daily free allocation used up" (3036 documented; 4006 seen historically).
QUOTA_ERROR_CODES = frozenset({3036, 4006})
CAPACITY_ERROR_CODES = frozenset({3040})
PLAN_REQUIRED_ERROR_CODES = frozenset({5035})

ASPECT_SIZES_KLEIN: dict[str, tuple[int, int]] = {  # all within 256-1920, multiples of 16
    "1:1": (1024, 1024), "3:4": (768, 1024), "4:3": (1024, 768), "9:16": (576, 1024), "16:9": (1024, 576),
}
ASPECT_SIZES_SCHNELL: dict[str, tuple[int, int]] = {"1:1": (1024, 1024)}  # size is not an input; model default


@dataclass(frozen=True)
class CloudflareModelSpec:
    model_id: str
    label: str
    request_mode: str  # "multipart" | "json"
    supports_reference_images: bool
    max_reference_images: int | None  # None = unknown; 0 = none
    default_width: int
    default_height: int
    default_steps: int | None
    aspect_sizes: dict[str, tuple[int, int]]
    runtime_requirements: str
    notes: str
    price_note: str
    est_neurons_per_image: float
    status: str = "configured_app"  # recommended registry status once credentials exist (see report)
    text_to_image: bool = True
    image_editing: bool = False
    reference_max_side: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)

    @property
    def aspect_ratios(self) -> tuple[str, ...]:
        return tuple(self.aspect_sizes)

    @property
    def est_usd_per_image(self) -> float:
        return round(self.est_neurons_per_image / 1000 * USD_PER_1000_NEURONS, 6)


_PRICE_COMMON = (
    "Uses the Cloudflare Workers Free allocation (10,000 Neurons/day, resets 00:00 UTC). Usage beyond it "
    "fails on the Free plan and is billed on Workers Paid; this app never changes your Cloudflare plan."
)

KLEIN = CloudflareModelSpec(
    model_id=KLEIN_ID,
    label="FLUX.2 Klein 4B",
    request_mode="multipart",
    supports_reference_images=True,
    max_reference_images=4,
    default_width=1024,
    default_height=1024,
    default_steps=None,  # fixed at 4 by the model, not sent
    aspect_sizes=ASPECT_SIZES_KLEIN,
    runtime_requirements="Cloudflare account ID and API token (Workers AI) in the local .env.",
    notes=("Text-to-image plus up to 4 reference images (multipart input_image_0..3, each downscaled to fit "
           "under 512x512 before sending). Output is a base64 image normalized to PNG. Expectations are from "
           "the Cloudflare docs; not yet verified with a live call."),
    price_note=(_PRICE_COMMON + " Published price ~26.05 Neurons per 512x512 output tile "
                "(1024x1024 = 4 tiles) + 5.37 per reference tile, about 110 Neurons (~$0.0012) per image, "
                "i.e. roughly 90 images/day inside the free allocation."),
    est_neurons_per_image=110.0,
    reference_max_side=KLEIN_REF_MAX_SIDE,
    capabilities={
        "text_to_image": True, "image_editing": True, "supports_reference_images": True,
        "max_reference_images": 4, "aspect_ratios": list(ASPECT_SIZES_KLEIN),
        "notes": "Reference images are downscaled to <512x512 by the adapter.", "verified": False,
    },
)

SCHNELL = CloudflareModelSpec(
    model_id=SCHNELL_ID,
    label="FLUX.1 Schnell",
    request_mode="json",
    supports_reference_images=False,
    max_reference_images=0,
    default_width=1024,
    default_height=1024,
    default_steps=4,
    aspect_sizes=ASPECT_SIZES_SCHNELL,
    runtime_requirements="Cloudflare account ID and API token (Workers AI) in the local .env.",
    notes=("Text-to-image only (JSON {prompt, steps}); no reference-image input, so reference selections are "
           "NOT sent - jobs record reference_selection=unsupported_by_model and provenance lists no references. "
           "Base64 JPEG output is normalized to PNG. Not yet verified with a live call."),
    price_note=(_PRICE_COMMON + " Published price ~4.80 Neurons per 512x512 tile + 9.60 per step "
                "(1024x1024, 4 steps = ~58 Neurons, ~$0.0006) per image, roughly 170 images/day inside the "
                "free allocation."),
    est_neurons_per_image=57.6,
    capabilities={
        "text_to_image": True, "image_editing": False, "supports_reference_images": False,
        "max_reference_images": 0, "aspect_ratios": list(ASPECT_SIZES_SCHNELL),
        "notes": "No image input. Output size is the model default (not selectable).", "verified": False,
    },
)

CLOUDFLARE_MODELS: tuple[CloudflareModelSpec, ...] = (KLEIN, SCHNELL)


def get_model_spec(model_id: str | None) -> CloudflareModelSpec | None:
    return next((m for m in CLOUDFLARE_MODELS if m.model_id == model_id), None)


def estimate_usd(model_id: str | None, settings: Any) -> float:
    """Per-image estimate. Free plan -> 0.0 (free allocation applies). Paid -> override or published price."""
    if getattr(settings, "cloudflare_plan", "free") != "paid":
        return 0.0
    override = getattr(settings, "cloudflare_est_cost_per_image_usd", None)
    if override is not None and override > 0:
        return round(float(override), 6)
    spec = get_model_spec(model_id) or KLEIN
    return spec.est_usd_per_image
