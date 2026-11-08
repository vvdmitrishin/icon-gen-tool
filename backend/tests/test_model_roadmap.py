"""Model Selector roadmap (spec v1.8 §12.7): planned models are visible, disabled and never 'working'."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.model_registry import ModelRegistry
from app.providers.roadmap import LOCAL_MODELS, PLANNED_MODELS, SEEDREAM_45

LOCAL_IDS = ["flux-2-klein-4b", "z-image-turbo", "z-image-base", "ernie-image-turbo", "ernie-image-base",
             "qwen-image-edit-2511", "qwen-image-2512"]


@pytest.fixture
def models(tmp_path):
    reg = ModelRegistry(Settings(data_root=tmp_path, gemini_api_key="k"))
    return {(m.provider_id, m.model_id): m for m in reg.models()}


def test_every_requested_model_is_listed_with_label_separate_from_identifier(models):
    local = [m for m in models.values() if m.runtime == "local"]
    assert [m.model_id for m in local] == LOCAL_IDS
    assert {m.label for m in local} == {"FLUX.2 Klein 4B", "Z-Image-Turbo", "Z-Image Base", "ERNIE-Image-Turbo",
                                        "ERNIE-Image Base", "Qwen-Image-Edit-2511", "Qwen-Image-2512"}
    assert all(m.label != m.model_id for m in local)             # human label vs real identifier
    cloud = {m.label for m in models.values() if m.runtime == "cloud_api"}
    assert {"Nano Banana 2.1", "FLUX.2 Klein 4B", "Seedream 4.5", "GPT Image Flare"} <= cloud
    assert any(m.runtime == "test" and m.provider_id == "mock" for m in models.values())  # Mock kept


def test_only_gemini_nano_banana_and_mock_are_selectable_by_default(models):
    sel = sorted(k for k, m in models.items() if m.selectable)
    assert sel == [("gemini", "gemini-nano-banana-2.1"), ("mock", "mock-placeholder-v1")]
    nb = models[("gemini", "gemini-nano-banana-2.1")]
    assert nb.status == "external_rest_verified" and nb.runtime == "cloud_api" and nb.capabilities["verified"] is True


def test_planned_models_are_disabled_with_reasons_and_never_look_working(models):
    for key, m in models.items():
        if m.status == "planned":
            assert m.selectable is False and m.disabled_reason and m.role == "planned"
            assert m.origin_badge.startswith("Planned") and "disabled" in m.origin_badge
            assert m.capabilities["verified"] is False           # expectations, not facts
            assert m.est_cost_per_image_usd in (None, 0.04)
    cfg = models[("openai", "gpt-image-2.5-flare")]
    assert cfg.status.startswith("disabled") and not cfg.selectable
    assert not models[("cloudflare", "@cf/black-forest-labs/flux-2-klein-4b")].selectable  # no account id configured


def test_seedream_45_is_planned_via_cloudflare_with_published_price_and_no_other_seedream(models):
    s = models[("cloudflare", "bytedance/seedream-4.5")]
    assert s.label == "Seedream 4.5" and s.status == "planned" and s.runtime == "cloud_api" and not s.selectable
    assert "0.04" in s.price_note and "no paid calls until approved" in s.price_note
    assert "approved" in s.disabled_reason
    assert [k for k in models if "seedream" in k[1].lower()] == [("cloudflare", "bytedance/seedream-4.5")]
    assert SEEDREAM_45.model_id == "bytedance/seedream-4.5"


def test_local_models_need_a_runtime_and_weights_that_are_not_installed(models):
    for m in (x for x in models.values() if x.runtime == "local"):
        assert m.provider_id == "local" and m.runtime_label == "Local (planned)"
        assert "ComfyUI" in m.runtime_requirements and "not installed" in m.runtime_requirements
        assert "weights" in m.disabled_reason and "none are installed" in m.disabled_reason


def test_qwen_edit_is_an_editing_model_not_assumed_text_to_image(models):
    c = models[("local", "qwen-image-edit-2511")].capabilities
    assert c["image_editing"] is True and c["text_to_image"] is None   # unverified, never assumed true
    assert c["supports_reference_images"] is True and "NOT treated as a standard text-to-image" in c["notes"]
    assert models[("local", "qwen-image-2512")].capabilities["text_to_image"] is True


def test_ernie_prompt_enhancement_is_an_option_not_a_duplicate_model(models):
    ernie = [m for m in models.values() if "ernie" in m.model_id]
    assert [m.model_id for m in ernie] == ["ernie-image-turbo", "ernie-image-base"]   # exactly two entries
    for m in ernie:
        assert [o["key"] for o in m.options] == ["prompt_enhancement"]
        assert m.options[0]["default"] is False and m.options[0]["status"] == "planned"
    assert not any("enhanc" in m.model_id for m in models.values())
    assert all(not m.options for m in models.values() if "ernie" not in m.model_id)


def test_capability_metadata_is_complete_for_every_model(models):
    required = {"text_to_image", "image_editing", "supports_reference_images", "max_reference_images", "aspect_ratios", "notes", "verified"}
    for m in models.values():
        assert required <= set(m.capabilities), m.model_id
        assert m.runtime_requirements, m.model_id
        assert m.runtime in ("cloud_api", "local", "test") and m.runtime_label
        assert m.capabilities["max_reference_images"] is None or isinstance(m.capabilities["max_reference_images"], int)
    assert len(PLANNED_MODELS) == 1 + len(LOCAL_MODELS) == 8


def test_planned_models_cannot_be_resolved_or_generated_and_make_no_network_call(tmp_path):
    hits = []

    def spy(request):
        hits.append(request)
        return httpx.Response(500)

    s = Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a", gemini_api_key="k")
    app = create_app(s, gemini_transport=httpx.MockTransport(spy), cloudflare_transport=httpx.MockTransport(spy))
    reg = ModelRegistry(s)
    from app.providers.base import ProviderDisabled
    for prov, mid in [("local", i) for i in LOCAL_IDS] + [("cloudflare", "bytedance/seedream-4.5")]:
        with pytest.raises(ProviderDisabled):
            reg.resolve(prov, mid)
        with pytest.raises(ProviderDisabled):
            asyncio.run(reg.provider(prov).generate(None))  # type: ignore[arg-type]
    with TestClient(app) as c:
        for prov, mid in [("local", "z-image-turbo"), ("local", "qwen-image-edit-2511"), ("cloudflare", "bytedance/seedream-4.5")]:
            r = c.post("/api/generate-card", json=dict(genre="Fantasy", object_name="Wand", category=1, provider=prov, model=mid, confirmed=True))
            assert r.status_code == 400 and r.json()["detail"]["code"] == "model_unavailable"
            j = c.post("/api/jobs", json=dict(genre="Fantasy", provider=prov, model=mid, confirmed=True))
            assert j.status_code == 400 and j.json()["detail"]["code"] == "model_unavailable"
    assert hits == []


def test_api_exposes_the_contract_fields_and_keeps_gemini_intact(tmp_path):
    s = Settings(data_root=tmp_path / "d", artifact_root=tmp_path / "d" / "a", gemini_api_key="k")
    with TestClient(create_app(s)) as c:
        data = c.get("/api/models").json()
        by = {(m["provider_id"], m["model_id"]): m for m in data["models"]}
        assert len(by) == 17  # + FLUX.1 Schnell
        z = by[("local", "z-image-turbo")]
        for key in ("runtime", "runtime_label", "runtime_requirements", "options", "price_note", "capabilities", "label", "model_id", "status", "selectable", "disabled_reason"):
            assert key in z
        assert by[("cloudflare", "bytedance/seedream-4.5")]["price_note"]
        assert data["defaults"]["model"] == "gemini-nano-banana-2.1"
        assert by[("gemini", "gemini-nano-banana-2.1")]["selectable"] is True
        r = c.post("/api/generate-card", json=dict(genre="Fantasy", object_name="Wand", category=1, provider="mock", model="mock-placeholder-v1"))
        assert r.status_code == 200  # mock path unaffected
