"""Cloudflare Workers AI / FLUX.2 Klein 4B adapter (verified free fallback, spec §12.2).

Implemented from the documented REST contract: POST /accounts/{id}/ai/run/{model} as
multipart/form-data with `prompt`, `width`, `height` and optional `input_image_0` (reference).
The response is JSON {"result": {"image": "<base64>"}, "success": true} (a raw image body is also
accepted). The API token travels in the Authorization header and is redacted from errors.

Status: external REST smoke tests succeeded earlier (text + reference); THIS adapter has only been
exercised against a mocked transport until a CLOUDFLARE_ACCOUNT_ID is configured for a live check.
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

CLOUDFLARE_MODEL_ID = "@cf/black-forest-labs/flux-2-klein-4b"

CLOUDFLARE_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=True,  # input_image_0 accepted in the external REST test
    max_reference_images=1,
    aspect_ratios=("1:1",),
    cost_type="free_tier",
)

DIMENSIONS = {"1:1": (1024, 1024)}


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


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

    def build_fields(self, req: GenerationRequest) -> dict[str, Any]:
        w, h = DIMENSIONS.get(req.aspect_ratio, DIMENSIONS["1:1"])
        fields: dict[str, Any] = {"prompt": (None, req.prompt), "width": (None, str(w)), "height": (None, str(h))}
        for i, ref in enumerate(req.references[: CLOUDFLARE_CAPABILITIES.max_reference_images]):
            fields[f"input_image_{i}"] = (f"reference_{i}.png", ref.png_bytes, ref.mime_type)
        return fields

    def _error(self, resp: httpx.Response) -> ProviderError:
        msg = ""
        try:
            errs = resp.json().get("errors") or []
            msg = "; ".join(str(e.get("message", e)) for e in errs)
        except Exception:
            msg = resp.text[:200]
        msg = _redact(msg or f"HTTP {resp.status_code}", self._token)
        return ProviderError(
            f"Cloudflare API error {resp.status_code}: {msg}",
            code=f"http_{resp.status_code}",
            http_status=resp.status_code,
            retryable=resp.status_code in (429, 500, 502, 503, 504),
        )

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        url = f"{self._base}/accounts/{self._account}/ai/run/{req.model_id}"
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            try:
                resp = await client.post(url, headers={"Authorization": f"Bearer {self._token}"},
                                         files=self.build_fields(req))
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
            if body.get("success") is False:
                raise self._error(resp)
            b64 = (body.get("result") or {}).get("image") if isinstance(body.get("result"), dict) else body.get("result")
            if not isinstance(b64, str) or not b64:
                raise ProviderError("Cloudflare returned no image.", code="no_image")
            try:
                raw = base64.b64decode(b64)
            except ValueError as exc:
                raise ProviderError("Cloudflare returned invalid base64.", code="bad_image") from exc
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                width, height = im.size
                buf = io.BytesIO()
                im.save(buf, format="PNG")
        except Exception as exc:
            raise ProviderError(f"Cloudflare returned an undecodable image: {type(exc).__name__}", code="bad_image") from exc

        return GenerationResult(
            png_bytes=buf.getvalue(), width=width, height=height, provider_id=self.provider_id,
            model_id=req.model_id, latency_ms=latency_ms, usage={},
            request_summary={"endpoint": f"accounts/*/ai/run/{req.model_id}", "reference_count": min(
                len(req.references), CLOUDFLARE_CAPABILITIES.max_reference_images),
                "aspect_ratio": req.aspect_ratio, "requested_size": list(DIMENSIONS.get(req.aspect_ratio, (1024, 1024)))},
        )
