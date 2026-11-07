import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.base import GenerationRequest, ProviderDisabled
from app.providers.model_registry import ModelRegistry
from app.providers.openai_image import OpenAIImageProvider


def req():
    return GenerationRequest(prompt="x", model_id="gpt-image-2.5-flare")


def test_openai_never_touches_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("OpenAI provider attempted network access")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", boom)
    monkeypatch.setattr(httpx.Client, "__init__", boom)
    with pytest.raises(ProviderDisabled):
        asyncio.run(OpenAIImageProvider().generate(req()))
    # even when both flags are flipped, the scaffold refuses (no implementation)
    with pytest.raises(ProviderDisabled):
        asyncio.run(OpenAIImageProvider(enabled=True, paid_calls_enabled=True).generate(req()))


def test_registry_lists_all_providers_with_honest_status(tmp_path):
    reg = ModelRegistry(Settings(data_root=tmp_path, gemini_api_key="k"))
    by = {(m.provider_id, m.model_id): m for m in reg.models()}
    nb = by[("gemini", "gemini-nano-banana-2.1")]
    assert nb.selectable and nb.status == "external_rest_verified"
    lite = by[("gemini", "gemini-3.1-flash-lite-image")]
    assert not lite.selectable and lite.status == "documented"
    for mid in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
        o = by[("openai", mid)]
        assert not o.selectable and o.status.startswith("disabled")
        with pytest.raises(ProviderDisabled):
            reg.resolve("openai", mid)
    cf = by[("cloudflare", "@cf/black-forest-labs/flux-2-klein-4b")]
    assert not cf.selectable and cf.role == "fallback"


def test_gemini_disabled_without_key(tmp_path):
    reg = ModelRegistry(Settings(data_root=tmp_path, gemini_api_key=""))
    nb = next(m for m in reg.models() if m.model_id == "gemini-nano-banana-2.1")
    assert not nb.selectable and nb.status == "disabled_no_key"
    with pytest.raises(ProviderDisabled):
        reg.provider("gemini")


def test_availability_check_unlocks_documented_models(tmp_path):
    from tests.conftest import FAKE_KEY, Recorder

    rec = Recorder(body={"models": [
        {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]},
    ]})
    reg = ModelRegistry(Settings(data_root=tmp_path, gemini_api_key=FAKE_KEY),
                        gemini_transport=rec.transport)
    out = asyncio.run(reg.check_gemini_availability())
    by = {m.model_id: m for m in reg.models()}
    assert by["gemini-3.1-flash-image"].selectable
    assert by["gemini-3.1-flash-image"].status == "available_in_account"
    assert not by["gemini-3-pro-image"].selectable
    assert out["available"]["gemini-3-pro-image"] is False
