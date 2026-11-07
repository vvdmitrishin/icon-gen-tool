"""Google Gemini image adapter (primary provider) over the REST `generateContent` API.

Request shape mirrors the PowerShell smoke tests recorded in spec v1.6: text part plus
`inlineData` reference parts. The spec notes that `generationConfig.responseFormat.image`
caused a 400, so that block is never sent. The API key travels in the `x-goog-api-key`
header (never in the URL) and is redacted from every error message.
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

GEMINI_CAPABILITIES = ProviderCapabilities(
    supports_reference_images=True,
    supports_multi_turn_editing=True,
    supports_image_editing=True,
    supports_batch_mode=True,
    max_reference_images=14,
    aspect_ratios=("1:1", "3:4", "4:3", "9:16", "16:9"),
    cost_type="token_billing_with_per-image-equivalent",
)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


class GeminiImageProvider(ImageProvider):
    provider_id = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_s: float = 120.0,
        send_image_config: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._send_image_config = send_image_config
        self._transport = transport

    # ------------------------------------------------------------------ helpers
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    def build_body(self, req: GenerationRequest) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": req.prompt}]
        for ref in req.references:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": ref.mime_type,
                        "data": base64.b64encode(ref.png_bytes).decode("ascii"),
                    }
                }
            )
        gen_cfg: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        if self._send_image_config and req.aspect_ratio:
            gen_cfg["imageConfig"] = {"aspectRatio": req.aspect_ratio}
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen_cfg}

    def _error_from_response(self, resp: httpx.Response) -> ProviderError:
        msg = ""
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except Exception:
            msg = resp.text[:300]
        msg = _redact(msg or f"HTTP {resp.status_code}", self._api_key)
        retryable = resp.status_code in (429, 500, 502, 503, 504)
        return ProviderError(
            f"Gemini API error {resp.status_code}: {msg}",
            code=f"http_{resp.status_code}",
            http_status=resp.status_code,
            retryable=retryable,
        )

    # --------------------------------------------------------------- list models
    async def list_models(self) -> dict[str, list[str]]:
        """Free metadata call: model id -> supportedGenerationMethods (spec §12.3.1)."""
        out: dict[str, list[str]] = {}
        token: str | None = None
        async with self._client() as client:
            for _ in range(10):
                params: dict[str, Any] = {"pageSize": 200}
                if token:
                    params["pageToken"] = token
                try:
                    resp = await client.get(
                        f"{self._base_url}/models", headers=self._headers, params=params
                    )
                except httpx.HTTPError as exc:
                    raise ProviderError(
                        _redact(f"Network error listing models: {exc}", self._api_key),
                        code="network_error",
                        retryable=True,
                    ) from exc
                if resp.status_code != 200:
                    raise self._error_from_response(resp)
                data = resp.json()
                for m in data.get("models", []):
                    out[m["name"].removeprefix("models/")] = m.get(
                        "supportedGenerationMethods", []
                    )
                token = data.get("nextPageToken")
                if not token:
                    break
        return out

    # ------------------------------------------------------------------ generate
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        url = f"{self._base_url}/models/{req.model_id}:generateContent"
        body = self.build_body(req)
        started = time.perf_counter()
        async with self._client() as client:
            try:
                resp = await client.post(url, headers=self._headers, json=body)
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    "Gemini request timed out; the call may still have been billed.",
                    code="timeout",
                    retryable=True,
                    billable=True,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    _redact(f"Network error calling Gemini: {exc}", self._api_key),
                    code="network_error",
                    retryable=True,
                ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            raise self._error_from_response(resp)

        data = resp.json()
        usage = data.get("usageMetadata", {}) or {}
        notes: list[str] = []
        image_b64: str | None = None
        finish_reason: str | None = None

        block = (data.get("promptFeedback") or {}).get("blockReason")
        for cand in data.get("candidates", []) or []:
            finish_reason = finish_reason or cand.get("finishReason")
            for part in (cand.get("content") or {}).get("parts", []) or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data") and image_b64 is None:
                    image_b64 = inline["data"]
                elif part.get("text"):
                    notes.append(part["text"])

        if image_b64 is None:
            why = f"blocked ({block})" if block else f"finishReason={finish_reason}"
            detail = f" Model said: {notes[0][:200]}" if notes else ""
            raise ProviderError(
                f"Gemini returned no image: {why}.{detail}",
                code="no_image",
                billable=True,  # HTTP 200 → tokens may have been consumed
                usage=usage,
            )

        try:
            raw = base64.b64decode(image_b64)
            with Image.open(io.BytesIO(raw)) as im:
                im.load()
                width, height = im.size
                buf = io.BytesIO()
                im.save(buf, format="PNG")  # normalize JPEG/WebP → PNG
        except Exception as exc:
            raise ProviderError(
                f"Gemini returned an undecodable image: {type(exc).__name__}",
                code="bad_image",
                billable=True,
                usage=usage,
            ) from exc

        return GenerationResult(
            png_bytes=buf.getvalue(),
            width=width,
            height=height,
            provider_id=self.provider_id,
            model_id=req.model_id,
            latency_ms=latency_ms,
            text_notes=notes,
            usage=usage,
            finish_reason=finish_reason,
            request_summary={
                "endpoint": f"models/{req.model_id}:generateContent",
                "reference_count": len(req.references),
                "aspect_ratio": req.aspect_ratio,
                "image_config_sent": self._send_image_config,
            },
        )
