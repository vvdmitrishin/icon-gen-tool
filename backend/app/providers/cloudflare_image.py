"""Cloudflare Workers AI image adapter (FLUX.2 Klein 4B + FLUX.1 Schnell), spec §12.2.

Per-model request formats live in cloudflare_models.py (docs-derived, not yet verified live):
  * Klein 4B  -> multipart/form-data: prompt, width, height, optional input_image_0..3
                 (references are downscaled to fit under 512x512 as the docs require).
  * Schnell   -> JSON {prompt, steps}; no reference input (references are never sent).
Responses: JSON {"result": {"image": "<base64>"}, "success": true} (REST wrapper) or a raw image body;
base64 JPEG/PNG is normalized to PNG. The API token travels in the Authorization header and is redacted
from errors.

Free-tier safety: this adapter never changes a plan and never falls back to another provider. A "daily free
allocation used up" failure becomes ProviderError(code="quota_exceeded", retryable=False, billable=False).
"""
from __future__ import annotations

import base64
import io
import time
from typing import Any

import httpx
from PIL import Image

from .base import (
    GenerationRequest,
    GenerationResult,
    ImageProvider,
    ProviderCapabilities,
    ProviderError,
)
from .cloudflare_models import (
    CAPACITY_ERROR_CODES,
    FREE_NEURONS_PER_DAY,
    KLEIN,
    PLAN_REQUIRED_ERROR_CODES,
    QUOTA_ERROR_CODES,
    CloudflareModelSpec,
    get_model_spec,
)

CLOUDFLARE_MODEL_ID = KLEIN.model_id  # default / first-priority model (kept for existing importers)

CLOUDFLARE_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=KLEIN.supports_reference_images,
    max_reference_images=KLEIN.max_reference_images or 0,
    aspect_ratios=KLEIN.aspect_ratios,
    cost_type="free_tier",
)

DIMENSIONS = KLEIN.aspect_sizes

QUOTA_MESSAGE = (
    f"Cloudflare's daily free allocation ({FREE_NEURONS_PER_DAY:,} Neurons/day) is used up. Nothing was charged "
    "and no plan upgrade was attempted. Wait for the daily reset (00:00 UTC) or choose another model."
)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _shrink_reference(png: bytes, max_side: int) -> bytes:
    """Downscale (never upscale) so both sides fit within max_side; always returns PNG."""
    with Image.open(io.BytesIO(png)) as im:
        im.load()
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


class CloudflareImageProvider(ImageProvider):
    provider_id = "cloudflare"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        base_url: str = "https://api.cloudflare.com/client/v4",
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._account = account_id
        self._token = api_token
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    # ------------------------------------------------------------------ request construction
    @staticmethod
    def _spec(req: GenerationRequest) -> CloudflareModelSpec:
        spec = get_model_spec(req.model_id)
        if spec is None:
            raise ProviderError(f"Unknown Cloudflare model id '{req.model_id}'. Supported: "
                                "@cf/black-forest-labs/flux-2-klein-4b, @cf/black-forest-labs/flux-1-schnell.",
                                code="unsupported_model")
        return spec

    @staticmethod
    def _used_references(spec: CloudflareModelSpec, req: GenerationRequest):
        if not spec.supports_reference_images:
            return ()
        return req.references[: spec.max_reference_images or 0]

    def build_request(self, req: GenerationRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (httpx kwargs for the body, request_summary extras). Raises on unknown models."""
        spec = self._spec(req)
        w, h = spec.aspect_sizes.get(req.aspect_ratio, (spec.default_width, spec.default_height))
        if spec.request_mode == "json":
            body: dict[str, Any] = {"prompt": req.prompt}
            if spec.default_steps:
                body["steps"] = spec.default_steps
            return {"json": body}, {"request_mode": "json", "steps": spec.default_steps, "reference_count": 0,
                                    "references_ignored": len(req.references), "requested_size": None}
        fields: dict[str, Any] = {"prompt": (None, req.prompt), "width": (None, str(w)), "height": (None, str(h))}
        used = self._used_references(spec, req)
        for i, ref in enumerate(used):
            data = _shrink_reference(ref.png_bytes, spec.reference_max_side or 504)
            fields[f"input_image_{i}"] = (f"reference_{i}.png", data, "image/png")
        return {"files": fields}, {"request_mode": "multipart", "reference_count": len(used),
                                   "references_ignored": len(req.references) - len(used),
                                   "requested_size": [w, h]}

    def build_fields(self, req: GenerationRequest) -> dict[str, Any]:  # backward-compatible helper
        return self.build_request(req)[0].get("files", {})

    # ------------------------------------------------------------------ errors
    def _error(self, resp: httpx.Response) -> ProviderError:
        errs: list[Any] = []
        msg = ""
        try:
            errs = resp.json().get("errors") or []
            msg = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errs)
        except Exception:
            msg = resp.text[:200]
        codes = set()
        for e in errs:
            try:
                codes.add(int(e.get("code")))
            except (AttributeError, TypeError, ValueError):
                pass
        status = resp.status_code
        low = msg.lower()
        quota = (codes & QUOTA_ERROR_CODES) or ("daily free allocation" in low) or (
            status == 429 and "neurons" in low and "allocation" in low)
        if quota:
            return ProviderError(QUOTA_MESSAGE, code="quota_exceeded", http_status=status,
                                 retryable=False, billable=False)
        msg = _redact(msg or f"HTTP {status}", self._token)
        if codes & PLAN_REQUIRED_ERROR_CODES:
            return ProviderError(
                f"Cloudflare says this model requires the Workers Paid plan ({msg}). No upgrade was attempted "
                "and nothing was charged; choose another model.",
                code="plan_required", http_status=status, retryable=False)
        if codes & CAPACITY_ERROR_CODES:
            return ProviderError(f"Cloudflare is temporarily out of capacity (retry later): {msg}",
                                 code="rate_limited", http_status=status, retryable=True)
        return ProviderError(
            f"Cloudflare API error {status}: {msg}",
            code="rate_limited" if status == 429 else f"http_{status}",
            http_status=status,
            retryable=status in (429, 500, 502, 503, 504),
        )

    # ------------------------------------------------------------------ call
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        body_kwargs, summary = self.build_request(req)  # rejects unknown models before any network use
        url = f"{self._base}/accounts/{self._account}/ai/run/{req.model_id}"
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                resp = await client.post(url, headers={"Authorization": f"Bearer {self._token}"}, **body_kwargs)
            except httpx.TimeoutException as exc:
                raise ProviderError("Cloudflare request timed out.", code="timeout", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(_redact(f"Network error calling Cloudflare: {exc}", self._token),
                                    code="network_error", retryable=True) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            raise self._error(resp)

        ctype = resp.headers.get("content-type", "")
        raw: bytes | None = None
        if ctype.startswith("image/"):
            raw = resp.content
        else:
            try:
                body = resp.json()
            except ValueError as exc:
                raise ProviderError("Cloudflare returned a non-JSON, non-image response.", code="bad_response") from exc
            if not isinstance(body, dict):
                raise ProviderError("Cloudflare returned an unexpected response.", code="bad_response")
            if body.get("success") is False:
                raise self._error(resp)
            result = body.get("result")
            b64 = result.get("image") if isinstance(result, dict) else (result if isinstance(result, str) else body.get("image"))
            if not isinstance(b64, str) or not b64:
                raise ProviderError("Cloudflare returned no image.", code="no_image")
            if b64.startswith("data:") and "," in b64:
                b64 = b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(b64, validate=False)
            except ValueError as exc:
                raise ProviderError("Cloudflare returned invalid base64.", code="bad_image") from exc
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                src_format = im.format
                width, height = im.size
                buf = io.BytesIO()
                im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, format="PNG")
        except Exception as exc:
            raise ProviderError(f"Cloudflare returned an undecodable image: {type(exc).__name__}", code="bad_image") from exc

        summary.update({"endpoint": f"accounts/*/ai/run/{req.model_id}", "aspect_ratio": req.aspect_ratio,
                        "source_format": src_format})
        return GenerationResult(
            png_bytes=buf.getvalue(), width=width, height=height, provider_id=self.provider_id,
            model_id=req.model_id, latency_ms=latency_ms, usage={}, request_summary=summary,
        )
