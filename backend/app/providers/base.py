"""Provider-agnostic image generation contract (spec §8.4, §12.4)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelStatus(str, Enum):
    """Spec §12.3.1: never conflate 'documented' with 'callable in the app'."""

    DOCUMENTED = "documented"
    EXTERNAL_REST_VERIFIED = "external_rest_verified"
    AVAILABLE_IN_ACCOUNT = "available_in_account"  # confirmed via ListModels
    CONFIGURED_APP = "configured_app"
    APP_LIVE = "app_live"
    ADAPTER_PENDING = "adapter_pending"
    PLANNED = "planned"  # visible roadmap entry: no adapter/runtime yet, never selectable
    DISABLED_NO_KEY = "disabled_no_key"
    DISABLED_BY_FLAG = "disabled_by_flag"
    CONNECTION_FAILED = "connection_failed"  # credentials present but the last free connection test rejected them
    MOCK = "mock"


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_reference_images: bool = False
    supports_multi_turn_editing: bool = False
    supports_image_editing: bool = False
    supports_transparent_background: bool = False
    supports_svg_output: bool = False
    supports_batch_mode: bool = False
    max_reference_images: int = 0
    aspect_ratios: tuple[str, ...] = ("1:1",)
    cost_type: str = "free"


@dataclass(frozen=True)
class ReferenceImage:
    ref_id: str
    png_bytes: bytes
    sha256: str
    role: str = "style"
    mime_type: str = "image/png"


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    model_id: str
    references: tuple[ReferenceImage, ...] = ()
    aspect_ratio: str = "1:1"


@dataclass
class GenerationResult:
    png_bytes: bytes
    width: int
    height: int
    provider_id: str
    model_id: str
    latency_ms: int
    text_notes: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    request_summary: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """A provider call failed. `billable` marks failures that may still have consumed quota
    (e.g. HTTP 200 with a safety block and no image)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        http_status: int | None = None,
        retryable: bool = False,
        billable: bool = False,
        usage: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.billable = billable
        self.usage = usage or {}


class ProviderDisabled(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="provider_disabled")


class ImageProvider(ABC):
    provider_id: str

    @abstractmethod
    async def generate(self, req: GenerationRequest) -> GenerationResult: ...
